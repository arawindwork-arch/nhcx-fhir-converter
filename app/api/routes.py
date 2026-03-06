"""
API Routes for the NHCX Insurance Plan Converter.
Endpoints:
  POST /convert          — Upload PDF, get FHIR InsurancePlan bundle
  GET  /jobs/{job_id}    — Check conversion status
  POST /review/{job_id}  — Submit human review corrections
  POST /validate         — Validate an existing FHIR bundle
  GET  /mappings         — List available mapping configurations
"""

import os
import uuid
import json
import logging
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.api.models import (
    ConversionResponse,
    ReviewUpdate,
    ValidationError as ValError,
)
from app.services.pdf_extractor import PDFExtractor
from app.services.llm_processor import LLMProcessor
from app.services.fhir_mapper import FHIRMapper
from app.services.validator import NHCXValidator
from app.services.pipeline import ConversionPipeline

logger = logging.getLogger("nhcx-converter.api")
router = APIRouter(tags=["Conversion"])

# In-memory job store (replace with DB in production)
jobs: dict = {}

pipeline = ConversionPipeline()


# ─────────────────────────────────────────────────────────────────────────
# POST /convert — Main conversion endpoint
# ─────────────────────────────────────────────────────────────────────────
@router.post("/convert", response_model=ConversionResponse)
async def convert_pdf_to_fhir(
    file: UploadFile = File(..., description="Insurance plan PDF"),
    mapping_config: str = Form(None, description="Mapping config name"),
    insurer_name_override: str = Form(None),
    plan_type_override: str = Form(None),
    auto_validate: bool = Form(True),
    skip_review: bool = Form(False),
):
    """
    Upload an insurance plan PDF and convert it to an NHCX-compliant
    FHIR InsurancePlan bundle.

    The pipeline:
    1. Extract text from PDF (pdfplumber + OCR fallback)
    2. Use LLM to extract structured insurance plan data
    3. Map extracted data → FHIR R4 InsurancePlan resource
    4. Wrap in InsurancePlanBundle (NRCeS profile)
    5. Validate against NRCeS NHCX profiles
    6. Return bundle (or queue for human review)
    """
    # Validate file
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.MAX_PDF_SIZE_MB:
        raise HTTPException(413, f"PDF exceeds {settings.MAX_PDF_SIZE_MB}MB limit")

    # Create job
    job_id = str(uuid.uuid4())[:12]
    pdf_path = os.path.join(settings.UPLOAD_DIR, f"{job_id}_{file.filename}")
    with open(pdf_path, "wb") as f:
        f.write(content)

    logger.info("Job %s: Received PDF '%s' (%.1f MB)", job_id, file.filename, size_mb)

    try:
        result = await pipeline.run(
            job_id=job_id,
            pdf_path=pdf_path,
            source_filename=file.filename,
            mapping_config=mapping_config,
            insurer_name_override=insurer_name_override,
            plan_type_override=plan_type_override,
            auto_validate=auto_validate,
            skip_review=skip_review,
        )

        jobs[job_id] = result
        return result

    except Exception as e:
        logger.exception("Job %s: Conversion failed", job_id)
        error_response = ConversionResponse(
            job_id=job_id,
            status="failed",
            message=f"Conversion failed: {str(e)}",
        )
        jobs[job_id] = error_response
        raise HTTPException(500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────
# GET /jobs/{job_id} — Check job status
# ─────────────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}", response_model=ConversionResponse)
async def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return jobs[job_id]


# ─────────────────────────────────────────────────────────────────────────
# POST /review/{job_id} — Submit human review
# ─────────────────────────────────────────────────────────────────────────
@router.post("/review/{job_id}", response_model=ConversionResponse)
async def submit_review(job_id: str, review: ReviewUpdate):
    """
    Submit corrected extracted data after human review.
    Re-runs FHIR mapping and validation with the corrected data.
    """
    if job_id not in jobs:
        raise HTTPException(404, f"Job {job_id} not found")

    logger.info("Job %s: Human review submitted (approved=%s)", job_id, review.approved)

    try:
        mapper = FHIRMapper()
        validator = NHCXValidator()

        fhir_bundle = mapper.map_to_insurance_plan_bundle(review.extracted_plan)
        validation_errors = validator.validate(fhir_bundle)

        # Save output
        output_path = os.path.join(
            settings.OUTPUT_DIR, f"{job_id}_InsurancePlan_Bundle.json"
        )
        with open(output_path, "w") as f:
            json.dump(fhir_bundle, f, indent=2)

        result = ConversionResponse(
            job_id=job_id,
            status="completed",
            message="Review applied, FHIR bundle generated successfully",
            extracted_plan=review.extracted_plan,
            fhir_bundle=fhir_bundle,
            validation_errors=[
                ValError(**e) for e in validation_errors
            ],
            output_file=output_path,
        )
        jobs[job_id] = result
        return result

    except Exception as e:
        logger.exception("Job %s: Review processing failed", job_id)
        raise HTTPException(500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────
# POST /validate — Validate existing FHIR bundle
# ─────────────────────────────────────────────────────────────────────────
@router.post("/validate")
async def validate_bundle(bundle: dict):
    """Validate a FHIR InsurancePlan bundle against NRCeS NHCX profiles."""
    validator = NHCXValidator()
    errors = validator.validate(bundle)
    return {
        "valid": all(e["severity"] != "error" for e in errors),
        "error_count": sum(1 for e in errors if e["severity"] == "error"),
        "warning_count": sum(1 for e in errors if e["severity"] == "warning"),
        "issues": errors,
    }


# ─────────────────────────────────────────────────────────────────────────
# GET /download/{job_id} — Download output bundle
# ─────────────────────────────────────────────────────────────────────────
@router.get("/download/{job_id}")
async def download_bundle(job_id: str):
    output_path = os.path.join(
        settings.OUTPUT_DIR, f"{job_id}_InsurancePlan_Bundle.json"
    )
    if not os.path.exists(output_path):
        raise HTTPException(404, "Output bundle not found")
    return FileResponse(
        output_path,
        media_type="application/json",
        filename=f"{job_id}_InsurancePlan_Bundle.json",
    )


# ─────────────────────────────────────────────────────────────────────────
# GET /mappings — List available mapping configs
# ─────────────────────────────────────────────────────────────────────────
@router.get("/mappings")
async def list_mappings():
    """List available insurer-specific mapping configurations."""
    mapping_dir = settings.MAPPINGS_DIR
    configs = []
    if os.path.exists(mapping_dir):
        for fname in os.listdir(mapping_dir):
            if fname.endswith((".yaml", ".yml", ".json")):
                configs.append(fname)
    return {"available_mappings": configs}
