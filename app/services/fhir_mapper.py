"""
FHIR InsurancePlan Bundle Mapper
=================================
Maps extracted insurance plan data to NHCX-compliant FHIR R4 resources.

Conforms to:
  - NRCeS FHIR IG for ABDM v6.5.0
  - InsurancePlan profile: https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlan
  - InsurancePlanBundle profile: https://nrces.in/ndhm/fhir/r4/StructureDefinition/InsurancePlanBundle

NHCX-specific extensions supported:
  - Claim-Exclusion
  - Claim-Condition
  - Claim-SupportingInfoRequirement
"""

import uuid
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.config import settings
from app.api.models import (
    ExtractedInsurancePlan,
    CoverageDetail,
    Benefit,
    Exclusion,
    PlanCost,
)

logger = logging.getLogger("nhcx-converter.fhir")

# ═══════════════════════════════════════════════════════════════════════════
# NRCeS NHCX value set URIs
# ═══════════════════════════════════════════════════════════════════════════
NRCES_BASE = "https://nrces.in/ndhm/fhir/r4"
VS_PLAN_TYPE = f"{NRCES_BASE}/CodeSystem/ndhm-insuranceplan-type"
VS_COVERAGE_TYPE = f"{NRCES_BASE}/CodeSystem/ndhm-coverage-type"
VS_BENEFIT_TYPE = f"{NRCES_BASE}/CodeSystem/ndhm-benefit-type"
VS_PLAN_VARIANT = f"{NRCES_BASE}/CodeSystem/ndhm-plan-type"
VS_BENEFIT_CATEGORY = f"{NRCES_BASE}/CodeSystem/ndhm-benefitcategory"
VS_PRODUCT_SERVICE = f"{NRCES_BASE}/CodeSystem/ndhm-productorservice"
EXT_EXCLUSION = f"{NRCES_BASE}/StructureDefinition/Claim-Exclusion"
EXT_CONDITION = f"{NRCES_BASE}/StructureDefinition/Claim-Condition"
EXT_SUPPORTING_INFO = f"{NRCES_BASE}/StructureDefinition/Claim-SupportingInfoRequirement"

# Plan type mapping from common terms → NRCeS codes
PLAN_TYPE_MAP = {
    "individual": {"code": "INDV", "display": "Individual"},
    "family-floater": {"code": "FMLY", "display": "Family Floater"},
    "family_floater": {"code": "FMLY", "display": "Family Floater"},
    "group": {"code": "GRP", "display": "Group"},
    "government": {"code": "GOVT", "display": "Government"},
    "critical-illness": {"code": "CI", "display": "Critical Illness"},
    "top-up": {"code": "TOPUP", "display": "Top Up"},
    "super-top-up": {"code": "STOPUP", "display": "Super Top Up"},
    "other": {"code": "OTH", "display": "Other"},
}

COVERAGE_TYPE_MAP = {
    "inpatient": {"code": "IPH", "display": "In-Patient Hospitalization"},
    "daycare": {"code": "DC", "display": "Day Care"},
    "outpatient": {"code": "OPD", "display": "Out-Patient"},
    "maternity": {"code": "MAT", "display": "Maternity"},
    "domiciliary": {"code": "DOM", "display": "Domiciliary Hospitalization"},
    "organ-donor": {"code": "OD", "display": "Organ Donor"},
    "ayush": {"code": "AYUSH", "display": "AYUSH Treatment"},
    "other": {"code": "OTH", "display": "Other"},
}


