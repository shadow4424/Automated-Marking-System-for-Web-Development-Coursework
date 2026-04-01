"""Assignment-level analytics engine."""

# Imports
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from ams.core.attempts import filter_attempts_for_root, list_attempts, sync_attempts_from_storage
from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats
from ams.core.database import get_assignment
from ams.core.profiles import get_relevant_components
from ams.io.metadata import MetadataValidator
from ams.io.web_storage import get_runs_root

# logger setup
logger = logging.getLogger(__name__)

# Constants and configuration for report generation
COMPONENT_ORDER = ["html", "css", "js", "php", "sql", "api"]
SMALL_COHORT_THRESHOLD = 5
SEVERITY_PRIORITY = {"FAIL": 3, "WARN": 2, "SKIPPED": 1, "PASS": 0}
GRADE_ORDER = {"unknown": 0, "failing": 1, "poor": 2, "partial": 3, "good": 4, "full marks": 5}
STATIC_ANALYTICS_STAGES = {"static", "quality"}
FUNCTIONAL_ANALYTICS_STAGES = {"runtime", "browser", "layout"}
REQUIREMENT_TITLES = {
    "html": "Required HTML structure",
    "css": "Required CSS requirements",
    "js": "Required JavaScript behaviour",
    "php": "Required PHP/backend processing",
    "sql": "Required SQL/database behaviour",
    "api": "Required API integration behaviour",
}
SIGNAL_DESCRIPTIONS = {
    "missing_backend": "Required backend files or backend rubric checks are missing.",
    "missing_frontend": "Required HTML, CSS, or JavaScript artefacts are missing.",
    "syntax": "Submissions contain syntax or parse issues that can distort scoring.",
    "behavioural_runtime": "Runtime or deterministic execution checks are failing.",
    "browser_runtime": "Browser loading or client-side execution issues were observed.",
    "consistency": "Cross-file links between HTML, CSS, JavaScript, or PHP are inconsistent.",
    "other": "Other repeated rubric-level issues were detected across the cohort.",
    "behavioural_skipped": "Runtime checks were skipped, reducing confidence in automatic interpretation.",
    "browser_skipped": "Browser checks were skipped or unavailable, reducing confidence in UI-related interpretation.",
}

FINDING_LABELS = {
    "PHP.MISSING_FILES": ("Missing required backend files (PHP)", "Required PHP files were not found."),
    "SQL.MISSING_FILES": ("Missing required backend files (SQL)", "Required SQL files were not found."),
    "CSS.MISSING_FILES": ("CSS missing", "CSS files required but not found."),
    "JS.MISSING_FILES": ("JavaScript missing", "JavaScript files required but not found."),
    "HTML.MISSING_FILES": ("HTML missing", "HTML files required but not found."),
    "PHP.REQ.MISSING_FILES": ("Missing required backend files (PHP)", "Required PHP files were not found."),
    "SQL.REQ.MISSING_FILES": ("Missing required backend files (SQL)", "Required SQL files were not found."),
    "CSS.REQ.MISSING_FILES": ("CSS missing", "CSS files required but not found."),
    "JS.REQ.MISSING_FILES": ("JavaScript missing", "JavaScript files required but not found."),
    "HTML.REQ.MISSING_FILES": ("HTML missing", "HTML files required but not found."),
    "CONFIG.MISSING_REQUIRED_RULES": ("Configuration issue", "A required component has no required rules configured."),
    "CONSISTENCY.JS_MISSING_HTML_ID": ("Cross-file consistency", "JavaScript references an HTML id that does not exist."),
    "CONSISTENCY.JS_MISSING_HTML_CLASS": ("Cross-file consistency", "JavaScript references an HTML class that does not exist."),
    "CONSISTENCY.CSS_MISSING_HTML_ID": ("Cross-file consistency", "CSS references an HTML id that does not exist."),
    "CONSISTENCY.CSS_MISSING_HTML_CLASS": ("Cross-file consistency", "CSS references an HTML class that does not exist."),
    "CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD": ("Cross-file consistency", "PHP accesses a form field that is not defined in HTML."),
    "CONSISTENCY.FORM_FIELD_UNUSED_IN_PHP": ("Cross-file consistency", "HTML form field is not accessed in PHP."),
    "CONSISTENCY.MISSING_LINK_TARGET": ("Cross-file consistency", "A link target does not exist."),
    "CONSISTENCY.MISSING_FORM_ACTION_TARGET": ("Cross-file consistency", "A form action target does not exist."),
    "BEHAVIOUR.PHP_SMOKE_FAIL": ("Runtime check failed", "PHP smoke test failed."),
    "BEHAVIOUR.PHP_SMOKE_TIMEOUT": ("Runtime check timed out", "PHP smoke test timed out."),
    "BEHAVIOUR.PHP_FORM_RUN_FAIL": ("Runtime check failed", "PHP form execution failed."),
    "BEHAVIOUR.PHP_FORM_RUN_TIMEOUT": ("Runtime check timed out", "PHP form execution timed out."),
    "BEHAVIOUR.SQL_EXEC_FAIL": ("Runtime check failed", "SQL execution failed."),
    "BEHAVIOUR.SQL_EXEC_TIMEOUT": ("Runtime check timed out", "SQL execution timed out."),
    "BROWSER.PAGE_LOAD_FAIL": ("Browser page load failed", "The browser could not load the page successfully."),
    "BROWSER.PAGE_LOAD_TIMEOUT": ("Browser page load timed out", "The browser timed out while loading the page."),
    "BROWSER.CONSOLE_ERRORS_PRESENT": ("Browser console errors", "Console errors were observed during browser checks."),
}

