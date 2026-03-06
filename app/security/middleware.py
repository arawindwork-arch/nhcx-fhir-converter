"""
FastAPI Security Middleware
============================
Intercepts all HTTP requests/responses to enforce:
  1. API key authentication (X-API-Key header)
  2. Rate limiting per client
  3. Security headers on all responses
  4. Automatic audit logging of PHI access
  5. Request/response sanitization
  6. CORS hardening for healthcare deployments
"""

import time
import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from app.security.core import (
    APIKeyAuth,
    AuditLogger,
    AuditEvent,
    RateLimiter,
    DataSanitizer,
    SECURITY_HEADERS,
)

logger = logging.getLogger("nhcx-converter.security.middleware")


class NHCXSecurityMiddleware(BaseHTTPMiddleware):
    """
    Healthcare-grade security middleware for all API requests.
    
    Every request passes through these checks in order:
      1. Rate limit check (prevent abuse)
      2. API key validation (who is this?)
      3. Permission check (are they allowed to do this?)
      4. Request logging (audit trail)
      5. Response security headers (browser hardening)
      6. Response audit (what happened?)
    """

    # Paths that don't require authentication
    PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/", "/favicon.ico"}
    # Static file prefixes
    STATIC_PREFIXES = ("/static/", "/api/v1/ui", "/docs", "/redoc", "/openapi")

    def __init__(self, app, auth: Optional[APIKeyAuth] = None,
                 audit: Optional[AuditLogger] = None,
                 rate_limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.auth = auth or APIKeyAuth()
        self.audit = audit or AuditLogger()
        self.rate_limiter = rate_limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        # ── Skip security for public/static paths ──
        if path in self.PUBLIC_PATHS or any(path.startswith(p) for p in self.STATIC_PREFIXES):
            response = await call_next(request)
            self._add_security_headers(response)
            return response

        # ── 1. Rate limiting ──
        api_key = request.headers.get("X-API-Key", "")
        client_id = api_key[:12] if api_key else client_ip
        
        if not self.rate_limiter.check(client_id):
            self.audit.log(AuditEvent(
                event_type="READ",
                event_subtype="rate_limited",
                outcome="failure",
                actor_id=client_id,
                client_ip=client_ip,
                action_detail=f"Rate limited: {request.method} {path}",
            ))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before retrying.",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )

        # ── 2. Authentication ──
        auth_result = self.auth.authenticate(api_key or None)
        if auth_result is None:
            self.audit.log(AuditEvent(
                event_type="LOGIN",
                event_subtype="auth_failed",
                outcome="failure",
                actor_id=DataSanitizer.mask_phi(api_key[:8]) if api_key else "none",
                client_ip=client_ip,
                action_detail=f"Invalid API key for: {request.method} {path}",
            ))
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Invalid API key. Provide a valid key via X-API-Key header.",
                    "documentation": "/docs",
                },
            )

        role = auth_result["role"]
        actor_id = auth_result.get("key_hash", "demo")

        # ── 3. Permission check ──
        action = self._path_to_action(path, request.method)
        if action and not self.auth.check_permission(role, action):
            self.audit.log(AuditEvent(
                event_type="READ",
                event_subtype="permission_denied",
                outcome="failure",
                actor_id=actor_id,
                actor_role=role,
                client_ip=client_ip,
                action_detail=f"Permission denied: {role} cannot {action} ({request.method} {path})",
            ))
            return JSONResponse(
                status_code=403,
                content={"detail": f"Role '{role}' does not have permission for this action."},
            )

        # ── 4. Process request ──
        # Store auth info for route handlers
        request.state.auth = auth_result
        request.state.client_ip = client_ip
        request.state.actor_id = actor_id

        try:
            response = await call_next(request)
        except Exception as e:
            # Log error (with PHI masking)
            self.audit.log(AuditEvent(
                event_type="READ",
                event_subtype="server_error",
                outcome="error",
                actor_id=actor_id,
                actor_role=role,
                client_ip=client_ip,
                action_detail=f"Server error: {DataSanitizer.mask_phi(str(e)[:200])}",
            ))
            raise

        # ── 5. Add security headers ──
        self._add_security_headers(response)

        # ── 6. Audit log the response ──
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Only log data-access endpoints (not static/health)
        if path.startswith("/api/"):
            self.audit.log(AuditEvent(
                event_type=self._method_to_event_type(request.method),
                event_subtype=action or "api_call",
                outcome="success" if response.status_code < 400 else "failure",
                actor_id=actor_id,
                actor_role=role,
                resource_type=self._path_to_resource(path),
                resource_id=self._extract_job_id(path),
                client_ip=client_ip,
                user_agent=request.headers.get("User-Agent", "")[:100],
                action_detail=f"{request.method} {path} → {response.status_code} ({duration_ms}ms)",
                data_classification="PHI" if self._is_phi_endpoint(path) else "INTERNAL",
            ))

        # Add rate limit headers
        remaining = self.rate_limiter.remaining(client_id)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limiter.max_requests)
        response.headers["X-Request-Duration-Ms"] = str(duration_ms)

        return response

    def _add_security_headers(self, response: Response):
        """Add security headers to response."""
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value

    def _path_to_action(self, path: str, method: str) -> Optional[str]:
        """Map URL path to permission action."""
        if "/convert" in path and method == "POST":
            return "convert"
        elif "/review/" in path and method == "POST":
            return "review"
        elif "/download/" in path:
            return "download"
        elif "/validate" in path:
            return "validate"
        elif "/mappings" in path:
            return "mappings"
        elif "/audit" in path:
            return "audit"
        elif "/dashboard" in path or "/security" in path:
            return "dashboard"
        return None

    def _path_to_resource(self, path: str) -> str:
        """Determine resource type from path."""
        if "/convert" in path:
            return "ConversionJob"
        elif "/review" in path:
            return "ReviewAction"
        elif "/download" in path:
            return "FHIRBundle"
        elif "/validate" in path:
            return "Validation"
        return "APIEndpoint"

    def _extract_job_id(self, path: str) -> str:
        """Extract job_id from URL path."""
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part in ("jobs", "review", "download") and i + 1 < len(parts):
                return parts[i + 1]
        return ""

    def _method_to_event_type(self, method: str) -> str:
        """Map HTTP method to audit event type."""
        return {
            "GET": "READ",
            "POST": "CREATE",
            "PUT": "UPDATE",
            "PATCH": "UPDATE",
            "DELETE": "DELETE",
        }.get(method, "READ")

    def _is_phi_endpoint(self, path: str) -> bool:
        """Check if endpoint handles PHI data."""
        phi_patterns = ["/convert", "/review/", "/download/", "/jobs/"]
        return any(p in path for p in phi_patterns)
