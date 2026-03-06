"""
NHCX Extraction Quality Measurement Framework
=================================================
Measures the accuracy, completeness, and compliance of the
PDF → FHIR InsurancePlan conversion pipeline.

Three measurement dimensions:
  1. EXTRACTION QUALITY — How well did the LLM pull data from the PDF?
  2. FHIR COMPLIANCE — Does the output conform to NRCeS NHCX profiles?
  3. PIPELINE RELIABILITY — Success rates, latency, error categorization

Think of it like a hospital lab's quality control:
  - Extraction = Did we read the test results correctly?
  - Compliance = Did we report it in the right format?
  - Reliability = Does our equipment work consistently?
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("nhcx-converter.quality")


# ═══════════════════════════════════════════════════════════════════════════
# 1. EXTRACTION QUALITY SCORING
# ═══════════════════════════════════════════════════════════════════════════

# Field definitions with weights (higher weight = more important for FHIR bundle)
FIELD_DEFINITIONS = {
    # Core identity fields (weight: critical)
    "plan_name":                    {"weight": 10, "category": "identity",    "required": True,  "label": "Plan Name"},
    "plan_identifier":              {"weight": 9,  "category": "identity",    "required": True,  "label": "Plan UIN/ID"},
    "plan_type":                    {"weight": 7,  "category": "identity",    "required": True,  "label": "Plan Type"},
    "insurer_name":                 {"weight": 10, "category": "identity",    "required": True,  "label": "Insurer Name"},
    "insurer_irdai_registration":   {"weight": 6,  "category": "identity",    "required": False, "label": "IRDAI Registration"},
    "status":                       {"weight": 5,  "category": "identity",    "required": True,  "label": "Plan Status"},

    # Coverage fields (weight: high)
    "coverages":                    {"weight": 10, "category": "coverage",    "required": True,  "label": "Coverage Types", "is_list": True},
    "coverage_areas":               {"weight": 6,  "category": "coverage",    "required": False, "label": "Geographic Coverage", "is_list": True},
    "exclusions":                   {"weight": 9,  "category": "coverage",    "required": True,  "label": "Exclusions", "is_list": True},

    # Financial fields (weight: high)
    "plan_costs":                   {"weight": 8,  "category": "financial",   "required": True,  "label": "Plan Costs", "is_list": True},

    # Temporal fields
    "effective_from":               {"weight": 5,  "category": "temporal",    "required": False, "label": "Effective From"},
    "effective_to":                 {"weight": 5,  "category": "temporal",    "required": False, "label": "Effective To"},

    # Waiting periods (India-specific, critical for claims)
    "initial_waiting_period_days":          {"weight": 8, "category": "waiting", "required": False, "label": "Initial Waiting Period"},
    "pre_existing_disease_waiting_days":    {"weight": 8, "category": "waiting", "required": False, "label": "PED Waiting Period"},
    "specific_disease_waiting_days":        {"weight": 6, "category": "waiting", "required": False, "label": "Specific Disease Waiting"},

    # Contact
    "contact_phone":    {"weight": 3, "category": "contact", "required": False, "label": "Phone"},
    "contact_email":    {"weight": 3, "category": "contact", "required": False, "label": "Email"},
    "contact_website":  {"weight": 3, "category": "contact", "required": False, "label": "Website"},

    # Network
    "tpa_name":               {"weight": 4, "category": "network", "required": False, "label": "TPA Name"},
    "network_hospital_count": {"weight": 4, "category": "network", "required": False, "label": "Network Hospitals"},
}


@dataclass
class ExtractionScore:
    """
    Detailed extraction quality score for a single conversion job.
    
    Scores are on a 0-100 scale, like a medical test result:
      95-100: Excellent — all critical fields extracted accurately
      80-94:  Good — major fields present, minor gaps
      60-79:  Acceptable — needs human review for missing fields
      <60:    Poor — significant data loss, manual correction needed
    """
    job_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Overall scores
    overall_score: float = 0.0
    weighted_completeness: float = 0.0
    llm_confidence: float = 0.0

    # Category scores
    identity_score: float = 0.0
    coverage_score: float = 0.0
    financial_score: float = 0.0
    waiting_period_score: float = 0.0
    contact_score: float = 0.0

    # Detail counts
    total_fields: int = 0
    populated_fields: int = 0
    required_populated: int = 0
    required_total: int = 0
    coverage_count: int = 0
    benefit_count: int = 0
    exclusion_count: int = 0
    cost_variant_count: int = 0

    # Quality flags
    warnings: List[str] = field(default_factory=list)
    grade: str = ""  # A, B, C, D, F

    def to_dict(self):
        return asdict(self)


def score_extraction(extracted_plan: Dict[str, Any], job_id: str = "") -> ExtractionScore:
    """
    Score the quality of LLM extraction against the schema.
    
    This is like grading a medical transcription — we check:
      1. Did the transcriber capture all the key information?
      2. Are the critical fields (diagnosis, medication, dosage) present?
      3. Are the secondary fields (contact info, notes) filled in?
    """
    score = ExtractionScore(job_id=job_id)
    
    if not extracted_plan:
        score.grade = "F"
        score.warnings.append("No extraction data available")
        return score

    total_weight = 0
    achieved_weight = 0
    category_weights: Dict[str, Dict] = {}

    for field_name, field_def in FIELD_DEFINITIONS.items():
        weight = field_def["weight"]
        category = field_def["category"]
        is_list = field_def.get("is_list", False)
        required = field_def["required"]

        total_weight += weight
        score.total_fields += 1

        if required:
            score.required_total += 1

        # Initialize category tracking
        if category not in category_weights:
            category_weights[category] = {"total": 0, "achieved": 0}
        category_weights[category]["total"] += weight

        # Check if field is populated
        value = extracted_plan.get(field_name)
        populated = False

        if is_list:
            if isinstance(value, list) and len(value) > 0:
                populated = True
                # Bonus points for richer extraction
                if field_name == "coverages":
                    score.coverage_count = len(value)
                    score.benefit_count = sum(
                        len(c.get("benefits", [])) for c in value if isinstance(c, dict)
                    )
                elif field_name == "exclusions":
                    score.exclusion_count = len(value)
                elif field_name == "plan_costs":
                    score.cost_variant_count = len(value)
        else:
            if value is not None and str(value).strip() and str(value).lower() != "none":
                populated = True

        if populated:
            achieved_weight += weight
            category_weights[category]["achieved"] += weight
            score.populated_fields += 1
            if required:
                score.required_populated += 1
        else:
            if required:
                score.warnings.append(f"Required field missing: {field_def['label']}")

    # Calculate scores
    score.weighted_completeness = (achieved_weight / max(total_weight, 1)) * 100

    # LLM confidence
    conf = extracted_plan.get("extraction_confidence")
    score.llm_confidence = (float(conf) * 100) if conf else 0

    # Category scores
    for cat, data in category_weights.items():
        cat_score = (data["achieved"] / max(data["total"], 1)) * 100
        if cat == "identity":
            score.identity_score = cat_score
        elif cat == "coverage":
            score.coverage_score = cat_score
        elif cat == "financial":
            score.financial_score = cat_score
        elif cat == "waiting":
            score.waiting_period_score = cat_score
        elif cat == "contact":
            score.contact_score = cat_score

    # Overall = weighted average of completeness and LLM confidence
    score.overall_score = round(
        (score.weighted_completeness * 0.7) + (score.llm_confidence * 0.3), 1
    )

    # Quality warnings
    if score.coverage_count == 0:
        score.warnings.append("No coverage types extracted — critical for FHIR InsurancePlan")
    if score.coverage_count > 0 and score.benefit_count == 0:
        score.warnings.append("Coverages found but no individual benefits extracted")
    if score.exclusion_count == 0:
        score.warnings.append("No exclusions extracted — most Indian health plans have 10+ exclusions")
    if score.cost_variant_count == 0:
        score.warnings.append("No plan cost variants found — premium/deductible data missing")
    if score.waiting_period_score == 0:
        score.warnings.append("No waiting period data — critical for Indian health insurance claims")

    # Grade
    if score.overall_score >= 95:
        score.grade = "A+"
    elif score.overall_score >= 85:
        score.grade = "A"
    elif score.overall_score >= 75:
        score.grade = "B"
    elif score.overall_score >= 60:
        score.grade = "C"
    elif score.overall_score >= 40:
        score.grade = "D"
    else:
        score.grade = "F"

    return score


# ═══════════════════════════════════════════════════════════════════════════
# 2. PIPELINE RELIABILITY METRICS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineMetrics:
    """
    Tracks pipeline reliability over time.
    
    Like a hospital's operational dashboard:
      - How many patients processed today?
      - What's the average wait time?
      - How many tests failed and why?
    """
    total_jobs: int = 0
    successful_jobs: int = 0
    failed_jobs: int = 0
    review_jobs: int = 0

    # Timing
    avg_processing_seconds: float = 0.0
    min_processing_seconds: float = 0.0
    max_processing_seconds: float = 0.0
    p95_processing_seconds: float = 0.0

    # Success rate
    success_rate_percent: float = 0.0
    
    # Error breakdown
    error_categories: Dict[str, int] = field(default_factory=dict)

    # By insurer (tracking accuracy per insurer helps identify problem patterns)
    insurer_stats: Dict[str, Dict] = field(default_factory=dict)

    # Quality distribution
    grade_distribution: Dict[str, int] = field(default_factory=lambda: {
        "A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "F": 0
    })


class MetricsCollector:
    """
    Collects and aggregates pipeline metrics.
    Stores metrics in JSON for dashboard consumption.
    """

    def __init__(self, metrics_dir: str = "./data/metrics"):
        self.metrics_dir = metrics_dir
        os.makedirs(metrics_dir, exist_ok=True)
        self._metrics_file = os.path.join(metrics_dir, "pipeline_metrics.json")
        self._job_history_file = os.path.join(metrics_dir, "job_history.jsonl")
        self._metrics = self._load_metrics()

    def _load_metrics(self) -> PipelineMetrics:
        """Load existing metrics or create new."""
        if os.path.exists(self._metrics_file):
            try:
                with open(self._metrics_file, "r") as f:
                    data = json.load(f)
                return PipelineMetrics(**data)
            except:
                pass
        return PipelineMetrics()

    def _save_metrics(self):
        """Persist metrics to disk."""
        with open(self._metrics_file, "w") as f:
            json.dump(asdict(self._metrics), f, indent=2, default=str)

    def record_job(self, job_id: str, status: str, processing_seconds: float,
                   extraction_score: Optional[ExtractionScore] = None,
                   insurer: str = "", error_category: str = ""):
        """
        Record a completed (or failed) job.
        
        Parameters:
            job_id: Unique job identifier
            status: 'completed', 'review', or 'failed'
            processing_seconds: Total pipeline time
            extraction_score: Quality score from score_extraction()
            insurer: Insurer name for per-insurer tracking
            error_category: Error type if failed
        """
        m = self._metrics
        m.total_jobs += 1

        if status == "completed":
            m.successful_jobs += 1
        elif status == "review":
            m.review_jobs += 1
        elif status == "failed":
            m.failed_jobs += 1
            if error_category:
                m.error_categories[error_category] = m.error_categories.get(error_category, 0) + 1

        # Update timing
        all_times = self._get_processing_times()
        all_times.append(processing_seconds)
        m.avg_processing_seconds = round(sum(all_times) / len(all_times), 2)
        m.min_processing_seconds = round(min(all_times), 2)
        m.max_processing_seconds = round(max(all_times), 2)
        if len(all_times) >= 20:
            sorted_times = sorted(all_times)
            m.p95_processing_seconds = round(sorted_times[int(len(sorted_times) * 0.95)], 2)

        # Success rate
        m.success_rate_percent = round(
            ((m.successful_jobs + m.review_jobs) / max(m.total_jobs, 1)) * 100, 1
        )

        # Quality grade
        if extraction_score:
            m.grade_distribution[extraction_score.grade] = m.grade_distribution.get(extraction_score.grade, 0) + 1
        
        # Insurer tracking
        if insurer:
            if insurer not in m.insurer_stats:
                m.insurer_stats[insurer] = {"total": 0, "successful": 0, "avg_score": 0}
            m.insurer_stats[insurer]["total"] += 1
            if status in ("completed", "review"):
                m.insurer_stats[insurer]["successful"] += 1
            if extraction_score:
                prev_avg = m.insurer_stats[insurer]["avg_score"]
                prev_count = m.insurer_stats[insurer]["total"] - 1
                m.insurer_stats[insurer]["avg_score"] = round(
                    (prev_avg * prev_count + extraction_score.overall_score) / m.insurer_stats[insurer]["total"], 1
                )

        # Append to history
        history_entry = {
            "job_id": job_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": status,
            "processing_seconds": processing_seconds,
            "extraction_grade": extraction_score.grade if extraction_score else None,
            "extraction_score": extraction_score.overall_score if extraction_score else None,
            "insurer": insurer,
            "error_category": error_category,
        }
        with open(self._job_history_file, "a") as f:
            f.write(json.dumps(history_entry) + "\n")

        self._save_metrics()
        logger.info("METRICS | Job %s | Status: %s | Time: %.1fs | Grade: %s",
                     job_id, status, processing_seconds,
                     extraction_score.grade if extraction_score else "N/A")

    def _get_processing_times(self) -> List[float]:
        """Load processing times from history."""
        times = []
        if os.path.exists(self._job_history_file):
            with open(self._job_history_file, "r") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get("processing_seconds"):
                            times.append(entry["processing_seconds"])
        return times

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get complete metrics for the dashboard."""
        m = self._metrics
        return {
            "summary": {
                "total_jobs": m.total_jobs,
                "successful": m.successful_jobs,
                "in_review": m.review_jobs,
                "failed": m.failed_jobs,
                "success_rate": m.success_rate_percent,
            },
            "timing": {
                "average": m.avg_processing_seconds,
                "min": m.min_processing_seconds,
                "max": m.max_processing_seconds,
                "p95": m.p95_processing_seconds,
            },
            "quality": {
                "grade_distribution": m.grade_distribution,
            },
            "error_breakdown": m.error_categories,
            "insurer_performance": m.insurer_stats,
        }

    def get_job_history(self, limit: int = 50) -> List[Dict]:
        """Get recent job history."""
        entries = []
        if os.path.exists(self._job_history_file):
            with open(self._job_history_file, "r") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
        return entries[-limit:]