# Functions for generating assignment analytics
def generate_assignment_analytics(
    assignment_id: str,
    *,
    app: Any | None = None,
) -> Dict[str, object]:
    assignment = get_assignment(assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment '{assignment_id}' not found")

    # Validate assignment metadata and configuration
    profile = assignment.get("profile", "frontend")
    if app is not None:
        runs_root = get_runs_root(app)
    else:
        runs_root = Path("ams_web_runs")
        runs_root.mkdir(parents=True, exist_ok=True)

    # Collect and process records for the assignment
    records, scan = _collect_assignment_records(runs_root, assignment_id)
    analytics = _build_analytics(
        records=records,
        profile=profile,
        assigned_students=assignment.get("assigned_students", []) or [],
        scan=scan,
    )

    # Enrich analytics with assignment-level context and metadata
    analytics["assignment_id"] = assignment_id
    analytics["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    analytics["submission_count"] = len(records)
    analytics["teaching_insight_context"]["assignment_id"] = assignment_id
    return analytics

# Functions for generating student-specific analytics
def generate_student_assignment_analytics(
    assignment_id: str,
    student_id: str,
    *,
    app: Any | None = None,
) -> Dict[str, object]:
    assignment = get_assignment(assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment '{assignment_id}' not found")

    # Student-specific analytics should be generated based on the same underlying data and profile as the overall assignment analytics
    profile = assignment.get("profile", "frontend")
    if app is not None:
        runs_root = get_runs_root(app)
    else:
        runs_root = Path("ams_web_runs")
        runs_root.mkdir(parents=True, exist_ok=True)

    records, scan = _collect_assignment_records(runs_root, assignment_id)
    from ams.analytics.insights import _build_student_assignment_analytics

    analytics = _build_analytics(
        records=records,
        profile=profile,
        assigned_students=assignment.get("assigned_students", []) or [],
        scan=scan,
    )
    enriched_records = [_enrich_record(record, profile) for record in records]
    target_student_id = str(student_id or "").strip()
    student_record = next(
        (
            record
            for record in enriched_records
            if str(record.get("student_id") or "").strip() == target_student_id
        ),
        None,
    )
    if student_record is None:
        raise ValueError("No active submission is available for this student in the current assignment scope.")

    # Build student-specific analytics context and insights
    payload = _build_student_assignment_analytics(
        assignment=assignment,
        analytics=analytics,
        records=enriched_records,
        student_record=student_record,
    )
    payload["assignment_id"] = assignment_id
    payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return payload

# Internal helper functions for data collection and processing
def _collect_assignment_records(runs_root: Path, assignment_id: str) -> tuple[List[dict], dict]:
    sync_attempts_from_storage(runs_root)
    attempts = filter_attempts_for_root(
        list_attempts(assignment_id=assignment_id, newest_first=True),
        runs_root,
    )
    scan = {
        "candidate_records": len(attempts),
        "inactive_submissions": 0,
        "inactive_student_ids": set[str](),
        "superseded_student_ids": set[str](),
    }

    # Process attempts to build records and track inactive/superseded submissions
    records: List[dict] = []
    for attempt in attempts:
        student_id = str(attempt.get("student_id") or "").strip()
        if not student_id:
            continue
        if not bool(attempt.get("is_active")):
            if str(attempt.get("validity_status") or "").strip().lower() == "valid":
                scan["superseded_student_ids"].add(student_id)
            else:
                scan["inactive_submissions"] += 1
                scan["inactive_student_ids"].add(student_id)
            continue
        record = _record_from_attempt(attempt)
        if record is not None:
            records.append(record)

    # Sort records by student_id for consistent ordering
    records.sort(key=lambda rec: rec.get("student_id", ""))

    # Calculate superseded records count based on attempts and active records
    scan["superseded_records"] = max(len(attempts) - len(records) - scan["inactive_submissions"], 0)
    scan["inactive_student_ids"] = sorted(str(student_id) for student_id in scan["inactive_student_ids"] if str(student_id).strip())
    scan["superseded_student_ids"] = sorted(str(student_id) for student_id in scan["superseded_student_ids"] if str(student_id).strip())
    return records, scan

# Helper functions for transforming attempts into analytics records
def _record_from_attempt(attempt: Mapping[str, object]) -> dict | None:
    report_path_text = str(attempt.get("report_path") or "").strip()
    report_path = Path(report_path_text) if report_path_text else None
    report = _load_json(report_path) if report_path and report_path.exists() else None
    assignment_id = str(attempt.get("assignment_id") or "").strip()
    student_id = str(attempt.get("student_id") or "").strip()
    if not assignment_id or not student_id:
        return None

    # Determine submission status, source mode, and identifiers for the attempt
    status = _normalize_submission_status(
        attempt.get("pipeline_status") or attempt.get("validity_status") or "ok"
    )
    source_mode = "batch" if str(attempt.get("batch_submission_id") or "").strip() else "mark"
    run_id = str(attempt.get("run_id") or attempt.get("id") or "")
    submission_id = str(
        attempt.get("batch_submission_id")
        or attempt.get("run_id")
        or attempt.get("id")
        or student_id
    )
    created_at = str(attempt.get("submitted_at") or attempt.get("created_at") or "")
    attempt_context = {
        "attempt_id": str(attempt.get("id") or ""),
        "attempt_number": int(attempt.get("attempt_number") or 0) or None,
        "source_type": str(attempt.get("source_type") or ""),
        "validity_status": str(attempt.get("validity_status") or ""),
        "is_active": bool(attempt.get("is_active")),
        "selection_reason": str(attempt.get("selection_reason") or ""),
        "submitted_at": created_at,
    }

    # If a valid report is available, transform it into a structured record; otherwise, create an empty record with error information
    if report is not None and report_path is not None:
        record = _report_to_record(
            report=report,
            student_id=student_id,
            assignment_id=assignment_id,
            report_path=report_path,
            run_id=run_id,
            created_at=created_at,
            submission_id=submission_id,
            status=status,
            source_mode=source_mode,
        )
        record.update(attempt_context)
        return record

    # If report is missing or invalid, create an empty record with error details for visibility in analytics
    record = _empty_record(
        student_id=student_id,
        assignment_id=assignment_id,
        run_id=run_id,
        created_at=created_at,
        submission_id=submission_id,
        status=status,
        report_path=report_path_text,
        original_filename=str(attempt.get("original_filename") or ""),
        error=str(attempt.get("error_message") or ""),
        source_mode=source_mode,
        overall=attempt.get("overall_score"),
        components={},
    )
    record.update(attempt_context)
    return record

# Helper functions for data normalisation, enrichment, and transformation
def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

# Normalise various representations of submission status into a consistent set of categories for analytics interpretation
def _normalize_submission_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"", "ok", "success", "succeeded", "completed", "complete"}:
        return "ok"
    if status in {"pending", "queued", "running"}:
        return "pending"
    return status

# Function to transform a raw report into a structured record for analytics, extracting relevant information and normalising it
def _report_to_record(
    *,
    report: Mapping[str, object],
    student_id: str,
    assignment_id: str,
    report_path: Path,
    run_id: str,
    created_at: str,
    submission_id: str,
    status: str,
    source_mode: str,
) -> dict:
    from ams.analytics.insights import _first_non_empty

    # Extract scores, findings, checks, and metadata from the report
    scores = report.get("scores", {}) or {}
    component_scores = scores.get("by_component", {}) or {}
    findings = list(report.get("findings", []) or [])
    checks, check_stats, diagnostics = _ensure_check_payload(report, findings)
    submission_meta = report.get("metadata", {}).get("submission_metadata", {}) or {}
    identity_meta = report.get("metadata", {}).get("student_identity", {}) or {}

    # Builds structured record
    return {
        "id": student_id or submission_id or run_id,
        "student_id": student_id,
        "student_name": _first_non_empty(
            [
                identity_meta.get("display_name"),
                identity_meta.get("name"),
                identity_meta.get("name_normalized"),
                submission_meta.get("student_name"),
            ]
        ),
        "assignment_id": assignment_id,
        "submission_id": submission_id or student_id or run_id,
        "overall": scores.get("overall"),
        "components": {name: component_scores.get(name, {}).get("score") for name in COMPONENT_ORDER},
        "status": status or "ok",
        "findings": findings,
        "checks": checks,
        "check_stats": check_stats,
        "diagnostics": diagnostics,
        "required_rules": _extract_required_rules(findings, report.get("score_evidence", {}) or {}),
        "score_evidence": dict(report.get("score_evidence", {}) or {}),
        "behavioural_evidence": list(report.get("behavioural_evidence", []) or []),
        "browser_evidence": list(report.get("browser_evidence", []) or []),
        "environment": dict(report.get("environment", {}) or {}),
        "report_path": str(report_path),
        "run_id": run_id,
        "source_mode": source_mode,
        "_created_at": created_at,
        "_submission_id": submission_id,
        "error": "",
    }

# Defines template for empty record
def _empty_record(
    *,
    student_id: str,
    assignment_id: str,
    run_id: str,
    created_at: str,
    submission_id: str,
    status: str,
    report_path: str,
    original_filename: str,
    error: str,
    source_mode: str,
    overall: object = None,
    components: Mapping[str, object] | None = None,
) -> dict:
    component_map = dict(components or {})
    return {
        "id": student_id or submission_id or run_id,
        "student_id": student_id,
        "student_name": "",
        "assignment_id": assignment_id,
        "submission_id": submission_id or student_id or run_id,
        "overall": overall,
        "components": {name: component_map.get(name) for name in COMPONENT_ORDER},
        "status": status or "failed",
        "findings": [],
        "checks": [],
        "check_stats": {"total": 0, "passed": 0, "failed": 0, "warnings": 0, "skipped": 0},
        "diagnostics": [],
        "required_rules": {},
        "score_evidence": {},
        "behavioural_evidence": [],
        "browser_evidence": [],
        "environment": {},
        "report_path": report_path,
        "run_id": run_id,
        "source_mode": source_mode,
        "_created_at": created_at,
        "_submission_id": submission_id,
        "error": error or original_filename,
    }

# Function to check report contains necessary payload for checks and diagnostics
def _ensure_check_payload(report: Mapping[str, object], findings: List[dict]) -> tuple[List[dict], dict, List[dict]]:
    raw_checks = report.get("checks")
    raw_stats = report.get("check_stats")
    raw_diagnostics = report.get("diagnostics")

    if isinstance(raw_checks, list) and isinstance(raw_stats, Mapping):
        return [dict(check) for check in raw_checks], dict(raw_stats), list(raw_diagnostics or [])

    # If the report does not contain pre-aggregated checks and stats, perform aggregation from findings
    checks, diagnostics = aggregate_findings_to_checks(findings)
    return [check.to_dict() for check in checks], compute_check_stats(checks), diagnostics

# Extract required rules from findings or score evidence, normalising and structuring them for analytics interpretation
def _extract_required_rules(
    findings: List[dict],
    score_evidence: Mapping[str, object] | None = None,
) -> Dict[str, Dict[str, dict]]:
    from ams.analytics.insights import _coerce_float

    required_by_component: Dict[str, Dict[str, dict]] = defaultdict(dict)
    requirements = list((score_evidence or {}).get("requirements", []) or [])

    # If explicit requirements are provided in score evidence, use them to determine required rules and their status 
    if requirements:
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            if requirement.get("required") is False:
                continue
            if str(requirement.get("aggregation_mode") or "") == "CAPPED_PENALTY":
                continue
            
            # Extract and normalise for each requirement
            rule_id = str(requirement.get("requirement_id") or "").strip()
            component = str(requirement.get("component") or "").strip().lower()
            if not rule_id or not component:
                continue
            
            # Normalise status to PASS, FAIL, WARN
            status = str(requirement.get("status") or "").upper()
            if status == "PARTIAL":
                normalized_status = "WARN"
            elif status in {"PASS", "FAIL", "SKIPPED"}:
                normalized_status = status
            else:
                normalized_status = "FAIL"

            """ when multiple requirements reference the same rule_id - 
            FAIL takes precedence over PASS, and WARN takes precedence over PASS but not over FAIL"""
            required_by_component[component][rule_id] = {
                "rule_id": rule_id,
                "status": normalized_status,
                "weight": _coerce_float(requirement.get("weight")),
                "message": str(requirement.get("description") or ""),
                "stage": str(requirement.get("stage") or ""),
                "aggregation_mode": str(requirement.get("aggregation_mode") or ""),
            }

        return {component: dict(rule_map) for component, rule_map in required_by_component.items()}

    # If explicit requirements are not provided, fall back to inferring required rules from findings with specific ID patterns and associated evidence
    for finding in findings:
        finding_id = str(finding.get("id") or "")
        if not finding_id.endswith(".REQ.PASS") and not finding_id.endswith(".REQ.FAIL"):
            continue
        evidence = dict(finding.get("evidence", {}) or {})
        rule_id = str(evidence.get("rule_id") or "").strip()
        if not rule_id:
            continue

        component = str(finding.get("category") or rule_id.split(".", 1)[0] or "").strip().lower()
        if not component:
            continue

        status = "PASS" if finding_id.endswith(".REQ.PASS") else "FAIL"
        current = required_by_component[component].get(rule_id)
        if current is not None and current.get("status") == "FAIL":
            continue
        required_by_component[component][rule_id] = {
            "rule_id": rule_id,
            "status": status,
            "weight": _coerce_float(evidence.get("weight")),
            "message": str(finding.get("message") or ""),
        }

    return {component: dict(rule_map) for component, rule_map in required_by_component.items()}

# Main function to build comprehensive analytics from collected records
def _build_analytics(
    *,
    records: List[dict],
    profile: str,
    assigned_students: Sequence[str],
    scan: Mapping[str, int],
) -> Dict[str, object]:
    from ams.analytics.graphs import _interactive_graphs, _score_composition
    from ams.analytics.insights import _teaching_insight_context, _teaching_insights

    # Use Profile to find relevant components for the assignment
    relevant_components = [component for component in COMPONENT_ORDER if component in get_relevant_components(profile)]
    enriched_records = [_enrich_record(record, profile) for record in records]
    total_records = len(enriched_records)
    small_cohort = _small_cohort_state(total_records)
    overall_scores = [
        float(record["overall"])
        for record in enriched_records
        if isinstance(record.get("overall"), (int, float))
    ]

    # Calculate overall statistics for the cohort's overall scores
    overall_stats: Dict[str, float] | None = None
    if overall_scores:
        overall_stats = {
            "mean": statistics.mean(overall_scores),
            "median": statistics.median(overall_scores),
            "min": min(overall_scores),
            "max": max(overall_scores),
            "standard_deviation": statistics.pstdev(overall_scores) if len(overall_scores) > 1 else 0.0,
        }

    # Calculate grade counts based on enriched records
    grade_counts = {
        "unknown": 0,
        "failing": 0,
        "poor": 0,
        "partial": 0,
        "good": 0,
        "full marks": 0,
    }
    for record in enriched_records:
        grade = str(record.get("grade") or "unknown").lower()
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    # Calculate score distribution buckets for overall scores
    buckets = {"zero": 0, "gt_0_to_0_5": 0, "gt_0_5_to_1": 0, "one": 0}
    for score in overall_scores:
        if score == 0.0:
            buckets["zero"] += 1
        elif 0.0 < score <= 0.5:
            buckets["gt_0_to_0_5"] += 1
        elif 0.5 < score < 1.0:
            buckets["gt_0_5_to_1"] += 1
        elif score == 1.0:
            buckets["one"] += 1

    # Calculate various analytics components for the assignment based on enriched records and relevant components
    coverage = _coverage_summary(enriched_records, assigned_students, scan)
    components = _component_readiness(enriched_records, relevant_components)
    signals, student_issues, runner_limitations = _cohort_signals(enriched_records, total_records)
    top_failing_rules = _top_failing_rules(enriched_records, total_records)
    requirement_coverage = _requirement_coverage(enriched_records, relevant_components, total_records)
    reliability = _reliability_summary(enriched_records, total_records)
    score_composition = _score_composition(enriched_records, total_records)
    needs_attention = _needs_attention(enriched_records)
    interactive_graphs = _interactive_graphs(
        records=enriched_records,
        relevant_components=relevant_components,
        coverage=coverage,
        components=components,
        requirement_coverage=requirement_coverage,
        top_failing_rules=top_failing_rules,
        reliability=reliability,
    )

    for index, signal in enumerate(signals):
        signal["default_visible"] = index < int(small_cohort.get("signal_limit", 6) or 6)
    for index, rule in enumerate(top_failing_rules):
        rule["default_visible"] = index < int(small_cohort.get("rule_limit", 8) or 8)
    overall_summary = {
        "mean": overall_stats.get("mean") if overall_stats else None,
        "median": overall_stats.get("median") if overall_stats else None,
        "min": overall_stats.get("min") if overall_stats else None,
        "max": overall_stats.get("max") if overall_stats else None,
        "standard_deviation": overall_stats.get("standard_deviation") if overall_stats else None,
        "total": total_records,
        "grade_counts": grade_counts,
        "buckets": {
            "No attempt (0%)": buckets["zero"],
            "Partial (1-50%)": buckets["gt_0_to_0_5"],
            "Good partial (51-99%)": buckets["gt_0_5_to_1"],
            "Full marks (100%)": buckets["one"],
        },
    }
    # Build teaching insight context and insights based on the calculated analytics components and enriched records
    teaching_insight_context = _teaching_insight_context(
        profile=profile,
        overall=overall_summary,
        coverage=coverage,
        components=components,
        requirement_coverage=requirement_coverage,
        top_failing_rules=top_failing_rules,
        reliability=reliability,
        needs_attention=needs_attention,
        interactive_graphs=interactive_graphs,
        total_records=total_records,
        small_cohort=small_cohort,
    )
    teaching_insights = _teaching_insights(context=teaching_insight_context)

    # Compile all analytics components into a comprehensive analytics dictionary for the assignment
    return {
        "profile": profile,
        "small_cohort": small_cohort,
        "overall": overall_summary,
        "coverage": coverage,
        "components": components,
        "signals": signals,
        "student_issues": student_issues,
        "runner_limitations": runner_limitations,
        "top_failing_rules": top_failing_rules,
        "requirement_coverage": requirement_coverage,
        "reliability": reliability,
        "score_composition": score_composition,
        "needs_attention": needs_attention,
        "interactive_graphs": interactive_graphs,
        "teaching_insights": teaching_insights,
        "teaching_insight_context": teaching_insight_context,
    }

# Function to summarise coverage of active submissions against assigned students
def _coverage_summary(records: List[dict], assigned_students: Sequence[str], scan: Mapping[str, int]) -> dict:
    assigned_unique = sorted({str(student).strip() for student in assigned_students if str(student).strip()})
    active_students = sorted({str(record.get("student_id") or "").strip() for record in records if record.get("student_id")})
    missing_students = sorted(student for student in assigned_unique if student not in active_students)

    fully_evaluated = sum(1 for record in records if record.get("evaluation_state") == "fully_evaluated")
    partially_evaluated = sum(1 for record in records if record.get("evaluation_state") == "partially_evaluated")
    not_analysable = sum(1 for record in records if record.get("evaluation_state") == "not_analysable")
    assigned_count = len(assigned_unique)
    active_count = len(active_students)

    return {
        "assigned_students": assigned_count,
        "assigned_student_ids": assigned_unique,
        "active_in_scope": len(records),
        "active_students": active_count,
        "active_student_ids": active_students,
        "missing_assigned": len(missing_students),
        "missing_students": missing_students,
        "fully_evaluated": fully_evaluated,
        "partially_evaluated": partially_evaluated,
        "not_analysable": not_analysable,
        "inactive_or_superseded": int(scan.get("inactive_submissions", 0)) + int(scan.get("superseded_records", 0)),
        "inactive_or_superseded_students": sorted(
            {
                str(student_id).strip()
                for student_id in list(scan.get("inactive_student_ids", []) or []) + list(scan.get("superseded_student_ids", []) or [])
                if str(student_id).strip()
            }
        ),
        "coverage_percent": round((active_count / assigned_count) * 100) if assigned_count else 0,
    }

# Function to determine if the cohort is considered "small" based on total active records 
def _small_cohort_state(total_records: int) -> dict:
    enabled = 0 < total_records < SMALL_COHORT_THRESHOLD
    return {
        "enabled": enabled,
        "threshold": SMALL_COHORT_THRESHOLD,
        "rule_limit": 5 if enabled else 8,
        "signal_limit": 4 if enabled else 6,
        "note": (
            f"Small cohort: only {total_records} active submission{' is' if total_records == 1 else 's are'} in scope, "
            "so raw counts should be interpreted before percentages."
            if enabled
            else ""
        ),
    }

# Function to calculate readiness statistics for relevant components based on the scores in the records
def _component_readiness(records: List[Mapping[str, object]], relevant_components: Sequence[str]) -> List[dict]:
    result: List[dict] = []
    for component in relevant_components:
        scores: List[float] = []
        for record in records:
            score = record.get("components", {}).get(component)
            if isinstance(score, (int, float)):
                scores.append(float(score))

        count = len(scores)
        zeros = len([score for score in scores if score == 0])
        half = len([score for score in scores if score == 0.5])
        full = len([score for score in scores if score == 1])
        other = len([score for score in scores if score not in {0, 0.5, 1}])
        avg = sum(scores) / count if count else None
        result.append(
            {
                "component": component,
                "title": REQUIREMENT_TITLES.get(component, component.upper()),
                "average": avg,
                "median": statistics.median(scores) if count else None,
                "count_zero": zeros,
                "count_half": half,
                "count_full": full,
                "count_other": other,
                "total_evaluable": count,
                "pct_zero": (zeros / count * 100) if count else None,
                "pct_half": (half / count * 100) if count else None,
                "pct_full": (full / count * 100) if count else None,
                "pct_other": (other / count * 100) if count else None,
                "skipped": len([1 for record in records if record.get("components", {}).get(component) == "SKIPPED"]),
            }
        )
    return result

# Function to enrich individual records with additional derived information and analytics context for deeper insights
def _enrich_record(record: dict, profile: str) -> dict:
    del profile
    record_copy = dict(record)
    score_evidence = dict(record_copy.get("score_evidence", {}) or {})
    explicit_confidence = dict(score_evidence.get("confidence", {}) or {})
    explicit_review = dict(score_evidence.get("review", {}) or {})
    runtime_flags = _runtime_flags(record_copy)
    problem_outcomes = _problem_outcomes(record_copy)
    matched_rules = [outcome for outcome in problem_outcomes if outcome["status"] in {"FAIL", "WARN", "SKIPPED"}]
    
    # Determine evaluation state, confidence level, grade, flags, reason, severity, and review recommendation
    evaluation_state = _evaluation_state(record_copy, runtime_flags)
    confidence_level, confidence_reasons = _confidence(record_copy, runtime_flags, evaluation_state)
    if explicit_confidence.get("level"):
        confidence_level = str(explicit_confidence.get("level"))
        confidence_reasons = [str(reason) for reason in explicit_confidence.get("reasons", []) if str(reason).strip()]
    overall = record_copy.get("overall")

    if overall is None:
        grade = "unknown"
    elif overall == 0:
        grade = "failing"
    elif overall < 0.5:
        grade = "poor"
    elif overall < 0.7:
        grade = "partial"
    elif overall < 1.0:
        grade = "good"
    else:
        grade = "full marks"

    # Determine flags
    flags: List[str] = []
    if record_copy.get("status") != "ok":
        flags.append("submission not analysable")
    if overall is None:
        flags.append("no score")
    elif isinstance(overall, (int, float)) and overall < 0.5:
        flags.append("low score")
    if runtime_flags["runtime_skipped"]:
        flags.append("runtime checks skipped")
    if runtime_flags["browser_skipped"]:
        flags.append("browser checks skipped")
    if runtime_flags["runtime_issue"]:
        flags.append("runtime issue")
    if runtime_flags["browser_issue"]:
        flags.append("browser issue")
    if runtime_flags["consistency_issue"]:
        flags.append("consistency issue")

    # Determine primary reason and severity for attention
    reason = _primary_reason(record_copy, runtime_flags)
    severity = _attention_severity(record_copy, runtime_flags, confidence_level, overall)
    manual_review = bool(explicit_review.get("recommended")) or severity in {"high", "medium"} or confidence_level != "high"
    review_note = _manual_review_note(record_copy, runtime_flags, confidence_level, overall)
    if explicit_review.get("reasons"):
        review_note = "; ".join(str(reason) for reason in explicit_review.get("reasons", []) if str(reason).strip()) or review_note
    limitation_details = list(confidence_reasons)
    if runtime_flags["runtime_issue"] and "Runtime failures or timeouts were detected." not in limitation_details:
        limitation_details.append("Runtime failures or timeouts were detected.")
    if runtime_flags["browser_issue"] and "Browser failures, timeouts, or console errors were detected." not in limitation_details:
        limitation_details.append("Browser failures, timeouts, or console errors were detected.")

    # Extract matched rule IDs, labels, and messages for the problem outcomes
    matched_rule_ids = [outcome["id"] for outcome in matched_rules]
    matched_rule_labels = [outcome["label"] for outcome in matched_rules]
    matched_rule_messages = [str(outcome.get("message") or "") for outcome in matched_rules if str(outcome.get("message") or "").strip()]
    reason_detail = ", ".join(matched_rule_labels[:2]) if matched_rule_labels else ", ".join(confidence_reasons[:2])

    # Update the record copy with the enriched information for analytics consumption
    record_copy.update(
        {
            "runtime_flags": runtime_flags,
            "problem_outcomes": matched_rules,
            "matched_rule_ids": matched_rule_ids,
            "matched_rule_labels": matched_rule_labels,
            "matched_rule_messages": matched_rule_messages[:3],
            "matched_rules": matched_rules[:5],
            "confidence": confidence_level,
            "confidence_reasons": confidence_reasons,
            "evaluation_state": evaluation_state,
            "grade": grade,
            "flags": flags,
            "reason": reason,
            "reason_detail": reason_detail or reason,
            "severity": severity,
            "manual_review_recommended": manual_review,
            "review_note": review_note,
            "limitation_details": limitation_details[:4],
            "sort_overall": float(overall) if isinstance(overall, (int, float)) else -1.0,
            "sort_grade": GRADE_ORDER.get(grade, 0),
        }
    )
    return record_copy

# Function to determine runtime and browser flags based on findings and environment information in the record
def _runtime_flags(record: Mapping[str, object]) -> dict:
    findings = list(record.get("findings", []) or [])
    finding_ids = {str(finding.get("id") or "") for finding in findings}
    environment = dict(record.get("environment", {}) or {})

    # Determine if any findings indicate skipped checks, issues, or unavailability
    runtime_skipped = any(fid.startswith("BEHAVIOUR.") and "SKIPPED" in fid for fid in finding_ids)
    browser_skipped = any(fid.startswith("BROWSER.") and "SKIPPED" in fid for fid in finding_ids)
    runtime_issue = any(
        fid.startswith("BEHAVIOUR.") and any(token in fid for token in ("FAIL", "TIMEOUT", "ERROR"))
        for fid in finding_ids
    )
    browser_issue = any(
        fid.startswith("BROWSER.") and any(token in fid for token in ("FAIL", "TIMEOUT"))
        for fid in finding_ids
    ) or "BROWSER.CONSOLE_ERRORS_PRESENT" in finding_ids

    runtime_unavailable = not environment.get("behavioural_tests_run", True)
    browser_unavailable = not environment.get("browser_tests_run", True)
    consistency_issue = any(fid.startswith("CONSISTENCY.") for fid in finding_ids)

    return {
        "runtime_skipped": runtime_skipped or runtime_unavailable,
        "browser_skipped": browser_skipped or browser_unavailable,
        "runtime_issue": runtime_issue,
        "browser_issue": browser_issue,
        "runtime_unavailable": runtime_unavailable,
        "browser_unavailable": browser_unavailable,
        "consistency_issue": consistency_issue,
    }

# Function to extract problem outcomes from the record's findings, checks and score evidence
def _problem_outcomes(record: Mapping[str, object]) -> List[dict]:
    from ams.analytics.insights import _coerce_float, _description_for_identifier, _first_non_empty, _label_for_identifier

    outcomes: list[dict] = []
    seen: set[str] = set()
    score_evidence = dict(record.get("score_evidence", {}) or {})

    # If submission status is not "ok", add a problem outcome indicating that the submission is not analysable
    if record.get("status") != "ok":
        outcomes.append(
            {
                "id": "submission.not_analysable",
                "label": "Submission not analysable",
                "status": "FAIL",
                "component": "pipeline",
                "message": str(record.get("error") or record.get("status") or "Automatic analysis did not complete."),
                "weight": None,
            }
        )
        seen.add("submission.not_analysable")

    # Extract problem outcomes from score evidence requirements, prioritising FAIL over PARTIAL/WARN
    for requirement in score_evidence.get("requirements", []) or []:
        if not isinstance(requirement, Mapping):
            continue
        if requirement.get("required") is False:
            continue
        if str(requirement.get("aggregation_mode") or "") == "CAPPED_PENALTY":
            continue
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        if not requirement_id or requirement_id in seen:
            continue
        status = str(requirement.get("status") or "").upper()
        if status not in {"FAIL", "PARTIAL", "SKIPPED"}:
            continue
        seen.add(requirement_id)
        outcomes.append(
            {
                "id": requirement_id,
                "label": str(requirement.get("description") or requirement_id),
                "status": "WARN" if status == "PARTIAL" else status,
                "component": str(requirement.get("component") or "other"),
                "message": str(
                    requirement.get("description")
                    or requirement.get("skipped_reason")
                    or requirement_id
                ),
                "weight": _coerce_float(requirement.get("weight")),
            }
        )

    """ Extract problem outcomes from checks, prioritising FAIL over WARN - 
        Ignoring any checks that end with .REQ.PASS, .REQ.FAIL, or .REQ.SKIPPED"""
    for check in record.get("checks", []) or []:
        status = str(check.get("status") or "").upper()
        if status not in {"FAIL", "WARN"}:
            continue
        check_id = str(check.get("check_id") or "").strip()
        if not check_id or check_id in seen:
            continue
        if check_id.endswith(".REQ.PASS") or check_id.endswith(".REQ.FAIL") or check_id.endswith(".REQ.SKIPPED"):
            continue
        seen.add(check_id)
        outcomes.append(
            {
                "id": check_id,
                "label": _label_for_identifier(check_id),
                "status": status,
                "component": str(check.get("component") or "other"),
                "message": _first_non_empty(check.get("messages", []) or []),
                "weight": _coerce_float(check.get("weight")),
            }
        )

    # Extract problem outcomes from findings with specific ID patterns and severity, prioritising FAIL over WARN over SKIPPED
    for finding in record.get("findings", []) or []:
        finding_id = str(finding.get("id") or "").strip()
        severity = str(finding.get("severity") or "").upper()
        if severity not in {"FAIL", "WARN", "SKIPPED"}:
            continue
        if not (finding_id.startswith("BEHAVIOUR.") or finding_id.startswith("BROWSER.")):
            continue
        if finding_id in seen:
            continue
        seen.add(finding_id)
        outcomes.append(
            {
                "id": finding_id,
                "label": _label_for_identifier(finding_id),
                "status": severity,
                "component": str(finding.get("category") or "runtime"),
                "message": str(finding.get("message") or _description_for_identifier(finding_id)),
                "weight": None,
            }
        )

    outcomes.sort(key=lambda item: (-SEVERITY_PRIORITY.get(item["status"], 0), item["id"]))
    return outcomes

# Function to determine the primary reason for attention based on the record's status, overall score, and runtime/browser flags
def _evaluation_state(record: Mapping[str, object], runtime_flags: Mapping[str, bool]) -> str:
    if record.get("status") != "ok" or record.get("overall") is None:
        return "not_analysable"
    if runtime_flags["runtime_skipped"] or runtime_flags["browser_skipped"] or runtime_flags["runtime_unavailable"] or runtime_flags["browser_unavailable"]:
        return "partially_evaluated"
    return "fully_evaluated"

# Function to determine confidence level and reasons based on the evaluation state and runtime/browser flags
def _confidence(record: Mapping[str, object], runtime_flags: Mapping[str, bool], evaluation_state: str) -> tuple[str, List[str]]:
    reasons: List[str] = []
    if evaluation_state == "not_analysable":
        reasons.append("Automatic analysis did not complete cleanly.")
        if record.get("error"):
            reasons.append(str(record.get("error")))
        return "low", reasons

    if runtime_flags["runtime_skipped"]:
        reasons.append("Runtime checks were skipped or unavailable.")
    if runtime_flags["browser_skipped"]:
        reasons.append("Browser checks were skipped or unavailable.")
    if runtime_flags["runtime_issue"]:
        reasons.append("Runtime checks reported failures or timeouts.")
    if runtime_flags["browser_issue"]:
        reasons.append("Browser checks reported failures, timeouts, or console errors.")

    if runtime_flags["runtime_issue"] or runtime_flags["browser_issue"]:
        return "medium", reasons
    if evaluation_state == "partially_evaluated":
        return "medium", reasons or ["Some checks were skipped, so reliability is reduced."]
    return "high", ["Static, runtime, and browser signals completed without known limitations."]

# Function to determine the severity of attention needed
def _attention_severity(record: Mapping[str, object], runtime_flags: Mapping[str, bool], confidence_level: str, overall: object) -> str:
    if record.get("status") != "ok" or overall is None:
        return "high"
    if isinstance(overall, (int, float)) and overall < 0.5:
        return "high"
    if runtime_flags["runtime_issue"] or runtime_flags["browser_issue"]:
        return "high"
    if confidence_level != "high" or runtime_flags["consistency_issue"]:
        return "medium"
    return "low"

# Function to determine the manual review note
def _manual_review_note(record: Mapping[str, object], runtime_flags: Mapping[str, bool], confidence_level: str, overall: object) -> str:
    if record.get("status") != "ok" or overall is None:
        return "Submission could not be fully analysed automatically."
    if runtime_flags["runtime_skipped"] or runtime_flags["browser_skipped"]:
        return "Some automated checks were skipped, so manual review is recommended."
    if runtime_flags["runtime_issue"] or runtime_flags["browser_issue"]:
        return "Runtime or browser evidence changed how this result should be interpreted."
    if isinstance(overall, (int, float)) and overall < 0.5:
        return "Low score should be checked against the detailed report before release."
    if confidence_level != "high":
        return "Reliability is reduced, so manual review is recommended."
    return "Repeated rubric issues suggest this submission should be reviewed."

# Function to extract records that need attention
def _needs_attention(records: List[Mapping[str, object]]) -> List[dict]:
    from ams.analytics.insights import _first_non_empty

    # Only include records that have flags or are recommended for manual review
    attention: List[dict] = []
    for record in records:
        if not record.get("manual_review_recommended") and not record.get("flags"):
            continue
        attention.append(
            {
                "submission_id": record.get("submission_id"),
                "student_id": record.get("student_id", ""),
                "overall": record.get("overall"),
                "score_percent": round(float(record.get("overall", 0) or 0) * 100) if isinstance(record.get("overall"), (int, float)) else None,
                "grade": record.get("grade", "unknown"),
                "flags": list(record.get("flags", []) or []),
                "reason": record.get("reason", "other"),
                "reason_detail": record.get("reason_detail", ""),
                "severity": record.get("severity", "low"),
                "confidence": record.get("confidence", "high"),
                "evaluation_state": record.get("evaluation_state", "fully_evaluated"),
                "manual_review_recommended": bool(record.get("manual_review_recommended")),
                "review_note": record.get("review_note", ""),
                "limitation_details": list(record.get("limitation_details", []) or []),
                "evidence_excerpt": _first_non_empty(
                    list(record.get("matched_rule_messages", []) or [])
                    + list(record.get("confidence_reasons", []) or [])
                    + [record.get("review_note", "")]
                ),
                "matched_rule_ids": list(record.get("matched_rule_ids", []) or []),
                "matched_rule_labels": list(record.get("matched_rule_labels", []) or []),
                "matched_rule_messages": list(record.get("matched_rule_messages", []) or []),
                "matched_rules": list(record.get("matched_rules", []) or []),
                "run_id": record.get("run_id", ""),
                "source_mode": record.get("source_mode", "mark"),
                "confidence_rank": {"high": 3, "medium": 2, "low": 1}.get(str(record.get("confidence", "high")).lower(), 0),
                "sort_overall": record.get("sort_overall", -1.0),
                "sort_grade": record.get("sort_grade", 0),
            }
        )

    # Sort attention records by severity (high to low), then by overall score (low to high), then by student ID
    attention.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("severity") or "low"), 3),
            float(item.get("sort_overall") or -1.0),
            str(item.get("student_id") or ""),
        )
    )
    return attention

