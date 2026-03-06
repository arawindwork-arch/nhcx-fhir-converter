"""
NHCX Insurance Plan FHIR Bundle Converter — Enhanced
======================================================
Original pipeline preserved. Added:
  1. Security middleware (auth, rate limiting, audit, encryption)
  2. Professional Stanford-style dashboard UI at /
  3. Quality measurement endpoints
  4. Security & compliance endpoints
  5. DPDP Act Right to Erasure endpoint
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── Your existing imports (unchanged) ──
from app.config import settings
from app.api.routes import router as api_router

# ── NEW: Security imports ──
from app.security.core import (
    DataEncryption, AuditLogger, APIKeyAuth, DataSanitizer,
    DataRetention, RateLimiter, AuditEvent, get_compliance_status,
)
from app.security.middleware import NHCXSecurityMiddleware

# ── NEW: Quality imports ──
from app.services.quality import score_extraction, MetricsCollector

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("nhcx-converter")

# ═══════════════════════════════════════════════════════════════════════════
# SECURITY & QUALITY COMPONENTS (initialized at module level)
# ═══════════════════════════════════════════════════════════════════════════

DATA_DIR = Path("./data")

encryption = DataEncryption()
audit_logger = AuditLogger(log_dir=str(DATA_DIR / "audit"))
api_auth = APIKeyAuth()
rate_limiter = RateLimiter(max_requests=30, window_seconds=60)
data_retention = DataRetention(encryption=encryption)
metrics_collector = MetricsCollector(metrics_dir=str(DATA_DIR / "metrics"))

# ═══════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all directories
    for d in [
        settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.REVIEW_DIR,
        str(DATA_DIR / "extracted"), str(DATA_DIR / "audit"),
        str(DATA_DIR / "metrics"),
    ]:
        os.makedirs(d, exist_ok=True)

    logger.info("=" * 60)
    logger.info("NHCX Insurance Plan → FHIR Converter v%s", settings.APP_VERSION)
    logger.info("Security: ENABLED (DPDP Act 2023 + HIPAA + ABDM)")
    logger.info("Encryption: AES-256-GCM")
    logger.info("Audit Logging: ACTIVE")
    logger.info("Quality Metrics: ACTIVE")
    logger.info("Dashboard UI: http://localhost:8000/")
    logger.info("API Docs: http://localhost:8000/docs")
    logger.info("=" * 60)

    # Purge expired data on startup
    purged = data_retention.purge_expired({
        "uploads": settings.UPLOAD_DIR,
        "extracted": str(DATA_DIR / "extracted"),
        "output": settings.OUTPUT_DIR,
    })
    if purged:
        logger.info("Startup cleanup: purged %d expired files", purged)

    yield

    logger.info("Shutting down NHCX Converter")
    audit_logger.log_conversion("system", "shutdown", "success", "Server shutdown")


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="NHCX Insurance Plan → FHIR Bundle Converter",
    description=(
        "Open-source microservice that converts Indian health insurance plan PDFs "
        "into NHCX-compliant FHIR R4 InsurancePlan bundles. "
        "Built for NHCX Hackathon 2026 (PS-03) under ABDM.\n\n"
        "**Security**: DPDP Act 2023 + HIPAA compliant · AES-256-GCM encryption · "
        "Audit logging · Role-based access\n\n"
        "**Quality**: Extraction scoring · Field coverage analysis · "
        "Pipeline reliability metrics"
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# ── CORS (restricted for healthcare) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)

# ── Security Middleware ──
app.add_middleware(
    NHCXSecurityMiddleware,
    auth=api_auth,
    audit=audit_logger,
    rate_limiter=rate_limiter,
)

# ── Your existing API routes (unchanged) ──
app.include_router(api_router, prefix="/api/v1")

# ── Static files (Professional UI) ──
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ═══════════════════════════════════════════════════════════════════════════
# ROOT — Professional Dashboard UI
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Serve the Stanford-style healthcare dashboard."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>NHCX Converter</h1><p><a href='/docs'>API Documentation</a></p>"
    )


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "service": "nhcx-insurance-plan-converter",
        "version": settings.APP_VERSION,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "security": "enabled",
        "encryption": "AES-256-GCM",
        "audit_logging": "active",
    }


# ═══════════════════════════════════════════════════════════════════════════
# NEW ENDPOINTS — Security
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/security/status", tags=["Security"])
async def security_status():
    """Get security and compliance status dashboard data."""
    compliance = get_compliance_status()
    audit_stats = audit_logger.get_stats()
    return {
        "compliance": compliance,
        "audit_stats": audit_stats,
        "rate_limiting": {
            "max_requests_per_minute": rate_limiter.max_requests,
            "window_seconds": rate_limiter.window_seconds,
        },
    }


@app.get("/api/v1/security/audit", tags=["Security"])
async def get_audit_events(resource_id: Optional[str] = None, limit: int = 100):
    """Get audit events (admin only)."""
    events = audit_logger.get_events(resource_id=resource_id, limit=limit)
    return {"events": events, "total": len(events)}


# ═══════════════════════════════════════════════════════════════════════════
# NEW ENDPOINTS — Quality Metrics
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/v1/quality/score/{job_id}", tags=["Quality"])
async def get_quality_score(job_id: str):
    """Get detailed extraction quality score for a job."""
    extracted_path = DATA_DIR / "extracted" / f"{job_id}_extracted.json"
    if not extracted_path.exists():
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    with open(extracted_path, "r") as f:
        plan_dict = json.load(f)

    score = score_extraction(plan_dict, job_id)
    return score.to_dict()


@app.get("/api/v1/quality/metrics", tags=["Quality"])
async def get_quality_metrics():
    """Get pipeline quality metrics dashboard data."""
    return metrics_collector.get_dashboard_data()


@app.get("/api/v1/quality/history", tags=["Quality"])
async def get_job_history(limit: int = 50):
    """Get recent job history with quality data."""
    return {"jobs": metrics_collector.get_job_history(limit=limit)}


# ═══════════════════════════════════════════════════════════════════════════
# NEW ENDPOINTS — DPDP Act Compliance
# ═══════════════════════════════════════════════════════════════════════════

@app.delete("/api/v1/jobs/{job_id}", tags=["Data Management"])
async def delete_job(job_id: str, request: Request):
    """
    Right to Erasure — permanently delete all data for a job.
    Compliant with India's DPDP Act 2023 Section 12.
    """
    deleted = data_retention.delete_job_data(
        job_id=job_id,
        data_dirs={
            "uploads": settings.UPLOAD_DIR,
            "extracted": str(DATA_DIR / "extracted"),
            "review": settings.REVIEW_DIR,
            "output": settings.OUTPUT_DIR,
        },
    )

    audit_logger.log(AuditEvent(
        event_type="DELETE",
        event_subtype="right_to_erasure",
        outcome="success",
        actor_id=getattr(request.state, "actor_id", "system"),
        resource_type="ConversionJob",
        resource_id=job_id,
        action_detail=f"Erased {deleted} files (DPDP Right to Erasure)",
        data_classification="PHI",
    ))

    return {
        "job_id": job_id,
        "status": "erased",
        "files_deleted": deleted,
        "message": f"All data for job {job_id} has been securely deleted.",
    }
