"""Assignment-level analytics engine."""
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats
from ams.core.db import get_assignment
from ams.core.profiles import get_relevant_components
from ams.io.metadata import MetadataValidator
from ams.io.web_storage import get_runs_root, load_run_info

logger = logging.getLogger(__name__)

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


def generate_assignment_analytics(
    assignment_id: str,
    *,
    app: Any | None = None,
) -> Dict[str, object]:
    assignment = get_assignment(assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment '{assignment_id}' not found")

    profile = assignment.get("profile", "frontend")
    if app is not None:
        runs_root = get_runs_root(app)
    else:
        runs_root = Path("ams_web_runs")
        runs_root.mkdir(parents=True, exist_ok=True)

    records, scan = _collect_assignment_records(runs_root, assignment_id)
    analytics = _build_analytics(
        records=records,
        profile=profile,
        assigned_students=assignment.get("assigned_students", []) or [],
        scan=scan,
    )
    analytics["assignment_id"] = assignment_id
    analytics["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    analytics["submission_count"] = len(records)
    analytics["teaching_insight_context"]["assignment_id"] = assignment_id
    return analytics


def _assignment_search_root(runs_root: Path, assignment_id: str) -> Path:
    candidate = runs_root / MetadataValidator.sanitize_identifier(assignment_id)
    return candidate if candidate.exists() else runs_root


def _collect_assignment_records(runs_root: Path, assignment_id: str) -> tuple[List[dict], dict]:
    latest_by_student: dict[str, tuple[tuple[str, str, str], dict]] = {}
    search_root = _assignment_search_root(runs_root, assignment_id)
    scan = {
        "candidate_records": 0,
        "inactive_submissions": 0,
        "inactive_student_ids": set(),
        "superseded_student_ids": set(),
    }

    for run_info_path in search_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        run_info = load_run_info(run_dir)
        if not run_info or run_info.get("assignment_id") != assignment_id:
            continue

        if run_info.get("active") is False:
            inactive_count, inactive_students = _count_run_submissions(run_dir, run_info, assignment_id)
            scan["inactive_submissions"] += inactive_count
            scan["inactive_student_ids"].update(inactive_students)
            continue

        records_from_run = _records_from_run(run_dir, run_info, assignment_id)
        scan["candidate_records"] += len(records_from_run)

        for record in records_from_run:
            student_id = str(record.get("student_id") or "").strip()
            if not student_id:
                continue
            sort_key = (
                str(record.get("_created_at") or ""),
                str(record.get("run_id") or ""),
                str(record.get("_submission_id") or ""),
            )
            current = latest_by_student.get(student_id)
            if current is None or sort_key > current[0]:
                if current is not None:
                    scan["superseded_student_ids"].add(student_id)
                latest_by_student[student_id] = (sort_key, record)
            else:
                scan["superseded_student_ids"].add(student_id)

    records = [record for _, record in latest_by_student.values()]
    for record in records:
        record.pop("_created_at", None)
        record.pop("_submission_id", None)
    records.sort(key=lambda rec: rec.get("student_id", ""))
    scan["superseded_records"] = max(scan["candidate_records"] - len(records), 0)
    scan["inactive_student_ids"] = sorted(str(student_id) for student_id in scan["inactive_student_ids"] if str(student_id).strip())
    scan["superseded_student_ids"] = sorted(str(student_id) for student_id in scan["superseded_student_ids"] if str(student_id).strip())
    return records, scan


def _count_run_submissions(run_dir: Path, run_info: Mapping[str, object], assignment_id: str) -> tuple[int, List[str]]:
    mode = str(run_info.get("mode") or "")
    if mode == "mark":
        student_id = str(run_info.get("student_id") or "").strip()
        return 1, [student_id] if student_id else []
    if mode != "batch":
        return 0, []

    summary_path = run_dir / "batch_summary.json"
    summary_data = _load_json(summary_path) if summary_path.exists() else None
    if summary_data is not None:
        matching_entries = [
            entry
            for entry in summary_data.get("records", []) or []
            if str(entry.get("assignment_id") or run_info.get("assignment_id") or "") == assignment_id
            and _batch_entry_is_active(entry)
        ]
        return len(matching_entries), [
            str(entry.get("student_id") or "").strip()
            for entry in matching_entries
            if str(entry.get("student_id") or "").strip()
        ]

    pending = run_info.get("pending_submissions", []) or []
    if isinstance(pending, list):
        matching_entries = [
            entry
            for entry in pending
            if str(entry.get("assignment_id") or run_info.get("assignment_id") or "") == assignment_id
        ]
        return len(matching_entries), [
            str(entry.get("student_id") or "").strip()
            for entry in matching_entries
            if str(entry.get("student_id") or "").strip()
        ]
    return 0, []


def _records_from_run(run_dir: Path, run_info: Mapping[str, object], assignment_id: str) -> List[dict]:
    mode = str(run_info.get("mode") or "")
    if mode == "mark":
        record = _record_from_mark_run(run_dir, run_info, assignment_id)
        return [record] if record is not None else []
    if mode == "batch":
        return _records_from_submission_batch(run_dir, run_info, assignment_id)
    return []


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_submission_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"", "ok", "success", "succeeded", "completed", "complete"}:
        return "ok"
    if status in {"pending", "queued", "running"}:
        return "pending"
    return status


def _batch_entry_is_active(entry: Mapping[str, object]) -> bool:
    if entry.get("invalid") is True:
        return False
    return not _normalize_submission_status(entry.get("status")).startswith("invalid")


def _record_from_mark_run(run_dir: Path, run_info: Mapping[str, object], assignment_id: str) -> dict | None:
    report_path = run_dir / "report.json"
    report = _load_json(report_path)
    student_id = str(run_info.get("student_id") or "").strip()

    if report is not None:
        submission_meta = report.get("metadata", {}).get("submission_metadata", {}) or {}
        record_assignment_id = str(submission_meta.get("assignment_id") or run_info.get("assignment_id") or "")
        if record_assignment_id != assignment_id:
            return None
        student_id = str(submission_meta.get("student_id") or student_id).strip()
        if not student_id:
            return None
        return _report_to_record(
            report=report,
            student_id=student_id,
            assignment_id=assignment_id,
            report_path=report_path,
            run_id=str(run_info.get("id") or run_dir.name),
            created_at=str(run_info.get("created_at") or ""),
            submission_id=str(run_info.get("id") or run_dir.name),
            status=_normalize_submission_status(run_info.get("status") or "ok"),
            source_mode="mark",
        )

    if str(run_info.get("assignment_id") or "") != assignment_id or not student_id:
        return None
    return _empty_record(
        student_id=student_id,
        assignment_id=assignment_id,
        run_id=str(run_info.get("id") or run_dir.name),
        created_at=str(run_info.get("created_at") or ""),
        submission_id=str(run_info.get("id") or run_dir.name),
        status=_normalize_submission_status(run_info.get("status") or "failed"),
        report_path="",
        original_filename=str(run_info.get("original_filename") or ""),
        error=str(run_info.get("error") or ""),
        source_mode="mark",
    )


def _records_from_submission_batch(run_dir: Path, run_info: Mapping[str, object], assignment_id: str) -> List[dict]:
    summary_path = run_dir / "batch_summary.json"
    summary_data = _load_json(summary_path)
    if summary_data is None:
        pending = run_info.get("pending_submissions", []) or []
        if not isinstance(pending, list):
            return []
        return [
            _empty_record(
                student_id=str(entry.get("student_id") or "").strip(),
                assignment_id=assignment_id,
                run_id=str(run_info.get("id") or run_dir.name),
                created_at=str(run_info.get("created_at") or ""),
                submission_id=str(entry.get("submission_id") or entry.get("student_id") or ""),
                status=_normalize_submission_status(entry.get("status") or "pending"),
                report_path="",
                original_filename=str(entry.get("original_filename") or ""),
                error=str(entry.get("error") or ""),
                source_mode="batch",
            )
            for entry in pending
            if str(entry.get("assignment_id") or run_info.get("assignment_id") or "") == assignment_id
            and str(entry.get("student_id") or "").strip()
        ]

    records: List[dict] = []
    created_at = str(run_info.get("created_at") or "")
    run_id = str(run_info.get("id") or run_dir.name)

    for entry in summary_data.get("records", []) or []:
        entry_assignment_id = str(entry.get("assignment_id") or run_info.get("assignment_id") or "")
        if entry_assignment_id != assignment_id:
            continue
        if not _batch_entry_is_active(entry):
            continue
        student_id = str(entry.get("student_id") or "").strip()
        if not student_id:
            continue

        report_path = _resolve_batch_report_path(run_dir, entry)
        report = _load_json(report_path) if report_path is not None else None
        if report is not None:
            submission_meta = report.get("metadata", {}).get("submission_metadata", {}) or {}
            student_id = str(submission_meta.get("student_id") or student_id).strip()
            if not student_id:
                continue
            record = _report_to_record(
                report=report,
                student_id=student_id,
                assignment_id=assignment_id,
                report_path=report_path,
                run_id=run_id,
                created_at=created_at,
                submission_id=str(entry.get("id") or ""),
                status=_normalize_submission_status(entry.get("status") or "ok"),
                source_mode="batch",
            )
            if entry.get("error"):
                record["error"] = str(entry.get("error"))
            records.append(record)
            continue

        records.append(
            _empty_record(
                student_id=student_id,
                assignment_id=assignment_id,
                run_id=run_id,
                created_at=created_at,
                submission_id=str(entry.get("id") or student_id),
                status=_normalize_submission_status(entry.get("status") or "failed"),
                report_path=str(report_path) if report_path is not None else "",
                original_filename=str(entry.get("original_filename") or ""),
                error=str(entry.get("error") or entry.get("validation_error") or ""),
                source_mode="batch",
                overall=entry.get("overall"),
                components=entry.get("components", {}),
            )
        )

    return records


def _resolve_batch_report_path(run_dir: Path, entry: Mapping[str, object]) -> Path | None:
    raw_report_path = entry.get("report_path")
    if isinstance(raw_report_path, str) and raw_report_path:
        candidate = Path(raw_report_path)
        if candidate.exists():
            return candidate

    submission_id = entry.get("id")
    if isinstance(submission_id, str) and submission_id:
        candidate = run_dir / "runs" / submission_id / "report.json"
        if candidate.exists():
            return candidate
    return None


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
    scores = report.get("scores", {}) or {}
    component_scores = scores.get("by_component", {}) or {}
    findings = list(report.get("findings", []) or [])
    checks, check_stats, diagnostics = _ensure_check_payload(report, findings)
    submission_meta = report.get("metadata", {}).get("submission_metadata", {}) or {}
    identity_meta = report.get("metadata", {}).get("student_identity", {}) or {}

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


def _ensure_check_payload(report: Mapping[str, object], findings: List[dict]) -> tuple[List[dict], dict, List[dict]]:
    raw_checks = report.get("checks")
    raw_stats = report.get("check_stats")
    raw_diagnostics = report.get("diagnostics")

    if isinstance(raw_checks, list) and isinstance(raw_stats, Mapping):
        return [dict(check) for check in raw_checks], dict(raw_stats), list(raw_diagnostics or [])

    checks, diagnostics = aggregate_findings_to_checks(findings)
    return [check.to_dict() for check in checks], compute_check_stats(checks), diagnostics


def _extract_required_rules(
    findings: List[dict],
    score_evidence: Mapping[str, object] | None = None,
) -> Dict[str, Dict[str, dict]]:
    required_by_component: Dict[str, Dict[str, dict]] = defaultdict(dict)
    requirements = list((score_evidence or {}).get("requirements", []) or [])

    if requirements:
        for requirement in requirements:
            if not isinstance(requirement, Mapping):
                continue
            if requirement.get("required") is False:
                continue
            if str(requirement.get("aggregation_mode") or "") == "CAPPED_PENALTY":
                continue

            rule_id = str(requirement.get("requirement_id") or "").strip()
            component = str(requirement.get("component") or "").strip().lower()
            if not rule_id or not component:
                continue

            status = str(requirement.get("status") or "").upper()
            if status == "PARTIAL":
                normalized_status = "WARN"
            elif status in {"PASS", "FAIL", "SKIPPED"}:
                normalized_status = status
            else:
                normalized_status = "FAIL"

            required_by_component[component][rule_id] = {
                "rule_id": rule_id,
                "status": normalized_status,
                "weight": _coerce_float(requirement.get("weight")),
                "message": str(requirement.get("description") or ""),
                "stage": str(requirement.get("stage") or ""),
                "aggregation_mode": str(requirement.get("aggregation_mode") or ""),
            }

        return {component: dict(rule_map) for component, rule_map in required_by_component.items()}

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


def _build_analytics(
    *,
    records: List[dict],
    profile: str,
    assigned_students: Sequence[str],
    scan: Mapping[str, int],
) -> Dict[str, object]:
    relevant_components = [component for component in COMPONENT_ORDER if component in get_relevant_components(profile)]
    enriched_records = [_enrich_record(record, profile) for record in records]
    total_records = len(enriched_records)
    small_cohort = _small_cohort_state(total_records)
    overall_scores = [
        float(record["overall"])
        for record in enriched_records
        if isinstance(record.get("overall"), (int, float))
    ]

    overall_stats: Dict[str, float] | None = None
    if overall_scores:
        overall_stats = {
            "mean": statistics.mean(overall_scores),
            "median": statistics.median(overall_scores),
            "min": min(overall_scores),
            "max": max(overall_scores),
            "standard_deviation": statistics.pstdev(overall_scores) if len(overall_scores) > 1 else 0.0,
        }

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
    teaching_insight_context = _teaching_insight_context(
        profile=profile,
        coverage=coverage,
        requirement_coverage=requirement_coverage,
        top_failing_rules=top_failing_rules,
        reliability=reliability,
        total_records=total_records,
        small_cohort=small_cohort,
    )
    teaching_insights = _teaching_insights(
        context=teaching_insight_context,
    )

    return {
        "profile": profile,
        "small_cohort": small_cohort,
        "overall": {
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
        },
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


def _enrich_record(record: dict, profile: str) -> dict:
    del profile
    record_copy = dict(record)
    score_evidence = dict(record_copy.get("score_evidence", {}) or {})
    explicit_confidence = dict(score_evidence.get("confidence", {}) or {})
    explicit_review = dict(score_evidence.get("review", {}) or {})
    runtime_flags = _runtime_flags(record_copy)
    problem_outcomes = _problem_outcomes(record_copy)
    matched_rules = [outcome for outcome in problem_outcomes if outcome["status"] in {"FAIL", "WARN", "SKIPPED"}]

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

    matched_rule_ids = [outcome["id"] for outcome in matched_rules]
    matched_rule_labels = [outcome["label"] for outcome in matched_rules]
    matched_rule_messages = [str(outcome.get("message") or "") for outcome in matched_rules if str(outcome.get("message") or "").strip()]
    reason_detail = ", ".join(matched_rule_labels[:2]) if matched_rule_labels else ", ".join(confidence_reasons[:2])

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


def _runtime_flags(record: Mapping[str, object]) -> dict:
    findings = list(record.get("findings", []) or [])
    finding_ids = {str(finding.get("id") or "") for finding in findings}
    environment = dict(record.get("environment", {}) or {})

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


def _problem_outcomes(record: Mapping[str, object]) -> List[dict]:
    outcomes: list[dict] = []
    seen: set[str] = set()
    score_evidence = dict(record.get("score_evidence", {}) or {})

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


def _evaluation_state(record: Mapping[str, object], runtime_flags: Mapping[str, bool]) -> str:
    if record.get("status") != "ok" or record.get("overall") is None:
        return "not_analysable"
    if runtime_flags["runtime_skipped"] or runtime_flags["browser_skipped"] or runtime_flags["runtime_unavailable"] or runtime_flags["browser_unavailable"]:
        return "partially_evaluated"
    return "fully_evaluated"


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


def _needs_attention(records: List[Mapping[str, object]]) -> List[dict]:
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

    attention.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("severity") or "low"), 3),
            float(item.get("sort_overall") or -1.0),
            str(item.get("student_id") or ""),
        )
    )
    return attention