# Function to determine the primary reason for attention
def _primary_reason(record: Mapping[str, object], runtime_flags: Mapping[str, bool]) -> str:
    if record.get("status") != "ok" or record.get("overall") is None:
        return "submission not analysable"
    if runtime_flags["runtime_skipped"] or runtime_flags["browser_skipped"]:
        return "reduced evaluation confidence"
    if runtime_flags["runtime_issue"]:
        return "behavioural runtime issue"
    if runtime_flags["browser_issue"]:
        return "browser runtime issue"

    # Prioritise findings
    findings = record.get("findings", []) or []
    priorities: Sequence[tuple[str, str]] = [
        ("missing", "missing required files"),
        ("SYNTAX", "syntax issue"),
        ("CONSISTENCY.", "consistency issue"),
    ]
    for prefix, label in priorities:
        for finding in findings:
            if finding.get("finding_category") == "missing" and prefix == "missing":
                return label
            if prefix in str(finding.get("id") or ""):
                return label

    overall = record.get("overall")
    if isinstance(overall, (int, float)) and overall < 0.5:
        return "low score"
    return "other"

# Function to summarise cohort signals
def _cohort_signals(records: List[Mapping[str, object]], total: int) -> tuple[List[dict], List[dict], List[dict]]:
    categories: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    runner_limits: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    labels_by_rule: dict[str, str] = {}
    messages_by_rule: dict[str, str] = {}

    # Iterate through records and their problem outcomes
    for record in records:
        student_id = str(record.get("student_id") or "")
        for outcome in record.get("problem_outcomes", []) or []:
            outcome_id = str(outcome.get("id") or "")
            status = str(outcome.get("status") or "")
            labels_by_rule.setdefault(outcome_id, str(outcome.get("label") or outcome_id))
            message = str(outcome.get("message") or "").strip()
            if message:
                messages_by_rule.setdefault(outcome_id, message)
            if outcome_id == "submission.not_analysable":
                runner_limits["behavioural_skipped"].append((student_id, outcome_id, status))
                continue
            if outcome_id.startswith("BEHAVIOUR.") and status == "SKIPPED":
                runner_limits["behavioural_skipped"].append((student_id, outcome_id, status))
                continue
            if outcome_id.startswith("BROWSER.") and status == "SKIPPED":
                runner_limits["browser_skipped"].append((student_id, outcome_id, status))
                continue
            if status not in {"WARN", "FAIL"}:
                continue
            if outcome_id.startswith("BROWSER."):
                categories["browser_runtime"].append((student_id, outcome_id, status))
            elif outcome_id.startswith("BEHAVIOUR."):
                categories["behavioural_runtime"].append((student_id, outcome_id, status))
            elif outcome_id.startswith("CONSISTENCY."):
                categories["consistency"].append((student_id, outcome_id, status))
            elif ".MISSING_FILES" in outcome_id:
                if outcome_id.startswith("PHP.") or outcome_id.startswith("SQL."):
                    categories["missing_backend"].append((student_id, outcome_id, status))
                else:
                    categories["missing_frontend"].append((student_id, outcome_id, status))
            elif "SYNTAX" in outcome_id or "PARSE" in outcome_id:
                categories["syntax"].append((student_id, outcome_id, status))
            else:
                categories["other"].append((student_id, outcome_id, status))

    # Helper function to summarise signals for a given category
    def _summary(category_name: str, entries: list[tuple[str, str, str]], kind: str) -> dict:
        if not entries:
            return {}
        students = sorted({student_id for student_id, _, _ in entries if student_id})
        rules = sorted({rule_id for _, rule_id, _ in entries})
        worst_status = "FAIL" if any(status == "FAIL" for _, _, status in entries) else "WARN"
        evidence_examples = [messages_by_rule[rule_id] for rule_id in rules if rule_id in messages_by_rule][:2]
        return {
            "id": category_name,
            "kind": kind,
            "title": category_name.replace("_", " ").title(),
            "description": SIGNAL_DESCRIPTIONS.get(category_name, ""),
            "incident_count": len(entries),
            "incident_unit": "limitation incidents" if kind == "reliability" else "signal incidents",
            "students_affected": len(students),
            "submissions_affected": len(students),
            "percent": (len(students) / total * 100) if total else 0,
            "related_rules": rules,
            "related_rule_labels": [labels_by_rule.get(rule_id, rule_id) for rule_id in rules[:4]],
            "affected_students": students,
            "examples": students[:3],
            "evidence_examples": evidence_examples,
            "severity": worst_status,
        }

    # Summarise signals for student issues and runner limitations
    student_sections = [
        _summary("missing_backend", categories["missing_backend"], "cohort_issue"),
        _summary("missing_frontend", categories["missing_frontend"], "cohort_issue"),
        _summary("syntax", categories["syntax"], "cohort_issue"),
        _summary("behavioural_runtime", categories["behavioural_runtime"], "cohort_issue"),
        _summary("browser_runtime", categories["browser_runtime"], "cohort_issue"),
        _summary("consistency", categories["consistency"], "cohort_issue"),
        _summary("other", categories["other"], "cohort_issue"),
    ]
    student_sections = [section for section in student_sections if section]

    runner_sections = [
        _summary("behavioural_skipped", runner_limits["behavioural_skipped"], "reliability"),
        _summary("browser_skipped", runner_limits["browser_skipped"], "reliability"),
    ]
    runner_sections = [section for section in runner_sections if section]

    signals = sorted(
        student_sections + runner_sections,
        key=lambda item: (-int(item.get("students_affected", 0)), item.get("title", "")),
    )
    return signals, student_sections, runner_sections

