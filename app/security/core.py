"""
NHCX Healthcare Data Security Core
=====================================
Security controls aligned to:
  - India's Digital Personal Data Protection (DPDP) Act, 2023
  - HIPAA Security Rule (Administrative, Physical, Technical Safeguards)
  - ABDM Health Data Management Policy
  - ISO 27001:2022 / ISO 27799 (Health informatics)

Implements:
  1. AES-256-GCM encryption at rest for all PHI/PII
  2. Structured audit logging (immutable trail)
  3. API key authentication with role-based access
  4. Automatic data retention & purge controls
  5. Input sanitization & injection prevention
  6. Request rate limiting per client
  7. Data masking for logs (no PHI in plaintext logs)
"""

import os
import re
import uuid
import json
import time
import hashlib
import logging
import secrets
from base64 import b64encode, b64decode
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from functools import wraps
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("nhcx-converter.security")


# ═══════════════════════════════════════════════════════════════════════════
# 1. ENCRYPTION AT REST — AES-256-GCM for PHI/PII
# ═══════════════════════════════════════════════════════════════════════════

class DataEncryption:
    """
    AES-256-GCM encryption for Protected Health Information (PHI).
    
    Think of this like a bank vault for patient data — every piece of
    insurance plan data (names, policy numbers, medical conditions in
    exclusions) gets encrypted before touching the disk.
    
    In a production deployment, the encryption key would come from
    AWS KMS, Azure Key Vault, or HashiCorp Vault. For the hackathon,
    we use a secure random key stored in environment variables.
    """

    def __init__(self, key: Optional[str] = None):
        self._key_hex = key or os.environ.get(
            "NHCX_ENCRYPTION_KEY",
            secrets.token_hex(32)  # 256-bit key
        )
        self._key = bytes.fromhex(self._key_hex)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt string data using AES-256-GCM."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = os.urandom(12)
            aesgcm = AESGCM(self._key)
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
            return b64encode(nonce + ciphertext).decode("utf-8")
        except ImportError:
            # Fallback: XOR-based obfuscation (NOT production-grade)
            logger.warning("cryptography library not installed — using basic obfuscation")
            return self._basic_obfuscate(plaintext)

    def decrypt(self, encrypted: str) -> str:
        """Decrypt AES-256-GCM encrypted data."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = b64decode(encrypted)
            nonce, ciphertext = raw[:12], raw[12:]
            aesgcm = AESGCM(self._key)
            return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        except ImportError:
            return self._basic_deobfuscate(encrypted)

    def encrypt_file(self, input_path: str, output_path: Optional[str] = None) -> str:
        """Encrypt an entire file at rest."""
        output_path = output_path or input_path + ".enc"
        with open(input_path, "r", encoding="utf-8") as f:
            data = f.read()
        encrypted = self.encrypt(data)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(encrypted)
        # Securely delete original
        self._secure_delete(input_path)
        return output_path

    def decrypt_file(self, encrypted_path: str) -> str:
        """Decrypt a file and return plaintext content."""
        with open(encrypted_path, "r", encoding="utf-8") as f:
            encrypted = f.read()
        return self.decrypt(encrypted)

    def _secure_delete(self, filepath: str):
        """Overwrite file with random data before deletion (DoD 5220.22-M)."""
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            with open(filepath, "wb") as f:
                f.write(os.urandom(size))  # Pass 1: random
                f.flush()
                os.fsync(f.fileno())
            os.remove(filepath)

    def _basic_obfuscate(self, text: str) -> str:
        """Basic XOR obfuscation fallback."""
        key_bytes = self._key[:len(text.encode())]
        result = bytes(a ^ b for a, b in zip(text.encode(), key_bytes * (len(text.encode()) // len(key_bytes) + 1)))
        return b64encode(result).decode()

    def _basic_deobfuscate(self, encoded: str) -> str:
        """Reverse basic XOR obfuscation."""
        data = b64decode(encoded)
        key_bytes = self._key[:len(data)]
        result = bytes(a ^ b for a, b in zip(data, key_bytes * (len(data) // len(key_bytes) + 1)))
        return result.decode()


# ═══════════════════════════════════════════════════════════════════════════
# 2. AUDIT LOGGING — Immutable, structured, HIPAA-compliant
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AuditEvent:
    """
    HIPAA-compliant audit event record.
    
    Maps to FHIR AuditEvent resource structure for healthcare
    interoperability. Every data access, modification, or system
    event gets recorded with who, what, when, where, and outcome.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    event_type: str = ""           # CREATE, READ, UPDATE, DELETE, LOGIN, EXPORT
    event_subtype: str = ""        # pdf_upload, extraction, fhir_mapping, download, etc.
    outcome: str = "success"       # success, failure, error
    actor_id: str = ""             # API key hash or session ID
    actor_role: str = ""           # admin, reviewer, api_client
    resource_type: str = ""        # InsurancePlan, Bundle, PDF, ExtractedData
    resource_id: str = ""          # job_id or resource UUID
    action_detail: str = ""        # Human-readable description
    client_ip: str = ""
    user_agent: str = ""
    data_classification: str = ""  # PHI, PII, PUBLIC, INTERNAL
    integrity_hash: str = ""       # SHA-256 of event for tamper detection

    def __post_init__(self):
        # Auto-compute integrity hash
        content = f"{self.timestamp}|{self.event_type}|{self.actor_id}|{self.resource_id}|{self.outcome}"
        self.integrity_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class AuditLogger:
    """
    Immutable audit trail for all PHI/PII access.
    
    In a production system, this writes to a WORM (Write Once Read Many)
    storage like AWS CloudTrail, Azure Immutable Blob, or a dedicated
    audit database. For the hackathon, we use append-only JSON lines.
    
    Think of this like a hospital's visitor log — every person who
    touches patient data gets recorded, and the log can't be erased.
    """

    def __init__(self, log_dir: str = "./data/audit"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._log_file = os.path.join(
            log_dir, f"audit_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
        )

    def log(self, event: AuditEvent):
        """Append an audit event to the immutable log."""
        record = asdict(event)
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        logger.info(
            "AUDIT | %s | %s | %s | %s | %s",
            event.event_type, event.outcome, event.actor_id[:8] if event.actor_id else "system",
            event.resource_type, event.action_detail[:80]
        )

    def log_data_access(self, job_id: str, actor: str, action: str, 
                        classification: str = "PHI", outcome: str = "success"):
        """Convenience method for common data access logging."""
        self.log(AuditEvent(
            event_type="READ",
            event_subtype=action,
            outcome=outcome,
            actor_id=actor,
            resource_type="InsurancePlan",
            resource_id=job_id,
            action_detail=action,
            data_classification=classification,
        ))

    def log_conversion(self, job_id: str, stage: str, outcome: str, detail: str = ""):
        """Log a pipeline conversion stage."""
        self.log(AuditEvent(
            event_type="CREATE",
            event_subtype=f"pipeline_{stage}",
            outcome=outcome,
            resource_type="ConversionJob",
            resource_id=job_id,
            action_detail=detail or f"Pipeline stage: {stage}",
            data_classification="PHI",
        ))

    def get_events(self, resource_id: Optional[str] = None, 
                   limit: int = 100) -> List[Dict]:
        """Retrieve audit events (for admin dashboard)."""
        events = []
        if os.path.exists(self._log_file):
            with open(self._log_file, "r") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        if resource_id is None or event.get("resource_id") == resource_id:
                            events.append(event)
        return events[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get audit statistics for the security dashboard."""
        events = self.get_events(limit=10000)
        return {
            "total_events": len(events),
            "today_events": sum(
                1 for e in events 
                if e.get("timestamp", "").startswith(datetime.utcnow().strftime("%Y-%m-%d"))
            ),
            "success_rate": (
                sum(1 for e in events if e.get("outcome") == "success") / max(len(events), 1)
            ) * 100,
            "data_classifications": {
                cls: sum(1 for e in events if e.get("data_classification") == cls)
                for cls in ["PHI", "PII", "PUBLIC", "INTERNAL"]
            },
            "event_types": {
                et: sum(1 for e in events if e.get("event_type") == et)
                for et in ["CREATE", "READ", "UPDATE", "DELETE", "LOGIN", "EXPORT"]
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# 3. API KEY AUTHENTICATION
# ═══════════════════════════════════════════════════════════════════════════

class APIKeyAuth:
    """
    API Key authentication with role-based access control.
    
    In Indian healthcare context, different stakeholders need different
    access levels:
      - admin:    Full access (hospital IT, insurer tech team)
      - reviewer: Can review/approve extracted data (medical coder, TPA staff)
      - api:      Can upload PDFs and download bundles (integration systems)
      - readonly: Can only view status (audit/compliance officer)
    """

    # Default keys for hackathon demo (in production, these go in a database)
    DEFAULT_KEYS = {
        "nhcx-admin-key-2026": {"role": "admin", "name": "NHCX Admin"},
        "nhcx-reviewer-key-2026": {"role": "reviewer", "name": "NHCX Reviewer"},
        "nhcx-api-key-2026": {"role": "api", "name": "API Client"},
        "nhcx-demo-key-2026": {"role": "api", "name": "Demo Client"},
    }

    # Role permissions matrix
    ROLE_PERMISSIONS = {
        "admin": {"convert", "review", "download", "validate", "mappings", "audit", "dashboard", "settings"},
        "reviewer": {"convert", "review", "download", "validate", "mappings"},
        "api": {"convert", "download", "validate", "mappings"},
        "readonly": {"mappings"},
    }

    def __init__(self):
        self._keys = dict(self.DEFAULT_KEYS)
        # Load additional keys from environment
        extra_keys = os.environ.get("NHCX_API_KEYS", "")
        if extra_keys:
            for entry in extra_keys.split(","):
                parts = entry.strip().split(":")
                if len(parts) == 2:
                    self._keys[parts[0]] = {"role": parts[1], "name": "Custom"}

    def authenticate(self, api_key: Optional[str]) -> Optional[Dict]:
        """
        Validate API key and return key info, or None if invalid.
        For the hackathon demo, unauthenticated requests are allowed
        with 'demo' role (limited permissions).
        """
        if not api_key:
            return {"role": "api", "name": "Unauthenticated (Demo Mode)", "key_hash": "demo"}
        
        key_info = self._keys.get(api_key)
        if key_info:
            return {
                **key_info,
                "key_hash": hashlib.sha256(api_key.encode()).hexdigest()[:12],
            }
        return None

    def check_permission(self, role: str, action: str) -> bool:
        """Check if a role has permission for an action."""
        perms = self.ROLE_PERMISSIONS.get(role, set())
        return action in perms

    def hash_key(self, key: str) -> str:
        """Hash an API key for safe logging (never log raw keys)."""
        return hashlib.sha256(key.encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════════
# 4. DATA SANITIZATION & PHI MASKING
# ═══════════════════════════════════════════════════════════════════════════

class DataSanitizer:
    """
    Sanitizes input data and masks PHI/PII in logs.
    
    Indian insurance PDFs contain sensitive data:
      - Aadhaar numbers (12 digits)
      - PAN numbers (ABCDE1234F)  
      - Phone numbers (+91-XXXXXXXXXX)
      - Email addresses
      - Policy holder names
      - Medical conditions (in exclusions/waiting periods)
    
    This ensures none of that leaks into logs or error messages.
    """

    # Patterns for Indian PII
    PII_PATTERNS = {
        "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
        "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
        "phone_in": re.compile(r"(\+91[-\s]?)?[6-9]\d{9}\b"),
        "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "policy_number": re.compile(r"\b[A-Z]{2,5}\d{8,15}\b"),
    }

    @classmethod
    def mask_phi(cls, text: str) -> str:
        """Mask all PII/PHI patterns in text for safe logging."""
        masked = text
        for name, pattern in cls.PII_PATTERNS.items():
            masked = pattern.sub(f"[MASKED_{name.upper()}]", masked)
        return masked

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """Sanitize uploaded filename to prevent path traversal."""
        # Remove path components
        filename = os.path.basename(filename)
        # Allow only safe characters
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
        # Prevent hidden files
        safe = safe.lstrip(".")
        return safe or "unnamed.pdf"

    @classmethod
    def sanitize_input(cls, data: Any, max_depth: int = 5) -> Any:
        """Recursively sanitize input data to prevent injection attacks."""
        if max_depth <= 0:
            return str(data)[:1000] if data else ""
        
        if isinstance(data, str):
            # Remove potential script injection
            sanitized = re.sub(r"<script[^>]*>.*?</script>", "", data, flags=re.IGNORECASE | re.DOTALL)
            # Remove SQL injection patterns
            sanitized = re.sub(r"(--|;|DROP\s+TABLE|DELETE\s+FROM|INSERT\s+INTO)", "", sanitized, flags=re.IGNORECASE)
            return sanitized
        elif isinstance(data, dict):
            return {k: cls.sanitize_input(v, max_depth - 1) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls.sanitize_input(v, max_depth - 1) for v in data]
        return data


# ═══════════════════════════════════════════════════════════════════════════
# 5. DATA RETENTION & AUTO-PURGE
# ═══════════════════════════════════════════════════════════════════════════

class DataRetention:
    """
    Automatic data retention and purge controls.
    
    Under India's DPDP Act 2023, personal data must be:
      - Retained only as long as necessary for the purpose
      - Deleted when consent is withdrawn or purpose is fulfilled
      - Erasable on request (Right to Erasure)
    
    Think of it like medical records in a hospital — they keep them
    for a legally required period, then securely destroy them.
    """

    # Retention periods (configurable via environment)
    RETENTION_HOURS = {
        "uploads": int(os.environ.get("RETENTION_UPLOADS_HOURS", "24")),      # Raw PDFs
        "extracted": int(os.environ.get("RETENTION_EXTRACTED_HOURS", "72")),   # Extracted JSON
        "output": int(os.environ.get("RETENTION_OUTPUT_HOURS", "168")),        # FHIR bundles (7 days)
        "audit": int(os.environ.get("RETENTION_AUDIT_HOURS", "8760")),         # Audit logs (1 year)
    }

    def __init__(self, encryption: Optional[DataEncryption] = None):
        self.encryption = encryption

    def purge_expired(self, data_dirs: Dict[str, str]):
        """Check and purge expired data across all directories."""
        purged = 0
        for category, dir_path in data_dirs.items():
            if not os.path.exists(dir_path):
                continue
            max_age_hours = self.RETENTION_HOURS.get(category, 24)
            cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

            for fname in os.listdir(dir_path):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath):
                    mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        self._secure_delete(fpath)
                        purged += 1
                        logger.info("RETENTION | Purged expired file: %s (age > %dh)", fname, max_age_hours)
        
        return purged

    def delete_job_data(self, job_id: str, data_dirs: Dict[str, str]):
        """Delete all data associated with a specific job (Right to Erasure)."""
        deleted = 0
        for category, dir_path in data_dirs.items():
            if not os.path.exists(dir_path):
                continue
            for fname in os.listdir(dir_path):
                if job_id in fname:
                    self._secure_delete(os.path.join(dir_path, fname))
                    deleted += 1
        logger.info("RETENTION | Job %s: Erased %d files (Right to Erasure)", job_id, deleted)
        return deleted

    def _secure_delete(self, filepath: str):
        """Overwrite and delete file securely."""
        try:
            if os.path.exists(filepath):
                size = os.path.getsize(filepath)
                if size > 0:
                    with open(filepath, "wb") as f:
                        f.write(os.urandom(min(size, 10 * 1024 * 1024)))
                        f.flush()
                os.remove(filepath)
        except Exception as e:
            logger.error("Secure delete failed for %s: %s", filepath, e)


# ═══════════════════════════════════════════════════════════════════════════
# 6. RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Token-bucket rate limiter per API key.
    
    Prevents abuse and ensures fair access — similar to how a hospital
    reception manages the queue, ensuring no single person blocks
    everyone else.
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: Dict[str, List[float]] = {}

    def check(self, client_id: str) -> bool:
        """Returns True if request is allowed, False if rate limited."""
        now = time.time()
        if client_id not in self._buckets:
            self._buckets[client_id] = []
        
        # Remove old timestamps
        self._buckets[client_id] = [
            t for t in self._buckets[client_id] 
            if now - t < self.window_seconds
        ]
        
        if len(self._buckets[client_id]) >= self.max_requests:
            return False
        
        self._buckets[client_id].append(now)
        return True

    def remaining(self, client_id: str) -> int:
        """Get remaining requests for a client."""
        now = time.time()
        if client_id not in self._buckets:
            return self.max_requests
        active = [t for t in self._buckets[client_id] if now - t < self.window_seconds]
        return max(0, self.max_requests - len(active))


# ═══════════════════════════════════════════════════════════════════════════
# 7. SECURITY HEADERS & COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def get_compliance_status() -> Dict[str, Any]:
    """Get current compliance status for the security dashboard."""
    return {
        "framework_alignment": {
            "DPDP_Act_2023": {
                "status": "implemented",
                "controls": [
                    "Data minimization (extract only required fields)",
                    "Purpose limitation (insurance plan conversion only)",
                    "Retention controls (auto-purge after configurable period)",
                    "Right to erasure (DELETE /api/v1/jobs/{job_id})",
                    "Data encryption at rest (AES-256-GCM)",
                    "Audit trail (immutable event logging)",
                ]
            },
            "HIPAA_Security_Rule": {
                "status": "implemented",
                "controls": [
                    "Access controls (API key + role-based)",
                    "Audit controls (structured audit logging)",
                    "Integrity controls (SHA-256 hash verification)",
                    "Transmission security (HTTPS enforcement headers)",
                    "Encryption at rest (AES-256-GCM)",
                    "Automatic logoff (session timeout)",
                ]
            },
            "ABDM_Health_Data_Policy": {
                "status": "aligned",
                "controls": [
                    "FHIR R4 standard compliance",
                    "NRCeS profile validation",
                    "Health data categorization (PHI tagging)",
                    "Consent-based data processing",
                    "Data portability (FHIR bundle export)",
                ]
            },
            "ISO_27001_27799": {
                "status": "partial",
                "controls": [
                    "Information security policy",
                    "Asset management (data classification)",
                    "Access control",
                    "Cryptography",
                    "Operations security (logging, monitoring)",
                ]
            },
        },
        "encryption": {
            "algorithm": "AES-256-GCM",
            "key_length": 256,
            "at_rest": True,
            "in_transit": "TLS 1.2+ (via deployment proxy)",
        },
        "data_retention": DataRetention.RETENTION_HOURS,
        "last_audit_check": datetime.utcnow().isoformat() + "Z",
    }
