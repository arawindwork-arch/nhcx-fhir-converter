"""Application settings loaded from environment variables."""

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── File paths ───────────────────────────────────────────────────────
    UPLOAD_DIR: str = "./data/uploads"
    OUTPUT_DIR: str = "./data/output"
    REVIEW_DIR: str = "./data/review"
    MAPPINGS_DIR: str = "./app/mappings"

    # ── LLM ──────────────────────────────────────────────────────────────
    LLM_PROVIDER: str = "openai"          # openai | anthropic | google | ollama
    LLM_MODEL: str = "gpt-4o"
    LLM_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 8192
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    # ── FHIR / NHCX ─────────────────────────────────────────────────────
    FHIR_BASE_URL: str = "https://nrces.in/ndhm/fhir/r4"
    FHIR_IG_VERSION: str = "6.5.0"
    NHCX_PROFILE_URL: str = (
        "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan"
    )
    INSURANCE_PLAN_BUNDLE_PROFILE: str = (
        "https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle"
    )

    # ── Processing ───────────────────────────────────────────────────────
    MAX_PDF_SIZE_MB: int = 50
    CHUNK_SIZE_CHARS: int = 12000
    CHUNK_OVERLAP_CHARS: int = 1000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