# Function to identify the top failing rules across the cohort
def _top_failing_rules(records: List[Mapping[str, object]], total: int) -> List[dict]:
    from ams.analytics.graphs import _rule_category
    from ams.analytics.insights import _coerce_float

    # Aggregate rule outcomes across all records to determine the most common and impactful rules
    rules: dict[str, dict] = {}
    for record in records:
        student_id = str(record.get("student_id") or "")
        for outcome in record.get("problem_outcomes", []) or []:
            status = str(outcome.get("status") or "")
            if status not in {"FAIL", "WARN"}:
                continue
            outcome_id = str(outcome.get("id") or "")
            if outcome_id == "submission.not_analysable":
                continue
            entry = rules.setdefault(
                outcome_id,
                {
                    "rule_id": outcome_id,
                    "label": str(outcome.get("label") or outcome_id),
                    "component": str(outcome.get("component") or "other"),
                    "students": set(),
                    "statuses": set(),
                    "weights": [],
                    "messages": [],
                    "fail_incidents": 0,
                    "warning_incidents": 0,
                    "confidence_affecting": outcome_id.startswith("BEHAVIOUR.") or outcome_id.startswith("BROWSER."),
                },
            )
            entry["students"].add(student_id)
            entry["statuses"].add(status)
            if status == "FAIL":
                entry["fail_incidents"] += 1
            elif status == "WARN":
                entry["warning_incidents"] += 1
            weight = _coerce_float(outcome.get("weight"))
            if weight is not None:
                entry["weights"].append(weight)
            message = str(outcome.get("message") or "")
            if message and message not in entry["messages"]:
                entry["messages"].append(message)

    # Compile the results into a list of dicts
    results: List[dict] = []
    for outcome_id, entry in rules.items():
        students = sorted(student for student in entry["students"] if student)
        worst_status = "FAIL" if "FAIL" in entry["statuses"] else "WARN"
        weights = list(entry["weights"])
        submissions_affected = len(students)
        impact_type = (
            "weighted"
            if weights
            else ("fail_level" if worst_status == "FAIL" else "warning_level")
        )
        results.append(
            {
                "rule_id": outcome_id,
                "label": entry["label"],
                "component": entry["component"],
                "category": _rule_category(outcome_id),
                "severity": worst_status,
                "students_affected": submissions_affected,
                "submissions_affected": submissions_affected,
                "percent": (submissions_affected / total * 100) if total else 0,
                "incident_count": int(entry["fail_incidents"]) + int(entry["warning_incidents"]),
                "fail_incidents": int(entry["fail_incidents"]),
                "warning_incidents": int(entry["warning_incidents"]),
                "impact_type": impact_type,
                "score_impact": (
                    f"Weighted rule ({max(weights):.2f})"
                    if weights
                    else ("Fail-level issue" if worst_status == "FAIL" else "Warning-level issue")
                ),
                "confidence_affecting": bool(entry["confidence_affecting"]),
                "examples": students[:3],
                "affected_students": students,
                "messages": entry["messages"][:2],
            }
        )

    # Sort results
    results.sort(
        key=lambda item: (
            -int(item.get("students_affected", 0)),
            -SEVERITY_PRIORITY.get(str(item.get("severity") or "WARN"), 0),
            -int(item.get("incident_count", 0)),
            str(item.get("rule_id") or ""),
        )
    )
    return results