class FHIRMapper:
    """Maps extracted insurance plan data to FHIR R4 InsurancePlan Bundle."""

    def map_to_insurance_plan_bundle(
        self, plan: ExtractedInsurancePlan
    ) -> Dict[str, Any]:
        """
        Create a complete NHCX-compliant FHIR InsurancePlanBundle.

        Returns a Bundle of type "collection" containing:
        1. InsurancePlan resource
        2. Organization resource (insurer)
        3. Organization resource (TPA, if applicable)
        """
        bundle_id = str(uuid.uuid4())
        insurer_id = str(uuid.uuid4())
        plan_id = str(uuid.uuid4())
        tpa_id = str(uuid.uuid4()) if plan.tpa_name else None

        # Build resources
        insurer_org = self._build_organization(
            resource_id=insurer_id,
            name=plan.insurer_name,
            irdai_reg=plan.insurer_irdai_registration,
            contact_phone=plan.contact_phone,
            contact_email=plan.contact_email,
            contact_website=plan.contact_website,
        )

        insurance_plan = self._build_insurance_plan(
            resource_id=plan_id,
            plan=plan,
            insurer_ref=f"Organization/{insurer_id}",
            tpa_ref=f"Organization/{tpa_id}" if tpa_id else None,
        )

        entries = [
            {"fullUrl": f"urn:uuid:{plan_id}", "resource": insurance_plan},
            {"fullUrl": f"urn:uuid:{insurer_id}", "resource": insurer_org},
        ]

        if plan.tpa_name and tpa_id:
            tpa_org = self._build_organization(
                resource_id=tpa_id,
                name=plan.tpa_name,
            )
            entries.append({"fullUrl": f"urn:uuid:{tpa_id}", "resource": tpa_org})

        bundle = {
            "resourceType": "Bundle",
            "id": bundle_id,
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
                "profile": [settings.INSURANCE_PLAN_BUNDLE_PROFILE],
            },
            "type": "collection",
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
            "entry": entries,
        }

        logger.info(
            "FHIR bundle created: id=%s, entries=%d", bundle_id, len(entries)
        )
        return bundle

    # ═══════════════════════════════════════════════════════════════════════
    # InsurancePlan resource builder
    # ═══════════════════════════════════════════════════════════════════════

    def _build_insurance_plan(
        self,
        resource_id: str,
        plan: ExtractedInsurancePlan,
        insurer_ref: str,
        tpa_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build NRCeS-compliant InsurancePlan resource."""
        plan_type_info = PLAN_TYPE_MAP.get(
            plan.plan_type, PLAN_TYPE_MAP["other"]
        )

        resource = {
            "resourceType": "InsurancePlan",
            "id": resource_id,
            "meta": {
                "profile": [settings.NHCX_PROFILE_URL],
            },
            # ── Identifier (1..1 required) ───────────────────────────────
            "identifier": [
                {
                    "system": f"{NRCES_BASE}/identifier/insurance-plan",
                    "value": plan.plan_identifier or f"PLAN-{resource_id[:8]}",
                }
            ],
            # ── Status (1..1 required) ───────────────────────────────────
            "status": plan.status or "active",
            # ── Type (1..1 required) ─────────────────────────────────────
            "type": [
                {
                    "coding": [
                        {
                            "system": VS_PLAN_TYPE,
                            "code": plan_type_info["code"],
                            "display": plan_type_info["display"],
                        }
                    ],
                    "text": plan.plan_type,
                }
            ],
            # ── Name (1..1 required) ─────────────────────────────────────
            "name": plan.plan_name,
            # ── Period (1..1 required) ───────────────────────────────────
            "period": {
                "start": plan.effective_from or datetime.utcnow().strftime("%Y-%m-%d"),
            },
            # ── OwnedBy (1..1 required) — insurer ───────────────────────
            "ownedBy": {"reference": insurer_ref},
        }

        # ── Optional: aliases ────────────────────────────────────────────
        if plan.plan_aliases:
            resource["alias"] = plan.plan_aliases

        # ── Optional: period end ─────────────────────────────────────────
        if plan.effective_to:
            resource["period"]["end"] = plan.effective_to

        # ── Optional: administeredBy (TPA) ───────────────────────────────
        if tpa_ref:
            resource["administeredBy"] = {"reference": tpa_ref}

        # ── Optional: coverageArea ───────────────────────────────────────
        if plan.coverage_areas:
            resource["coverageArea"] = [
                {"display": area} for area in plan.coverage_areas
            ]

        # ── Optional: contact ────────────────────────────────────────────
        contacts = []
        telecoms = []
        if plan.contact_phone:
            telecoms.append({"system": "phone", "value": plan.contact_phone})
        if plan.contact_email:
            telecoms.append({"system": "email", "value": plan.contact_email})
        if plan.contact_website:
            telecoms.append({"system": "url", "value": plan.contact_website})
        if telecoms:
            contacts.append({"telecom": telecoms})
            resource["contact"] = contacts

        # ── Extensions: Exclusions ───────────────────────────────────────
        extensions = []
        for excl in plan.exclusions:
            ext = self._build_exclusion_extension(excl)
            extensions.append(ext)

        if extensions:
            resource["extension"] = extensions

        # ── Coverage (1..*) ──────────────────────────────────────────────
        coverages = []
        for cov in plan.coverages:
            coverages.append(self._build_coverage(cov))

        # Ensure at least one coverage
        if not coverages:
            coverages.append({
                "type": {
                    "coding": [{"system": VS_COVERAGE_TYPE, "code": "IPH", "display": "In-Patient Hospitalization"}],
                    "text": "In-Patient Hospitalization",
                },
                "benefit": [{"type": {"text": "General Coverage"}}],
            })

        resource["coverage"] = coverages

        # ── Plan (cost details) ──────────────────────────────────────────
        if plan.plan_costs:
            resource["plan"] = self._build_plan_costs(plan.plan_costs)

        return resource

    # ═══════════════════════════════════════════════════════════════════════
    # Sub-builders
    # ═══════════════════════════════════════════════════════════════════════

    def _build_coverage(self, cov: CoverageDetail) -> Dict[str, Any]:
        """Build InsurancePlan.coverage element."""
        cov_type_info = COVERAGE_TYPE_MAP.get(
            cov.coverage_type, COVERAGE_TYPE_MAP["other"]
        )

        coverage = {
            "type": {
                "coding": [
                    {
                        "system": VS_COVERAGE_TYPE,
                        "code": cov_type_info["code"],
                        "display": cov_type_info["display"],
                    }
                ],
                "text": cov.description or cov.coverage_type,
            },
            "benefit": [],
        }

        # Extensions for coverage-level conditions
        extensions = []
        for cond in cov.conditions:
            extensions.append({
                "url": EXT_CONDITION,
                "valueString": cond,
            })
        for doc in cov.supporting_info_requirements:
            extensions.append({
                "url": EXT_SUPPORTING_INFO,
                "valueString": doc,
            })
        if extensions:
            coverage["extension"] = extensions

        # Benefits
        for benefit in cov.benefits:
            coverage["benefit"].append(self._build_benefit(benefit))

        # Ensure at least one benefit
        if not coverage["benefit"]:
            coverage["benefit"].append({"type": {"text": cov.coverage_type}})

        return coverage

    def _build_benefit(self, benefit: Benefit) -> Dict[str, Any]:
        """Build InsurancePlan.coverage.benefit element."""
        b = {
            "type": {
                "coding": [
                    {
                        "system": VS_BENEFIT_TYPE,
                        "code": benefit.benefit_type or "general",
                        "display": benefit.name,
                    }
                ] if benefit.benefit_type else [],
                "text": benefit.name,
            },
        }

        # Sub-limits
        if benefit.limits:
            b["limit"] = []
            for limit in benefit.limits:
                lim = {}
                if limit.value is not None:
                    lim["value"] = {
                        "value": limit.value,
                        "unit": limit.unit or "INR",
                        "system": "urn:iso:std:iso:4217" if limit.unit in ("INR", None) else None,
                        "code": limit.unit or "INR",
                    }
                if limit.description:
                    lim["code"] = {"text": limit.description}
                if lim:
                    b["limit"].append(lim)

        # Extensions for benefit-level conditions and docs
        extensions = []
        for cond in benefit.conditions:
            extensions.append({
                "url": EXT_CONDITION,
                "valueString": cond,
            })
        for doc in benefit.supporting_docs:
            extensions.append({
                "url": EXT_SUPPORTING_INFO,
                "valueString": doc,
            })
        if extensions:
            b["extension"] = extensions

        return b

    def _build_exclusion_extension(self, excl: Exclusion) -> Dict[str, Any]:
        """Build Claim-Exclusion extension."""
        ext = {
            "url": EXT_EXCLUSION,
            "extension": [
                {
                    "url": "exclusionType",
                    "valueString": excl.exclusion_type or "permanent",
                },
                {
                    "url": "description",
                    "valueString": excl.description or excl.name,
                },
            ],
        }
        if excl.waiting_period_days:
            ext["extension"].append({
                "url": "waitingPeriod",
                "valueDuration": {
                    "value": excl.waiting_period_days,
                    "unit": "days",
                    "system": "http://unitsofmeasure.org",
                    "code": "d",
                },
            })
        return ext

    def _build_plan_costs(self, costs: List[PlanCost]) -> List[Dict[str, Any]]:
        """Build InsurancePlan.plan array with cost details."""
        plans = []
        for cost in costs:
            plan_entry = {
                "type": {
                    "coding": [
                        {
                            "system": VS_PLAN_VARIANT,
                            "code": (cost.plan_variant or "standard").lower().replace(" ", "-"),
                            "display": cost.plan_variant or "Standard",
                        }
                    ],
                    "text": cost.plan_variant or "Standard",
                },
            }

            # General cost (sum insured)
            if cost.sum_insured:
                plan_entry["generalCost"] = [
                    {
                        "type": {"text": "Sum Insured"},
                        "cost": {
                            "value": cost.sum_insured,
                            "unit": "INR",
                            "system": "urn:iso:std:iso:4217",
                            "code": "INR",
                        },
                    }
                ]

            # Specific costs (premium, copay, deductible)
            specific_costs = []
            if cost.premium:
                specific_costs.append({
                    "category": {
                        "coding": [{"system": VS_BENEFIT_CATEGORY, "code": "premium", "display": "Premium"}],
                        "text": f"Annual Premium{' (' + cost.age_band + ')' if cost.age_band else ''}",
                    },
                    "benefit": [
                        {
                            "type": {"text": "Annual Premium"},
                            "cost": [
                                {
                                    "type": {"text": "premium"},
                                    "value": {
                                        "value": cost.premium,
                                        "unit": "INR",
                                        "system": "urn:iso:std:iso:4217",
                                        "code": "INR",
                                    },
                                }
                            ],
                        }
                    ],
                })

            if cost.copay_percentage:
                specific_costs.append({
                    "category": {"text": "Co-payment"},
                    "benefit": [
                        {
                            "type": {"text": "Co-payment"},
                            "cost": [
                                {
                                    "type": {"text": "copay"},
                                    "value": {
                                        "value": cost.copay_percentage,
                                        "unit": "%",
                                    },
                                }
                            ],
                        }
                    ],
                })

            if specific_costs:
                plan_entry["specificCost"] = specific_costs

            plans.append(plan_entry)

        return plans

    # ═══════════════════════════════════════════════════════════════════════
    # Organization resource
    # ═══════════════════════════════════════════════════════════════════════

    def _build_organization(
        self,
        resource_id: str,
        name: str,
        irdai_reg: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
        contact_website: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build Organization resource (insurer or TPA)."""
        org = {
            "resourceType": "Organization",
            "id": resource_id,
            "meta": {
                "profile": [f"{NRCES_BASE}/StructureDefinition/Organization"],
            },
            "name": name,
        }

        if irdai_reg:
            org["identifier"] = [
                {
                    "system": "https://irdai.gov.in/registration",
                    "value": irdai_reg,
                }
            ]

        telecoms = []
        if contact_phone:
            telecoms.append({"system": "phone", "value": contact_phone})
        if contact_email:
            telecoms.append({"system": "email", "value": contact_email})
        if contact_website:
            telecoms.append({"system": "url", "value": contact_website})
        if telecoms:
            org["telecom"] = telecoms

        return org
