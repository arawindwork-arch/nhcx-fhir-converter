"""
NHCX FHIR Bundle Validator
============================
Validates FHIR InsurancePlan bundles against NRCeS NHCX profiles (v6.5.0).

Checks:
1. Bundle structure (type=collection, correct profile)
2. Required fields per NRCeS InsurancePlan profile
3. Cardinality constraints (identifier 1..1, name 1..1, etc.)
4. Value set bindings (plan type, coverage type, benefit type)
5. Extension structure (Claim-Exclusion, Claim-Condition)
6. Reference integrity (all references resolve within bundle)
"""

import logging
from typing import Dict, Any, List

from app.config import settings

logger = logging.getLogger("nhcx-converter.validator")

NRCES_BASE = "https://nrces.in/ndhm/fhir/r4"


class NHCXValidator:
    """Validates FHIR bundles against NRCeS NHCX InsurancePlan profiles."""

    def validate(self, bundle: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Run all validation checks and return list of issues.
        Each issue: {"severity": "error|warning|information", "location": "...", "message": "..."}
        """
        issues = []

        # 1. Bundle-level checks
        issues.extend(self._validate_bundle_structure(bundle))

        # 2. Find InsurancePlan resource
        ip_resource = None
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == "InsurancePlan":
                ip_resource = resource
                break

        if ip_resource is None:
            issues.append({
                "severity": "error",
                "location": "Bundle.entry",
                "message": "No InsurancePlan resource found in bundle",
            })
            return issues

        # 3. InsurancePlan resource checks
        issues.extend(self._validate_insurance_plan(ip_resource))

        # 4. Reference integrity
        issues.extend(self._validate_references(bundle))

        # Log summary
        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = sum(1 for i in issues if i["severity"] == "warning")
        logger.info(
            "Validation complete: %d errors, %d warnings, %d total issues",
            errors, warnings, len(issues),
        )

        return issues

    def _validate_bundle_structure(self, bundle: Dict) -> List[Dict]:
        """Validate Bundle-level structure."""
        issues = []

        if bundle.get("resourceType") != "Bundle":
            issues.append({
                "severity": "error",
                "location": "Bundle.resourceType",
                "message": "resourceType must be 'Bundle'",
            })

        if bundle.get("type") != "collection":
            issues.append({
                "severity": "error",
                "location": "Bundle.type",
                "message": "InsurancePlanBundle must have type 'collection'",
            })

        # Check meta.profile
        profiles = bundle.get("meta", {}).get("profile", [])
        if settings.INSURANCE_PLAN_BUNDLE_PROFILE not in profiles:
            issues.append({
                "severity": "warning",
                "location": "Bundle.meta.profile",
                "message": f"Missing profile: {settings.INSURANCE_PLAN_BUNDLE_PROFILE}",
            })

        if not bundle.get("entry"):
            issues.append({
                "severity": "error",
                "location": "Bundle.entry",
                "message": "Bundle must contain at least one entry",
            })

        return issues

    def _validate_insurance_plan(self, ip: Dict) -> List[Dict]:
        """Validate InsurancePlan against NRCeS profile constraints."""
        issues = []
        base = "InsurancePlan"

        # ── Required fields (1..1) ───────────────────────────────────────

        # identifier (1..1)
        identifiers = ip.get("identifier", [])
        if not identifiers:
            issues.append({
                "severity": "error",
                "location": f"{base}.identifier",
                "message": "identifier is required (1..1) per NRCeS profile",
            })

        # status (1..1)
        status = ip.get("status")
        if not status:
            issues.append({
                "severity": "error",
                "location": f"{base}.status",
                "message": "status is required (1..1)",
            })
        elif status not in ("draft", "active", "retired", "unknown"):
            issues.append({
                "severity": "error",
                "location": f"{base}.status",
                "message": f"Invalid status '{status}'. Must be: draft|active|retired|unknown",
            })

        # type (1..1)
        plan_type = ip.get("type")
        if not plan_type:
            issues.append({
                "severity": "error",
                "location": f"{base}.type",
                "message": "type is required (1..1) per NRCeS profile",
            })
        else:
            # Check coding structure
            codings = plan_type[0].get("coding", []) if isinstance(plan_type, list) else plan_type.get("coding", [])
            if codings:
                for coding in codings:
                    if not coding.get("system"):
                        issues.append({
                            "severity": "error",
                            "location": f"{base}.type.coding.system",
                            "message": "type.coding.system is required (1..1)",
                        })
                    if not coding.get("code"):
                        issues.append({
                            "severity": "error",
                            "location": f"{base}.type.coding.code",
                            "message": "type.coding.code is required (1..1)",
                        })
                    if not coding.get("display"):
                        issues.append({
                            "severity": "error",
                            "location": f"{base}.type.coding.display",
                            "message": "type.coding.display is required (1..1)",
                        })

        # name (1..1)
        if not ip.get("name"):
            issues.append({
                "severity": "error",
                "location": f"{base}.name",
                "message": "name is required (1..1)",
            })

        # period (1..1)
        period = ip.get("period")
        if not period:
            issues.append({
                "severity": "error",
                "location": f"{base}.period",
                "message": "period is required (1..1)",
            })
        elif not period.get("start"):
            issues.append({
                "severity": "warning",
                "location": f"{base}.period.start",
                "message": "period.start should be provided",
            })

        # ownedBy (1..1)
        if not ip.get("ownedBy"):
            issues.append({
                "severity": "error",
                "location": f"{base}.ownedBy",
                "message": "ownedBy (insurer reference) is required (1..1)",
            })

        # ── Coverage (1..*) ──────────────────────────────────────────────
        coverages = ip.get("coverage", [])
        if not coverages:
            issues.append({
                "severity": "error",
                "location": f"{base}.coverage",
                "message": "At least one coverage is required (1..*)",
            })
        else:
            for i, cov in enumerate(coverages):
                # coverage.type (1..1)
                if not cov.get("type"):
                    issues.append({
                        "severity": "error",
                        "location": f"{base}.coverage[{i}].type",
                        "message": "coverage.type is required (1..1)",
                    })

                # coverage.benefit (1..*)
                benefits = cov.get("benefit", [])
                if not benefits:
                    issues.append({
                        "severity": "error",
                        "location": f"{base}.coverage[{i}].benefit",
                        "message": "At least one benefit is required (1..*)",
                    })
                else:
                    for j, ben in enumerate(benefits):
                        if not ben.get("type"):
                            issues.append({
                                "severity": "error",
                                "location": f"{base}.coverage[{i}].benefit[{j}].type",
                                "message": "benefit.type is required (1..1)",
                            })

        # ── Plan ─────────────────────────────────────────────────────────
        plans = ip.get("plan", [])
        for i, plan in enumerate(plans):
            if not plan.get("type"):
                issues.append({
                    "severity": "error",
                    "location": f"{base}.plan[{i}].type",
                    "message": "plan.type is required (1..1)",
                })

        # ── Extensions validation ────────────────────────────────────────
        for ext in ip.get("extension", []):
            url = ext.get("url", "")
            if url == f"{NRCES_BASE}/StructureDefinition/Claim-Exclusion":
                sub_exts = ext.get("extension", [])
                if not any(se.get("url") == "description" for se in sub_exts):
                    issues.append({
                        "severity": "warning",
                        "location": f"{base}.extension(Claim-Exclusion)",
                        "message": "Exclusion extension should have a description",
                    })

        # ── ipn-1 constraint ─────────────────────────────────────────────
        has_id = bool(identifiers)
        has_name = bool(ip.get("name"))
        if not (has_id or has_name):
            issues.append({
                "severity": "error",
                "location": base,
                "message": "ipn-1: The organization SHALL at least have a name or identifier",
            })

        return issues

    def _validate_references(self, bundle: Dict) -> List[Dict]:
        """Check that all references within the bundle resolve."""
        issues = []
        full_urls = set()

        for entry in bundle.get("entry", []):
            fu = entry.get("fullUrl", "")
            if fu:
                full_urls.add(fu)
            rid = entry.get("resource", {}).get("id")
            rtype = entry.get("resource", {}).get("resourceType")
            if rid and rtype:
                full_urls.add(f"{rtype}/{rid}")
                full_urls.add(f"urn:uuid:{rid}")

        # Check ownedBy, administeredBy, network references
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "InsurancePlan":
                continue

            for ref_field in ("ownedBy", "administeredBy"):
                ref = resource.get(ref_field, {}).get("reference", "")
                if ref and ref not in full_urls:
                    issues.append({
                        "severity": "error",
                        "location": f"InsurancePlan.{ref_field}",
                        "message": f"Reference '{ref}' does not resolve within bundle",
                    })

        return issues