# Function to calculate requirement coverage for relevant components
def _requirement_coverage(records: List[Mapping[str, object]], relevant_components: Sequence[str], total: int) -> List[dict]:
    coverage_rows: List[dict] = []
    for component in relevant_components:
        met_students: list[str] = []
        partial_students: list[str] = []
        unmet_students: list[str] = []
        not_evaluable_students: list[str] = []
        rule_count = 0

        for record in records:
            student_id = str(record.get("student_id") or "")
            outcomes = dict((record.get("required_rules", {}) or {}).get(component, {}) or {})
            rule_count = max(rule_count, len(outcomes))

            if record.get("status") != "ok" or record.get("overall") is None:
                not_evaluable_students.append(student_id)
                continue
            if not outcomes:
                not_evaluable_students.append(student_id)
                continue

            passed = sum(1 for outcome in outcomes.values() if outcome.get("status") == "PASS")
            partial = sum(1 for outcome in outcomes.values() if outcome.get("status") == "WARN")
            skipped = sum(1 for outcome in outcomes.values() if outcome.get("status") == "SKIPPED")
            total_rules = len(outcomes)
            evaluable_rules = total_rules - skipped
            if evaluable_rules <= 0:
                not_evaluable_students.append(student_id)
            elif passed == evaluable_rules:
                met_students.append(student_id)
            elif partial > 0 or passed > 0:
                partial_students.append(student_id)
            elif passed == 0:
                unmet_students.append(student_id)

        coverage_rows.append(
            {
                "component": component,
                "title": REQUIREMENT_TITLES.get(component, component.upper()),
                "rule_count": rule_count,
                "students_met": len(met_students),
                "students_partial": len(partial_students),
                "students_unmet": len(unmet_students),
                "students_not_evaluable": len(not_evaluable_students),
                "met_percent": (len(met_students) / total * 100) if total else 0,
                "met_students": met_students,
                "partial_students": partial_students,
                "unmet_students": unmet_students,
                "not_evaluable_students": not_evaluable_students,
                "met_examples": met_students[:3],
                "unmet_examples": unmet_students[:3],
            }
        )
    return coverage_rows

