"""
NHCX Insurance Plan FHIR Bundle Converter
==========================================
Open-source microservice: PDF → NHCX-compliant FHIR InsurancePlan Bundle
Built for NHCX Hackathon 2026 — Problem Statement 03 (ABDM)

Pipeline: PDF Upload → Text Extraction → LLM Structured Extraction →
          FHIR Mapping → NRCeS Validation → Human Review → Bundle Output
"""

import os
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("nhcx-converter")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NHCX Insurance Plan FHIR Converter v%s", settings.APP_VERSION)
    for d in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.REVIEW_DIR]:
        os.makedirs(d, exist_ok=True)
    yield
    logger.info("Shutting down NHCX Converter")


app = FastAPI(
    title="NHCX Insurance Plan → FHIR Bundle Converter",
    description=(
        "Open-source microservice that converts Indian health insurance plan PDFs "
        "into NHCX-compliant FHIR R4 InsurancePlan bundles. "
        "Built for NHCX Hackathon 2026 (PS-03) under ABDM."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["System"])
async def health_check():
    return {
        "status": "healthy",
        "service": "nhcx-insurance-plan-converter",
        "version": settings.APP_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
    }
