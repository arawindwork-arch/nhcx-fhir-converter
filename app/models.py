"""
Pydantic models for the NHCX Insurance Plan Converter.
Defines structured representations of extracted insurance plan data
that maps directly to FHIR R4 InsurancePlan resource (NRCeS v6.5.0).
"""

from __future__ import annotations
from typing import Optional, List, Any
from datetime import date
from enum import Enum
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Enums — aligned to NRCeS NHCX value sets
# ═══════════════════════════════════════════════════════════════════════════

class InsurancePlanType(str, Enum):
    """NRCeS ValueSet: ndhm-insuranceplan-type (from IIB manual)."""
    INDIVIDUAL = "individual"
    FAMILY_FLOATER = "family-floater"
    GROUP = "group"
    GOVERNMENT = "government"
    MICRO = "micro"
    CRITICAL_ILLNESS = "critical-illness"
    TOP_UP = "top-up"
    SUPER_TOP_UP = "super-top-up"
    PERSONAL_ACCIDENT = "personal-accident"
    OTHER = "other"


class CoverageType(str, Enum):
    """NRCeS ValueSet: ndhm-coverage-type."""
    INPATIENT = "inpatient"
    DAYCARE = "daycare"
    OUTPATIENT = "outpatient"
    MATERNITY = "maternity"
    DOMICILIARY = "domiciliary"
    ORGAN_DONOR = "organ-donor"
    AYUSH = "ayush"
    OTHER = "other"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# Extracted plan data structures
# All fields that LLMs might return as null are Optional to handle
# varying output quality from different models (GPT-4o vs local llama)
# ═══════════════════════════════════════════════════════════════════════════

class BenefitLimit(BaseModel):
    """A sub-limit or cap on a specific benefit."""
    description: Optional[str] = Field(None, description="What this limit applies to (e.g., 'Room Rent')")
    value: Optional[float] = Field(None, description="Monetary cap in INR")
    percentage: Optional[float] = Field(None, description="Percentage cap (e.g., co-pay 20%)")
    unit: Optional[str] = Field(None, description="Unit: 'INR', 'percent', 'days', etc.")
    condition: Optional[str] = Field(None, description="Condition for this limit")


class Benefit(BaseModel):
    """A single benefit covered under the plan."""
    name: Optional[str] = Field(None, description="Benefit name (e.g., 'In-Patient Hospitalization')")
    benefit_type: Optional[str] = Field(None, description="NHCX benefit type code")
    description: Optional[str] = Field(None, description="Detailed description")
    covered: Optional[bool] = Field(True, description="Whether this benefit is covered")
    limits: Optional[List[BenefitLimit]] = Field(default_factory=list)
    waiting_period_days: Optional[int] = Field(None, description="Waiting period in days")
    conditions: Optional[List[str]] = Field(default_factory=list, description="Claim conditions")
    supporting_docs: Optional[List[str]] = Field(default_factory=list, description="Required documents")


class Exclusion(BaseModel):
    """An exclusion from coverage."""
    name: Optional[str] = Field(None, description="Exclusion name")
    description: Optional[str] = None
    exclusion_type: Optional[str] = Field(
        None, description="'permanent', 'waiting-period', 'conditional'"
    )
    waiting_period_days: Optional[int] = None


class CoverageDetail(BaseModel):
    """Coverage details for a category (e.g., Inpatient, Daycare)."""
    coverage_type: Optional[str] = Field(None, description="Type: inpatient, daycare, outpatient, etc.")
    description: Optional[str] = None
    benefits: Optional[List[Benefit]] = Field(default_factory=list)
    conditions: Optional[List[str]] = Field(default_factory=list)
    supporting_info_requirements: Optional[List[str]] = Field(default_factory=list)


class PlanCost(BaseModel):
    """Cost information for a plan variant."""
    sum_insured: Optional[float] = Field(None, description="Sum insured in INR")
    premium: Optional[float] = Field(None, description="Annual premium in INR")
    copay_percentage: Optional[float] = None
    deductible: Optional[float] = None
    age_band: Optional[str] = Field(None, description="e.g., '18-35', '36-45'")
    plan_variant: Optional[str] = Field(None, description="e.g., 'Silver', 'Gold', 'Platinum'")