def _primary_reason(record: Mapping[str, object], runtime_flags: Mapping[str, bool]) -> str:
    if record.get("status") != "ok" or record.get("overall") is None:
        return "submission not analysable"
    if runtime_flags["runtime_skipped"] or runtime_flags["browser_skipped"]:
        return "reduced evaluation confidence"
    if runtime_flags["runtime_issue"]:
        return "behavioural runtime issue"
    if runtime_flags["browser_issue"]:
        return "browser runtime issue"

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


def _cohort_signals(records: List[Mapping[str, object]], total: int) -> tuple[List[dict], List[dict], List[dict]]:
    categories: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    runner_limits: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    labels_by_rule: dict[str, str] = {}
    messages_by_rule: dict[str, str] = {}

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


def _top_failing_rules(records: List[Mapping[str, object]], total: int) -> List[dict]:
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

    results.sort(
        key=lambda item: (
            -int(item.get("students_affected", 0)),
            -SEVERITY_PRIORITY.get(str(item.get("severity") or "WARN"), 0),
            -int(item.get("incident_count", 0)),
            str(item.get("rule_id") or ""),
        )
    )
    return results


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


def _interactive_graphs(
    *,
    records: List[Mapping[str, object]],
    relevant_components: Sequence[str],
    coverage: Mapping[str, object],
    components: Sequence[Mapping[str, object]],
    requirement_coverage: Sequence[Mapping[str, object]],
    top_failing_rules: Sequence[Mapping[str, object]],
    reliability: Mapping[str, object],
) -> dict:
    student_index = {
        str(record.get("student_id") or ""): _student_graph_snapshot(record)
        for record in records
        if str(record.get("student_id") or "").strip()
    }
    histogram = _build_mark_distribution_histogram(records)

    component_rows: List[dict] = []
    for component_summary in components:
        component = str(component_summary.get("component") or "")
        if not component:
            continue
        state_students = {
            "zero": [],
            "half": [],
            "full": [],
            "other": [],
            "not_scored": [],
        }
        related_rule_ids: set[str] = set()
        for record in records:
            student_id = str(record.get("student_id") or "").strip()
            if not student_id:
                continue
            score = (record.get("components", {}) or {}).get(component)
            if score == 0:
                state_students["zero"].append(student_id)
            elif score == 0.5:
                state_students["half"].append(student_id)
            elif score == 1:
                state_students["full"].append(student_id)
            elif isinstance(score, (int, float)):
                state_students["other"].append(student_id)
            else:
                state_students["not_scored"].append(student_id)
            for outcome in record.get("problem_outcomes", []) or []:
                if str(outcome.get("component") or "").lower() == component and str(outcome.get("status") or "") in {"FAIL", "WARN"}:
                    related_rule_ids.add(str(outcome.get("id") or ""))
        component_rows.append(
            {
                "component": component,
                "title": str(component_summary.get("title") or component.upper()),
                "average_percent": round(float(component_summary.get("average") or 0) * 100, 2)
                if component_summary.get("average") is not None
                else None,
                "total_evaluable": int(component_summary.get("total_evaluable", 0) or 0),
                "segments": [
                    _graph_segment(
                        segment_id=f"{component}_zero",
                        label="Score 0",
                        count=len(state_students["zero"]),
                        total=len(records),
                        student_ids=state_students["zero"],
                    ),
                    _graph_segment(
                        segment_id=f"{component}_half",
                        label="Score 0.5",
                        count=len(state_students["half"]),
                        total=len(records),
                        student_ids=state_students["half"],
                    ),
                    _graph_segment(
                        segment_id=f"{component}_full",
                        label="Score 1",
                        count=len(state_students["full"]),
                        total=len(records),
                        student_ids=state_students["full"],
                    ),
                    _graph_segment(
                        segment_id=f"{component}_other",
                        label="Other scored states",
                        count=len(state_students["other"]),
                        total=len(records),
                        student_ids=state_students["other"],
                    ),
                    _graph_segment(
                        segment_id=f"{component}_not_scored",
                        label="Not scored",
                        count=len(state_students["not_scored"]),
                        total=len(records),
                        student_ids=state_students["not_scored"],
                    ),
                ],
                "related_rule_ids": sorted(rule_id for rule_id in related_rule_ids if rule_id),
            }
        )

    requirement_rows: List[dict] = []
    for row in requirement_coverage:
        component = str(row.get("component") or "")
        if not component:
            continue
        requirement_rows.append(
            {
                "component": component,
                "title": str(row.get("title") or component.upper()),
                "rule_count": int(row.get("rule_count", 0) or 0),
                "cells": [
                    _graph_segment(
                        segment_id=f"{component}_met",
                        label="Met",
                        count=int(row.get("students_met", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("met_students", []) or []),
                    ),
                    _graph_segment(
                        segment_id=f"{component}_partial",
                        label="Partial",
                        count=int(row.get("students_partial", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("partial_students", []) or []),
                    ),
                    _graph_segment(
                        segment_id=f"{component}_unmet",
                        label="Unmet",
                        count=int(row.get("students_unmet", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("unmet_students", []) or []),
                    ),
                    _graph_segment(
                        segment_id=f"{component}_not_evaluable",
                        label="Not evaluable",
                        count=int(row.get("students_not_evaluable", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("not_evaluable_students", []) or []),
                    ),
                ],
            }
        )

    reliability_groups = [
        {
            "id": "evaluation_state",
            "label": "Evaluation state",
            "segments": [
                _graph_segment(
                    segment_id="fully_evaluated",
                    label="Fully evaluated",
                    count=int(reliability.get("fully_evaluated", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "fully_evaluated" and str(record.get("student_id") or "").strip()
                    ],
                ),
                _graph_segment(
                    segment_id="partially_evaluated",
                    label="Partially evaluated",
                    count=int(reliability.get("partially_evaluated", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "partially_evaluated" and str(record.get("student_id") or "").strip()
                    ],
                ),
                _graph_segment(
                    segment_id="not_analysable",
                    label="Not analysable",
                    count=int(reliability.get("not_analysable", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "not_analysable" and str(record.get("student_id") or "").strip()
                    ],
                ),
            ],
        },
        {
            "id": "confidence",
            "label": "Confidence level",
            "segments": [
                _graph_segment(
                    segment_id="confidence_high",
                    label="High confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("high", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "high" and str(record.get("student_id") or "").strip()
                    ],
                ),
                _graph_segment(
                    segment_id="confidence_medium",
                    label="Medium confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("medium", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "medium" and str(record.get("student_id") or "").strip()
                    ],
                ),
                _graph_segment(
                    segment_id="confidence_low",
                    label="Low confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("low", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "low" and str(record.get("student_id") or "").strip()
                    ],
                ),
            ],
        },
    ]

    limitation_rows = [
        _graph_segment(
            segment_id="manual_review",
            label="Manual review recommended",
            count=int(reliability.get("manual_review", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("manual_review_recommended") and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="runtime_skipped",
            label="Runtime checks skipped or unavailable",
            count=int(reliability.get("runtime_skipped", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("runtime_skipped") and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="browser_skipped",
            label="Browser checks skipped or unavailable",
            count=int(reliability.get("browser_skipped", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("browser_skipped") and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="runtime_issue",
            label="Runtime failures or timeouts",
            count=int(reliability.get("runtime_issue_submissions", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("runtime_issue") and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="browser_issue",
            label="Browser failures, timeouts, or console errors",
            count=int(reliability.get("browser_issue_submissions", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("browser_issue") and str(record.get("student_id") or "").strip()
            ],
        ),
    ]

    scatter_points: List[dict] = []
    plotted_static_scores: List[float] = []
    plotted_functional_scores: List[float] = []
    static_requirement_support = 0
    functional_requirement_support = 0
    behavioural_evaluable_students = 0
    for record in records:
        student_id = str(record.get("student_id") or "").strip()
        if not student_id:
            continue
        overall = record.get("overall")
        score_percent = round(float(overall) * 100, 2) if isinstance(overall, (int, float)) else None
        static_axis = _requirement_axis_score(record, STATIC_ANALYTICS_STAGES)
        functional_axis = _requirement_axis_score(record, FUNCTIONAL_ANALYTICS_STAGES)
        static_score_percent = (
            round(float(static_axis["score"]) * 100, 2)
            if isinstance(static_axis.get("score"), (int, float))
            else None
        )
        behavioural_score_percent = (
            round(float(functional_axis["score"]) * 100, 2)
            if isinstance(functional_axis.get("score"), (int, float))
            else None
        )
        static_requirement_support += int(static_axis.get("requirement_count", 0) or 0)
        functional_requirement_support += int(functional_axis.get("requirement_count", 0) or 0)
        if int(functional_axis.get("evaluable_count", 0) or 0) > 0:
            behavioural_evaluable_students += 1
        if static_score_percent is not None:
            plotted_static_scores.append(static_score_percent)
        if behavioural_score_percent is not None:
            plotted_functional_scores.append(behavioural_score_percent)
        scatter_points.append(
            {
                "id": student_id,
                "student_id": student_id,
                "student_name": str(record.get("student_name") or ""),
                "submission_id": record.get("submission_id"),
                "overall_mark_percent": score_percent,
                "static_score_percent": static_score_percent,
                "behavioural_score_percent": behavioural_score_percent,
                "static_requirement_count": int(static_axis.get("requirement_count", 0) or 0),
                "behavioural_requirement_count": int(functional_axis.get("requirement_count", 0) or 0),
                "behavioural_evaluable_count": int(functional_axis.get("evaluable_count", 0) or 0),
                "functional_evidence_limited": int(functional_axis.get("evaluable_count", 0) or 0) == 0,
                "manual_review_recommended": bool(record.get("manual_review_recommended")),
                "confidence": str(record.get("confidence") or "high"),
                "severity": str(record.get("severity") or "low"),
                "matched_rule_count": len(list(record.get("matched_rule_ids", []) or [])),
                "primary_issue": _first_non_empty(
                    [
                        record.get("reason_detail"),
                        record.get("review_note"),
                        record.get("reason"),
                    ]
                ),
                "report_available": bool(record.get("run_id")),
                "student_ids": [student_id],
            }
        )

    scatter_supported = bool(
        records
        and static_requirement_support > 0
        and functional_requirement_support > 0
        and behavioural_evaluable_students >= 2
    )
    scatter_reason = ""
    if not records:
        scatter_reason = "Scatter plot data will appear once assignment submissions are available."
    elif functional_requirement_support == 0:
        scatter_reason = "This chart is hidden because the selected assignment profile does not include enough runtime or browser evidence."
    elif behavioural_evaluable_students < 2:
        scatter_reason = "Not enough behavioural evidence to plot this view for the current assignment."
    elif static_requirement_support == 0:
        scatter_reason = "Static and code-quality evidence is not available for the current assignment."

    coverage_rows = [
        _graph_segment(
            segment_id="assigned_students",
            label="Assigned students",
            count=int(coverage.get("assigned_students", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("assigned_student_ids", []) or []),
        ),
        _graph_segment(
            segment_id="active_in_scope",
            label="Active submissions in scope",
            count=int(coverage.get("active_in_scope", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("active_student_ids", []) or []),
        ),
        _graph_segment(
            segment_id="missing_assigned",
            label="Assigned but not submitted",
            count=int(coverage.get("missing_assigned", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("missing_students", []) or []),
        ),
        _graph_segment(
            segment_id="submitted_not_analysable",
            label="Submitted but not analysable",
            count=int(coverage.get("not_analysable", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "not_analysable" and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="excluded_or_superseded",
            label="Excluded or superseded",
            count=int(coverage.get("inactive_or_superseded", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("inactive_or_superseded_students", []) or []),
        ),
        _graph_segment(
            segment_id="fully_evaluated_coverage",
            label="Fully evaluated",
            count=int(coverage.get("fully_evaluated", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "fully_evaluated" and str(record.get("student_id") or "").strip()
            ],
        ),
        _graph_segment(
            segment_id="partially_evaluated_coverage",
            label="Partially evaluated",
            count=int(coverage.get("partially_evaluated", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "partially_evaluated" and str(record.get("student_id") or "").strip()
            ],
        ),
    ]

    return {
        "student_index": student_index,
        "mark_distribution_histogram": {
            "total_students": len(records),
            "scored_students": histogram["scored_students"],
            "unscored_submissions": histogram["unscored_submissions"],
            "bin_width": histogram["bin_width"],
            "x_ticks": [0, 20, 40, 60, 80, 100],
            "primary_reference": {
                "key": "mean_percent",
                "label": "Mean",
                "value": histogram["mean_percent"],
                "detail": "Cohort mean mark across the active submissions in scope.",
            },
            "summary_stats": {
                "mean_percent": histogram["mean_percent"],
                "median_percent": histogram["median_percent"],
                "pass_threshold_percent": 50,
            },
            "reference_lines": {
                "mean_percent": histogram["mean_percent"],
                "median_percent": histogram["median_percent"],
                "pass_threshold_percent": 50,
            },
            "bins": histogram["bins"],
        },
        "component_performance_distribution": {
            "components": component_rows,
            "relevant_components": list(relevant_components),
        },
        "requirement_coverage_matrix": {
            "states": ["Met", "Partial", "Unmet", "Not evaluable"],
            "rows": requirement_rows,
        },
        "top_failing_rules_chart": {
            "rules": list(top_failing_rules[: min(len(top_failing_rules), 10)]),
        },
        "confidence_reliability_breakdown": {
            "groups": reliability_groups,
            "limitation_rows": limitation_rows,
        },
        "static_functional_scatter_plot": {
            "x_label": "Static / Code Quality Score",
            "y_label": "Behavioural / Functional Score",
            "supported": scatter_supported,
            "unsupported_reason": scatter_reason,
            "cohort_count": len(records),
            "behavioural_evaluable_students": behavioural_evaluable_students,
            "reference_lines": {
                "static_mean_percent": round(statistics.mean(plotted_static_scores), 2) if scatter_supported and plotted_static_scores else None,
                "behavioural_mean_percent": round(statistics.mean(plotted_functional_scores), 2) if scatter_supported and plotted_functional_scores else None,
                "show_mean_lines": scatter_supported and len(records) >= 4,
                "show_balance_diagonal": scatter_supported,
            },
            "points": scatter_points,
        },
        "missing_incomplete_submission_coverage_chart": {
            "stages": coverage_rows,
        },
    }


def _build_mark_distribution_histogram(records: Sequence[Mapping[str, object]]) -> dict:
    scored_records: List[dict[str, object]] = []
    for record in records:
        overall = record.get("overall")
        if not isinstance(overall, (int, float)):
            continue
        percent = max(0.0, min(100.0, float(overall) * 100))
        student_id = str(record.get("student_id") or "").strip()
        scored_records.append({"student_id": student_id, "percent": percent})

    scored_count = len(scored_records)
    bin_width = 5 if scored_count >= 20 else 10
    bins: List[dict] = []

    for start in range(0, 100, bin_width):
        end = min(start + bin_width, 100)
        student_ids = [
            str(item.get("student_id") or "")
            for item in scored_records
            if (
                start <= float(item.get("percent") or 0) <= 100
                if end >= 100
                else start <= float(item.get("percent") or 0) < end
            )
            and str(item.get("student_id") or "").strip()
        ]
        bins.append(
            {
                "id": f"band_{start}_{end}",
                "label": f"{start}-{end}%",
                "range_min": start,
                "range_max": end,
                "count": len(student_ids),
                "percent": (len(student_ids) / len(records) * 100) if records else 0,
                "student_ids": student_ids,
            }
        )

    scored_marks = [float(item["percent"]) for item in scored_records]
    return {
        "scored_students": scored_count,
        "unscored_submissions": sum(1 for record in records if not isinstance(record.get("overall"), (int, float))),
        "bin_width": bin_width,
        "mean_percent": round(statistics.mean(scored_marks), 2) if scored_marks else None,
        "median_percent": round(statistics.median(scored_marks), 2) if scored_marks else None,
        "bins": bins,
    }


def _student_graph_snapshot(record: Mapping[str, object]) -> dict:
    overall = record.get("overall")
    static_axis = _requirement_axis_score(record, STATIC_ANALYTICS_STAGES)
    functional_axis = _requirement_axis_score(record, FUNCTIONAL_ANALYTICS_STAGES)
    return {
        "student_id": str(record.get("student_id") or ""),
        "student_name": str(record.get("student_name") or ""),
        "submission_id": str(record.get("submission_id") or ""),
        "score_percent": round(float(overall) * 100, 2) if isinstance(overall, (int, float)) else None,
        "static_score_percent": (
            round(float(static_axis["score"]) * 100, 2)
            if isinstance(static_axis.get("score"), (int, float))
            else None
        ),
        "behavioural_score_percent": (
            round(float(functional_axis["score"]) * 100, 2)
            if isinstance(functional_axis.get("score"), (int, float))
            else None
        ),
        "grade": str(record.get("grade") or "unknown"),
        "confidence": str(record.get("confidence") or "high"),
        "evaluation_state": str(record.get("evaluation_state") or "fully_evaluated"),
        "severity": str(record.get("severity") or "low"),
        "manual_review_recommended": bool(record.get("manual_review_recommended")),
        "primary_issue": _first_non_empty(
            [
                record.get("reason_detail"),
                record.get("review_note"),
                record.get("reason"),
            ]
        ),
        "reason": str(record.get("reason") or ""),
        "reason_detail": str(record.get("reason_detail") or ""),
        "flags": list(record.get("flags", []) or []),
        "matched_rule_ids": list(record.get("matched_rule_ids", []) or []),
        "matched_rule_labels": list(record.get("matched_rule_labels", []) or []),
        "run_id": str(record.get("run_id") or ""),
        "source_mode": str(record.get("source_mode") or ""),
    }


def _graph_segment(
    *,
    segment_id: str,
    label: str,
    count: int,
    total: int,
    student_ids: Sequence[str],
) -> dict:
    clean_students = sorted({str(student_id).strip() for student_id in student_ids if str(student_id).strip()})
    return {
        "id": segment_id,
        "label": label,
        "count": int(count),
        "percent": (int(count) / total * 100) if total else 0,
        "student_ids": clean_students,
    }


def _requirement_axis_score(
    record: Mapping[str, object],
    stages: Sequence[str],
) -> dict:
    stage_set = {str(stage or "").strip().lower() for stage in stages if str(stage or "").strip()}
    requirements = list((record.get("score_evidence", {}) or {}).get("requirements", []) or [])
    total_weight = 0.0
    weighted_score = 0.0
    evaluable_count = 0
    requirement_count = 0

    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        if requirement.get("required") is False:
            continue
        stage = str(requirement.get("stage") or "").strip().lower()
        if stage not in stage_set:
            continue
        requirement_count += 1
        score = _requirement_numeric_score(requirement)
        weight = _coerce_float(requirement.get("weight"))
        if weight is None or weight <= 0:
            weight = 1.0
        if score is None:
            score = 0.0
        else:
            evaluable_count += 1
        total_weight += weight
        weighted_score += score * weight

    return {
        "score": (weighted_score / total_weight) if total_weight > 0 else None,
        "requirement_count": requirement_count,
        "evaluable_count": evaluable_count,
    }


def _requirement_numeric_score(requirement: Mapping[str, object]) -> float | None:
    raw_score = _coerce_float(requirement.get("score"))
    if raw_score is not None:
        return max(0.0, min(1.0, raw_score))

    status = str(requirement.get("status") or "").strip().upper()
    if status == "PASS":
        return 1.0
    if status == "PARTIAL":
        return 0.5
    if status == "FAIL":
        return 0.0
    if status == "SKIPPED":
        return None
    return None


def _rule_category(rule_id: str) -> str:
    identifier = str(rule_id or "").upper()
    if identifier.startswith("BROWSER."):
        return "browser/runtime"
    if identifier.startswith("BEHAVIOUR."):
        return "behavioural/runtime"
    if identifier.startswith("CONSISTENCY."):
        return "consistency"
    if ".QUALITY." in identifier:
        return "quality"
    if ".SECURITY." in identifier:
        return "security"
    if ".REQ." in identifier or ".MISSING_FILES" in identifier:
        return "structure"
    if identifier == "SUBMISSION.NOT_ANALYSABLE":
        return "confidence/runner limitation"
    return "other"


def _score_composition(records: List[Mapping[str, object]], total: int) -> List[dict]:
    sources = [
        {
            "id": "static_analysis",
            "label": "Static analysis",
            "description": "Required rules, structural checks, and static rubric findings that contribute baseline evidence.",
            "predicate": lambda record: bool(record.get("required_rules")) or int((record.get("check_stats") or {}).get("total", 0)) > 0,
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if not str(outcome.get("id") or "").startswith(("BEHAVIOUR.", "BROWSER.", "CONSISTENCY."))
                and ".QUALITY." not in str(outcome.get("id") or "")
                and ".SECURITY." not in str(outcome.get("id") or "")
                and str(outcome.get("id") or "") != "submission.not_analysable"
            ],
            "skipped_incidents": lambda record: 0,
            "confidence_reduced": lambda record: record.get("status") != "ok" and (
                bool(record.get("required_rules")) or int((record.get("check_stats") or {}).get("total", 0)) > 0
            ),
        },
        {
            "id": "runtime_checks",
            "label": "Behavioural and runtime checks",
            "description": "Runtime execution checks that validate backend behaviour or deterministic execution paths.",
            "predicate": lambda record: bool(record.get("behavioural_evidence")) or any(
                str(outcome.get("id") or "").startswith("BEHAVIOUR.") for outcome in record.get("problem_outcomes", []) or []
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("id") or "").startswith("BEHAVIOUR.")
            ],
            "skipped_incidents": lambda record: 1 if record.get("runtime_flags", {}).get("runtime_skipped") else 0,
            "confidence_reduced": lambda record: bool(
                record.get("runtime_flags", {}).get("runtime_skipped")
                or record.get("runtime_flags", {}).get("runtime_issue")
            ),
        },
        {
            "id": "browser_checks",
            "label": "Browser interaction checks",
            "description": "Browser automation and client-side checks that validate page loading and front-end behaviour.",
            "predicate": lambda record: bool(record.get("browser_evidence")) or any(
                str(outcome.get("id") or "").startswith("BROWSER.") for outcome in record.get("problem_outcomes", []) or []
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("id") or "").startswith("BROWSER.")
            ],
            "skipped_incidents": lambda record: 1 if record.get("runtime_flags", {}).get("browser_skipped") else 0,
            "confidence_reduced": lambda record: bool(
                record.get("runtime_flags", {}).get("browser_skipped")
                or record.get("runtime_flags", {}).get("browser_issue")
            ),
        },
        {
            "id": "penalties",
            "label": "Penalties and quality checks",
            "description": "Consistency, quality, or security findings that can drag performance down or trigger moderation review.",
            "predicate": lambda record: any(
                token in str(outcome.get("id") or "")
                for outcome in record.get("problem_outcomes", []) or []
                for token in ("CONSISTENCY.", ".QUALITY.", ".SECURITY.")
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if any(
                    token in str(outcome.get("id") or "")
                    for token in ("CONSISTENCY.", ".QUALITY.", ".SECURITY.")
                )
            ],
            "skipped_incidents": lambda record: 0,
            "confidence_reduced": lambda record: bool(record.get("runtime_flags", {}).get("consistency_issue")),
        },
        {
            "id": "skipped_logic",
            "label": "Skipped or unavailable checks",
            "description": "Confidence-reducing gaps where runtime, browser, or full pipeline evaluation was unavailable.",
            "predicate": lambda record: record.get("evaluation_state") != "fully_evaluated",
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("status") or "") == "SKIPPED" or str(outcome.get("id") or "") == "submission.not_analysable"
            ],
            "skipped_incidents": lambda record: int(bool(record.get("runtime_flags", {}).get("runtime_skipped")))
            + int(bool(record.get("runtime_flags", {}).get("browser_skipped"))),
            "confidence_reduced": lambda record: record.get("confidence") != "high",
        },
    ]

    rows: List[dict] = []
    for source in sources:
        students: set[str] = set()
        fail_incidents = 0
        warning_incidents = 0
        skipped_incidents = 0
        confidence_reduced_students: set[str] = set()
        for record in records:
            if not source["predicate"](record):
                continue
            student_id = str(record.get("student_id") or "")
            if student_id:
                students.add(student_id)
            outcomes = list(source["outcomes"](record))
            fail_incidents += sum(1 for outcome in outcomes if str(outcome.get("status") or "") == "FAIL")
            warning_incidents += sum(1 for outcome in outcomes if str(outcome.get("status") or "") == "WARN")
            skipped_incidents += int(source["skipped_incidents"](record))
            if source["confidence_reduced"](record) and student_id:
                confidence_reduced_students.add(student_id)

        counted = len(students)
        rows.append(
            {
                "id": source["id"],
                "label": source["label"],
                "description": source["description"],
                "students_affected": counted,
                "submissions_affected": counted,
                "percent": (counted / total * 100) if total else 0,
                "fail_incidents": fail_incidents,
                "warning_incidents": warning_incidents,
                "skipped_incidents": skipped_incidents,
                "confidence_reduced_submissions": len(confidence_reduced_students),
                "examples": sorted(students)[:3],
            }
        )
    return rows


def _teaching_insights(
    *,
    context: Mapping[str, object],
) -> List[dict]:
    insights: List[dict] = []
    assigned_students = int(context.get("assigned_students", 0))
    active_in_scope = int(context.get("active_in_scope", 0))
    missing_assigned = int(context.get("missing_assigned", 0))
    coverage_percent = int(context.get("coverage_percent", 0))
    partially_evaluated = int(context.get("partially_evaluated", 0))
    not_analysable = int(context.get("not_analysable", 0))
    manual_review = int(context.get("manual_review", 0))
    limitation_incidents = int(context.get("limitation_incidents", 0))
    small_cohort_enabled = bool(context.get("small_cohort_enabled"))
    strongest = dict(context.get("strongest_requirement", {}) or {})
    weakest = dict(context.get("weakest_requirement", {}) or {})
    top_rule = dict(context.get("top_failing_rule", {}) or {})
    major_limitations = list(context.get("major_limitations", []) or [])

    if assigned_students:
        if active_in_scope == 0:
            coverage_text = "No assigned students currently have an active submission in scope."
            coverage_priority = "high"
        elif missing_assigned == 0:
            coverage_text = "All assigned students currently have an active submission in scope."
            coverage_priority = "low"
        else:
            coverage_text = (
                f"{active_in_scope} of {assigned_students} assigned students currently have an active submission in scope; "
                f"{missing_assigned} are still missing."
            )
            coverage_priority = "medium"
        insights.append(
            {
                "insight_type": "coverage",
                "priority": coverage_priority,
                "text": coverage_text,
                "supporting_metric_keys": [
                    "assigned_students",
                    "active_in_scope",
                    "missing_assigned",
                    "coverage_percent",
                ],
            }
        )

    if strongest and weakest:
        if strongest.get("title") == weakest.get("title"):
            requirement_text = f"{strongest.get('title', 'Requirement coverage')} is the only requirement area with enough evaluable evidence to summarise so far."
        elif small_cohort_enabled:
            requirement_text = (
                f"{strongest.get('title', 'Requirement coverage')} is strongest ({strongest.get('students_met', 0)} fully met), "
                f"while {weakest.get('title', 'Requirement coverage')} is weakest ({weakest.get('students_met', 0)} fully met)."
            )
        else:
            requirement_text = (
                f"{strongest.get('title', 'Requirement coverage')} is currently the strongest requirement area by full attainment, "
                f"while {weakest.get('title', 'Requirement coverage')} is the weakest."
            )
        insights.append(
            {
                "insight_type": "requirement_balance",
                "priority": "medium",
                "text": requirement_text,
                "supporting_metric_keys": [
                    "strongest_requirement",
                    "weakest_requirement",
                ],
            }
        )

    if top_rule:
        affected = int(top_rule.get("submissions_affected", top_rule.get("students_affected", 0)) or 0)
        top_rule_text = (
            f"{top_rule.get('label', 'The top failing rule')} ({top_rule.get('rule_id', '')}) is the most common rule-level issue, "
            f"affecting {affected} active submission{'s' if affected != 1 else ''}"
        )
        if not small_cohort_enabled and coverage_percent:
            top_rule_text += f" ({int(round(float(top_rule.get('percent', 0) or 0)))}%)."
        else:
            top_rule_text += "."
        insights.append(
            {
                "insight_type": "rule_pattern",
                "priority": "medium" if affected <= 1 else "high",
                "text": top_rule_text,
                "supporting_metric_keys": [
                    "top_failing_rule",
                    "active_in_scope",
                ],
            }
        )

    if manual_review or partially_evaluated or not_analysable or limitation_incidents:
        reliability_text = (
            f"Manual review is recommended for {manual_review} active submission{'s' if manual_review != 1 else ''}; "
            f"{partially_evaluated} were partially evaluated and {not_analysable} were not analysable."
        )
        if major_limitations:
            reliability_text += f" The main confidence risk is {major_limitations[0].get('label', 'runner limitations').lower()}."
        insights.append(
            {
                "insight_type": "reliability",
                "priority": "high",
                "text": reliability_text,
                "supporting_metric_keys": [
                    "manual_review",
                    "partially_evaluated",
                    "not_analysable",
                    "limitation_incidents",
                    "major_limitations",
                ],
            }
        )
    else:
        insights.append(
            {
                "insight_type": "reliability",
                "priority": "low",
                "text": "Automated evaluation confidence is currently high across the active submissions in scope.",
                "supporting_metric_keys": [
                    "manual_review",
                    "partially_evaluated",
                    "not_analysable",
                    "limitation_incidents",
                ],
            }
        )

    return insights[:4]


def _teaching_insight_context(
    *,
    profile: str,
    coverage: Mapping[str, object],
    requirement_coverage: Sequence[Mapping[str, object]],
    top_failing_rules: Sequence[Mapping[str, object]],
    reliability: Mapping[str, object],
    total_records: int,
    small_cohort: Mapping[str, object],
) -> dict:
    strongest = None
    weakest = None
    if requirement_coverage:
        strongest_row = max(
            requirement_coverage,
            key=lambda row: (float(row.get("met_percent", 0) or 0), int(row.get("students_met", 0) or 0), str(row.get("title") or "")),
        )
        weakest_row = min(
            requirement_coverage,
            key=lambda row: (float(row.get("met_percent", 0) or 0), int(row.get("students_met", 0) or 0), str(row.get("title") or "")),
        )
        strongest = {
            "component": strongest_row.get("component"),
            "title": strongest_row.get("title"),
            "students_met": int(strongest_row.get("students_met", 0) or 0),
            "met_percent": round(float(strongest_row.get("met_percent", 0) or 0), 2),
        }
        weakest = {
            "component": weakest_row.get("component"),
            "title": weakest_row.get("title"),
            "students_met": int(weakest_row.get("students_met", 0) or 0),
            "met_percent": round(float(weakest_row.get("met_percent", 0) or 0), 2),
        }

    top_rule = None
    if top_failing_rules:
        first_rule = dict(top_failing_rules[0])
        top_rule = {
            "rule_id": first_rule.get("rule_id"),
            "label": first_rule.get("label"),
            "component": first_rule.get("component"),
            "severity": first_rule.get("severity"),
            "submissions_affected": int(first_rule.get("submissions_affected", first_rule.get("students_affected", 0)) or 0),
            "percent": round(float(first_rule.get("percent", 0) or 0), 2),
        }

    return {
        "profile": profile,
        "assigned_students": int(coverage.get("assigned_students", 0) or 0),
        "active_in_scope": int(coverage.get("active_in_scope", total_records) or 0),
        "coverage_percent": int(coverage.get("coverage_percent", 0) or 0),
        "missing_assigned": int(coverage.get("missing_assigned", 0) or 0),
        "fully_evaluated": int(reliability.get("fully_evaluated", 0) or 0),
        "partially_evaluated": int(reliability.get("partially_evaluated", 0) or 0),
        "not_analysable": int(reliability.get("not_analysable", 0) or 0),
        "manual_review": int(reliability.get("manual_review", 0) or 0),
        "limitation_incidents": int(reliability.get("limitation_incidents", 0) or 0),
        "limitation_categories": int(reliability.get("limitation_categories", 0) or 0),
        "major_limitations": [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "incident_count": int(item.get("incident_count", 0) or 0),
            }
            for item in list(reliability.get("limitation_breakdown", []) or [])[:2]
        ],
        "strongest_requirement": strongest,
        "weakest_requirement": weakest,
        "top_failing_rule": top_rule,
        "small_cohort_enabled": bool(small_cohort.get("enabled")),
        "small_cohort_threshold": int(small_cohort.get("threshold", SMALL_COHORT_THRESHOLD) or SMALL_COHORT_THRESHOLD),
        "small_cohort_note": str(small_cohort.get("note") or ""),
    }


def _label_for_identifier(identifier: str) -> str:
    label, _ = FINDING_LABELS.get(identifier, ("", ""))
    return label or identifier.replace(".", " / ").replace("_", " ").strip().title()


def _description_for_identifier(identifier: str) -> str:
    _, description = FINDING_LABELS.get(identifier, ("", ""))
    return description


def _first_non_empty(values: Sequence[object]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["generate_assignment_analytics", "FINDING_LABELS"]