# Function to summarise reliability and limitations of the automated analysis
def _reliability_summary(records: List[Mapping[str, object]], total: int) -> dict:
    fully_evaluated = sum(1 for record in records if record.get("evaluation_state") == "fully_evaluated")
    partially_evaluated = sum(1 for record in records if record.get("evaluation_state") == "partially_evaluated")
    not_analysable = sum(1 for record in records if record.get("evaluation_state") == "not_analysable")
    runtime_skipped = sum(1 for record in records if record.get("runtime_flags", {}).get("runtime_skipped"))
    browser_skipped = sum(1 for record in records if record.get("runtime_flags", {}).get("browser_skipped"))
    runtime_issue_submissions = sum(1 for record in records if record.get("runtime_flags", {}).get("runtime_issue"))
    browser_issue_submissions = sum(1 for record in records if record.get("runtime_flags", {}).get("browser_issue"))
    manual_review = sum(1 for record in records if record.get("manual_review_recommended"))
    high_confidence = sum(1 for record in records if record.get("confidence") == "high")
    medium_confidence = sum(1 for record in records if record.get("confidence") == "medium")
    low_confidence = sum(1 for record in records if record.get("confidence") == "low")

    # Create a breakdown of limitation incidents by category
    limitation_breakdown: List[dict] = []
    for category_id, label, count in [
        ("analysis_failure", "Not analysable submissions", not_analysable),
        ("runtime_skipped", "Runtime checks skipped or unavailable", runtime_skipped),
        ("browser_skipped", "Browser checks skipped or unavailable", browser_skipped),
        ("runtime_issue", "Runtime failures or timeouts", runtime_issue_submissions),
        ("browser_issue", "Browser failures, timeouts, or console errors", browser_issue_submissions),
    ]:
        if count:
            limitation_breakdown.append(
                {
                    "id": category_id,
                    "label": label,
                    "incident_count": int(count),
                    "incident_unit": "limitation incidents",
                }
            )
    
    return {
        "fully_evaluated": fully_evaluated,
        "fully_evaluated_submissions": fully_evaluated,
        "partially_evaluated": partially_evaluated,
        "partially_evaluated_submissions": partially_evaluated,
        "not_analysable": not_analysable,
        "not_analysable_submissions": not_analysable,
        "runtime_skipped": runtime_skipped,
        "runtime_issue_submissions": runtime_issue_submissions,
        "browser_skipped": browser_skipped,
        "browser_issue_submissions": browser_issue_submissions,
        "browser_limited": browser_skipped + browser_issue_submissions,
        "manual_review": manual_review,
        "manual_review_submissions": manual_review,
        "limitation_incidents": sum(item["incident_count"] for item in limitation_breakdown),
        "limitation_categories": len(limitation_breakdown),
        "limitation_breakdown": limitation_breakdown,
        "confidence": {
            "high": high_confidence,
            "medium": medium_confidence,
            "low": low_confidence,
            "high_percent": (high_confidence / total * 100) if total else 0,
            "medium_percent": (medium_confidence / total * 100) if total else 0,
            "low_percent": (low_confidence / total * 100) if total else 0,
        },
    }

__all__ = ["generate_assignment_analytics", "generate_student_assignment_analytics", "FINDING_LABELS"]