class ExtractedInsurancePlan(BaseModel):
    """
    Complete structured representation of an Indian health insurance plan
    extracted from PDF. This maps directly to FHIR InsurancePlan resource.
    All fields are Optional to gracefully handle varying LLM output quality.
    """
    # ── Identity ─────────────────────────────────────────────────────────
    plan_name: Optional[str] = Field(None, description="Official product name")
    plan_aliases: Optional[List[str]] = Field(default_factory=list, description="Alternate names")
    plan_identifier: Optional[str] = Field(None, description="UIN or product code")
    plan_type: Optional[str] = Field("individual", description="InsurancePlan type")
    status: Optional[str] = Field("active")

    # ── Insurer info ─────────────────────────────────────────────────────
    insurer_name: Optional[str] = Field(None, description="Insurance company name")
    insurer_irdai_registration: Optional[str] = Field(None, description="IRDAI registration number")
    tpa_name: Optional[str] = Field(None, description="TPA administering the plan")

    # ── Period ───────────────────────────────────────────────────────────
    effective_from: Optional[str] = Field(None, description="Plan effective date (YYYY-MM-DD)")
    effective_to: Optional[str] = Field(None, description="Plan end date (YYYY-MM-DD)")

    # ── Coverage Area ────────────────────────────────────────────────────
    coverage_areas: Optional[List[str]] = Field(
        default_factory=lambda: ["India"],
        description="Geographic coverage (e.g., India, Worldwide)"
    )

    # ── Coverage & Benefits ──────────────────────────────────────────────
    coverages: Optional[List[CoverageDetail]] = Field(default_factory=list)

    # ── Plan Costs ───────────────────────────────────────────────────────
    plan_costs: Optional[List[PlanCost]] = Field(default_factory=list)

    # ── Exclusions ───────────────────────────────────────────────────────
    exclusions: Optional[List[Exclusion]] = Field(default_factory=list)

    # ── Waiting Periods ──────────────────────────────────────────────────
    initial_waiting_period_days: Optional[int] = Field(None, description="General waiting period")
    pre_existing_disease_waiting_days: Optional[int] = Field(
        None, description="PED waiting period"
    )
    specific_disease_waiting_days: Optional[int] = Field(
        None, description="Specific disease waiting period"
    )

    # ── Contact ──────────────────────────────────────────────────────────
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    contact_website: Optional[str] = None

    # ── Network ──────────────────────────────────────────────────────────
    network_hospital_count: Optional[int] = None

    # ── Raw / metadata ───────────────────────────────────────────────────
    extraction_confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="LLM extraction confidence score"
    )
    source_pdf_filename: Optional[str] = None
    extraction_warnings: Optional[List[str]] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# API Request / Response models
# ═══════════════════════════════════════════════════════════════════════════

class ConversionRequest(BaseModel):
    """Optional configuration overrides for a conversion."""
    mapping_config: Optional[str] = Field(
        None, description="Name of mapping config file to use"
    )
    insurer_name_override: Optional[str] = None
    plan_type_override: Optional[str] = None
    auto_validate: bool = Field(True, description="Run NRCeS validation after mapping")
    skip_review: bool = Field(False, description="Skip human review step")


class ValidationError(BaseModel):
    severity: str = Field(..., description="error | warning | information")
    location: str = Field(..., description="FHIR path of the issue")
    message: str


class ConversionResponse(BaseModel):
    job_id: str
    status: str = Field(..., description="processing | review | completed | failed")
    message: str
    extracted_plan: Optional[ExtractedInsurancePlan] = None
    fhir_bundle: Optional[dict] = None
    validation_errors: Optional[List[Any]] = Field(default_factory=list)
    output_file: Optional[str] = None


class ReviewUpdate(BaseModel):
    """Human review corrections to extracted data."""
    extracted_plan: ExtractedInsurancePlan
    reviewer_notes: Optional[str] = None
    approved: bool = False
