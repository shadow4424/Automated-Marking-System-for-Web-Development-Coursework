"""Assignment-level analytics engine.

Aggregates every submission for a given assignment ID and builds
teacher-facing analytics (score distribution, component readiness,
student issues, needs attention list).

Public API::

    analytics_dict = generate_assignment_analytics(assignment_id)

The function collects every *completed* run whose ``assignment_id``
matches, picks the **latest successful run per student**, then
delegates to :func:`build_teacher_analytics` for the actual
aggregation.
"""
from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ams.core.db import get_assignment
from ams.io.web_storage import get_runs_root, list_runs

logger = logging.getLogger(__name__)

# ── Finding labels (carried over from batch_analytics) ──────────────
FINDING_LABELS = {
    "PHP.MISSING_FILES": ("Missing required backend files (PHP)", ""),
    "SQL.MISSING_FILES": ("Missing required backend files (SQL)", ""),
    "CSS.MISSING_FILES": ("CSS missing", "CSS files required but not found"),
    "JS.MISSING_FILES": ("JavaScript missing", "JS files required but not found"),
    "HTML.MISSING_FILES": ("HTML missing", "HTML files required but not found"),
    "PHP.REQ.MISSING_FILES": ("Missing required backend files (PHP)", ""),
    "SQL.REQ.MISSING_FILES": ("Missing required backend files (SQL)", ""),
    "CSS.REQ.MISSING_FILES": ("CSS missing", "CSS files required but not found"),
    "JS.REQ.MISSING_FILES": ("JavaScript missing", "JS files required but not found"),
    "HTML.REQ.MISSING_FILES": ("HTML missing", "HTML files required but not found"),
    "CONFIG.MISSING_REQUIRED_RULES": ("Configuration issue", "Required component has no required rules configured"),
    "CONSISTENCY.JS_MISSING_HTML_ID": ("Cross-file consistency", "JS references HTML ID that does not exist"),
    "CONSISTENCY.JS_MISSING_HTML_CLASS": ("Cross-file consistency", "JS references HTML class that does not exist"),
    "CONSISTENCY.CSS_MISSING_HTML_ID": ("Cross-file consistency", "CSS references HTML ID that does not exist"),
    "CONSISTENCY.CSS_MISSING_HTML_CLASS": ("Cross-file consistency", "CSS references HTML class that does not exist"),
    "CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD": ("Cross-file consistency", "PHP accesses form field not defined in HTML"),
    "CONSISTENCY.FORM_FIELD_UNUSED_IN_PHP": ("Cross-file consistency", "HTML form field not accessed in PHP"),
    "CONSISTENCY.MISSING_LINK_TARGET": ("Cross-file consistency", "Link target does not exist"),
    "CONSISTENCY.MISSING_FORM_ACTION_TARGET": ("Cross-file consistency", "Form action target does not exist"),
    "BEHAVIOUR.PHP_SMOKE_FAIL": ("Runtime/behavioural issues", "PHP entrypoint execution failed"),
    "BEHAVIOUR.PHP_SMOKE_TIMEOUT": ("Runtime/behavioural issues", "PHP smoke test timed out"),
    "BEHAVIOUR.PHP_FORM_RUN_FAIL": ("Runtime/behavioural issues", "PHP form injection failed"),
    "BEHAVIOUR.PHP_FORM_RUN_TIMEOUT": ("Runtime/behavioural issues", "PHP form injection timed out"),
    "BEHAVIOUR.SQL_EXEC_FAIL": ("Runtime/behavioural issues", "SQL execution failed"),
    "BEHAVIOUR.SQL_EXEC_TIMEOUT": ("Runtime/behavioural issues", "SQL execution timed out"),
    "BROWSER.PAGE_LOAD_FAIL": ("Browser/runtime issues", "Browser page load failed"),
    "BROWSER.PAGE_LOAD_TIMEOUT": ("Browser/runtime issues", "Browser page load timed out"),
    "BROWSER.CONSOLE_ERRORS_PRESENT": ("Browser/runtime issues", "Console errors observed"),
}


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def generate_assignment_analytics(
    assignment_id: str,
    *,
    app: Any | None = None,
) -> Dict[str, object]:
    """Build analytics for *assignment_id* from all matching runs.

    Parameters
    ----------
    assignment_id:
        Assignment to analyse.
    app:
        Flask app instance (used to locate ``runs_root``).
        If ``None``, falls back to the default location.

    Returns
    -------
    dict
        The analytics payload (suitable for JSON serialisation and
        template rendering).
    """
    assignment = get_assignment(assignment_id)
    if assignment is None:
        raise ValueError(f"Assignment '{assignment_id}' not found")

    profile = assignment.get("profile", "frontend")

    # ── Gather runs belonging to this assignment ──────────────────────
    if app is not None:
        runs_root = get_runs_root(app)
    else:
        # Fallback for CLI / testing — use default path
        runs_root = Path("ams_web_runs")
        runs_root.mkdir(parents=True, exist_ok=True)

    all_runs = list_runs(runs_root)
    assignment_runs = [
        r for r in all_runs
        if r.get("assignment_id") == assignment_id
    ]

    # ── Collect the latest successful run per student ─────────────────
    records = _collect_student_records(assignment_runs, runs_root)

    # ── Build analytics from the collected records ────────────────────
    total = len(records) or 1
    analytics = _build_analytics(records, profile, total)
    analytics["assignment_id"] = assignment_id
    analytics["total_submissions_considered"] = len(assignment_runs)

    return analytics


def _collect_student_records(
    assignment_runs: List[dict],
    runs_root: Path,
) -> List[dict]:
    """For each student, pick the latest completed run and extract a record dict."""
    from ams.io.web_storage import find_run_by_id, load_run_info

    # Group runs by student_id
    runs_by_student: Dict[str, List[dict]] = defaultdict(list)
    for run in assignment_runs:
        student_id = run.get("student_id", "")
        if not student_id or student_id == "batch":
            # Batch runs don't have a meaningful student_id at the top level —
            # expand them by reading their batch_summary records instead.
            _expand_batch_run(run, runs_root, runs_by_student)
            continue
        runs_by_student[student_id].append(run)

    records: List[dict] = []
    for student_id, student_runs in runs_by_student.items():
        # Sort descending by created_at so we pick the latest first
        student_runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)

        for run in student_runs:
            record = _run_to_record(run, runs_root)
            if record is not None:
                records.append(record)
                break  # use only the latest successful run per student

    return records


def _expand_batch_run(
    run: dict,
    runs_root: Path,
    runs_by_student: Dict[str, List[dict]],
) -> None:
    """Expand a batch run into individual per-student entries."""
    from ams.io.web_storage import find_run_by_id

    run_id = run.get("id", "")
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return

    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return

    try:
        batch_data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return

    for rec in batch_data.get("records", []):
        sid = rec.get("student_id", "")
        if not sid:
            continue
        # Synthesize a run-like dict for this student's batch entry
        synthetic_run = {
            "id": run_id,
            "student_id": sid,
            "assignment_id": run.get("assignment_id", ""),
            "mode": "batch",
            "profile": run.get("profile", ""),
            "created_at": run.get("created_at", ""),
            "status": rec.get("status", ""),
            "_batch_record": rec,  # attach original record for direct extraction
        }
        runs_by_student[sid].append(synthetic_run)


def _run_to_record(run: dict, runs_root: Path) -> Optional[dict]:
    """Convert a run dict into the analytics record format.

    Returns ``None`` if the run doesn't contain usable results.
    """
    from ams.io.web_storage import find_run_by_id

    # If this is a pre-extracted batch record, use it directly
    batch_rec = run.get("_batch_record")
    if batch_rec is not None:
        return _batch_record_to_analytics_record(batch_rec, run, runs_root)

    run_id = run.get("id", "")
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return None

    # For single mark runs — read report.json
    report_path = run_dir / "report.json"
    if not report_path.exists():
        return None

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    scores = report.get("scores", {})
    comps = scores.get("by_component", {}) or {}
    findings = report.get("findings", []) or []

    return {
        "id": run.get("student_id", run_id),
        "student_id": run.get("student_id", ""),
        "assignment_id": run.get("assignment_id", ""),
        "overall": scores.get("overall"),
        "components": {k: comps.get(k, {}).get("score") for k in ("html", "css", "js", "php", "sql")},
        "status": "ok",
        "findings": findings,
        "report_path": str(report_path),
        "run_id": run_id,
    }


def _batch_record_to_analytics_record(
    rec: dict,
    run: dict,
    runs_root: Path,
) -> Optional[dict]:
    """Convert a batch_summary record into an analytics record.

    Enriches with findings from the individual report if available.
    """
    from ams.io.web_storage import find_run_by_id

    if rec.get("status") not in ("ok", "invalid_filename", "invalid_student_id", "invalid_assignment_id"):
        if "error" in rec:
            return None  # skip pipeline errors

    run_id = run.get("id", "")
    run_dir = find_run_by_id(runs_root, run_id)

    findings: list = []
    report_path_str = rec.get("report_path", "")
    if report_path_str and Path(report_path_str).exists():
        try:
            rep = json.loads(Path(report_path_str).read_text(encoding="utf-8"))
            findings = rep.get("findings", []) or []
        except Exception:
            pass
    elif run_dir is not None:
        # Try sub-run directory
        sub_dir = run_dir / "runs" / rec.get("id", "")
        sub_report = sub_dir / "report.json"
        if sub_report.exists():
            try:
                rep = json.loads(sub_report.read_text(encoding="utf-8"))
                findings = rep.get("findings", []) or []
            except Exception:
                pass

    return {
        "id": rec.get("student_id") or rec.get("id", ""),
        "student_id": rec.get("student_id", ""),
        "assignment_id": rec.get("assignment_id", run.get("assignment_id", "")),
        "overall": rec.get("overall"),
        "components": rec.get("components", {}),
        "status": rec.get("status", "ok"),
        "findings": findings,
        "report_path": report_path_str,
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------
#  Analytics aggregation (adapted from batch_analytics.py)
# ---------------------------------------------------------------------------

def _build_analytics(records: List[dict], profile: str, total: int) -> Dict[str, object]:
    """Build the full analytics dict from a list of student records."""
    overall_scores = [
        float(r["overall"]) for r in records
        if r.get("overall") is not None
    ]

    overall_stats: Dict[str, float] | None = None
    if overall_scores:
        overall_stats = {
            "mean": statistics.mean(overall_scores),
            "median": statistics.median(overall_scores),
            "min": min(overall_scores),
            "max": max(overall_scores),
        }

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

    component_readiness = _component_readiness(records)
    student_issues, runner_limits = _student_and_runner_issues(records, total)
    needs_attention = _needs_attention(records, profile)

    return {
        "profile": profile,
        "overall": {
            "mean": overall_stats.get("mean") if overall_stats else None,
            "median": overall_stats.get("median") if overall_stats else None,
            "min": overall_stats.get("min") if overall_stats else None,
            "max": overall_stats.get("max") if overall_stats else None,
            "total": len(records),
            "buckets": {
                "No attempt (0%)": buckets["zero"],
                "Partial (1–50%)": buckets["gt_0_to_0_5"],
                "Good partial (51–99%)": buckets["gt_0_5_to_1"],
                "Full marks (100%)": buckets["one"],
            },
        },
        "components": component_readiness,
        "student_issues": student_issues,
        "runner_limitations": runner_limits,
        "needs_attention": needs_attention,
    }


def _component_readiness(records: List[Mapping[str, object]]) -> Dict[str, dict]:
    comps = ["html", "css", "js", "php", "sql"]
    result: Dict[str, dict] = {}
    for comp in comps:
        scores: List[float] = []
        for rec in records:
            score = rec.get("components", {}).get(comp)
            if isinstance(score, (int, float)):
                scores.append(float(score))
        n = len(scores)
        zeros = len([s for s in scores if s == 0])
        full = len([s for s in scores if s == 1])
        half = len([s for s in scores if s == 0.5])
        avg = sum(scores) / n if n else None
        result[comp] = {
            "average": avg,
            "pct_zero": (zeros / n * 100) if n else None,
            "pct_half": (half / n * 100) if n else None,
            "pct_full": (full / n * 100) if n else None,
            "skipped": len([1 for rec in records if rec.get("components", {}).get(comp) == "SKIPPED"]),
        }
    return result


def _needs_attention(records: List[Mapping[str, object]], profile: str) -> List[dict]:
    attention: List[dict] = []
    for rec in sorted(records, key=lambda r: r.get("id", "")):
        pipeline_status = rec.get("status", "ok")
        overall = rec.get("overall")
        comps = rec.get("components", {})
        flags: List[str] = []

        if pipeline_status != "ok":
            flags.append("pipeline error")
        if overall is None or overall == 0:
            flags.append("no score")
        elif isinstance(overall, (int, float)) and overall < 0.5:
            flags.append("low score")

        if profile == "fullstack":
            for comp in ["php", "sql"]:
                if comps.get(comp) == 0:
                    flags.append(f"{comp} missing")

        findings = rec.get("findings", []) or []
        fail_ids = {f.get("id", "") for f in findings if f.get("severity") in ("FAIL", "WARN")}
        if any(fid.startswith("BEHAVIOUR.") and "SKIPPED" not in fid for fid in fail_ids):
            flags.append("runtime failure")
        if any(fid.startswith("CONSISTENCY.") for fid in fail_ids):
            flags.append("consistency issues")

        reason = _primary_reason(rec)

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

        if flags or reason != "other":
            attention.append({
                "submission_id": rec.get("id"),
                "student_id": rec.get("student_id", ""),
                "overall": overall,
                "grade": grade,
                "flags": flags,
                "reason": reason,
                "report_path": rec.get("report_path"),
                "run_id": rec.get("run_id", ""),
            })
    return attention


def _primary_reason(rec: Mapping[str, object]) -> str:
    findings = rec.get("findings", []) or []
    priorities: Sequence[tuple[str, str]] = [
        ("missing", "missing required files"),
        ("BEHAVIOUR.", "behavioural runtime issue"),
        ("SYNTAX", "syntax issue"),
        ("BROWSER.", "browser runtime issue"),
        ("CONSISTENCY.", "consistency issue"),
    ]
    for prefix, label in priorities:
        for f in findings:
            if f.get("finding_category") == "missing" and prefix == "missing":
                return label
            if prefix in f.get("id", ""):
                return label
    return "other"


def _student_and_runner_issues(
    records: List[Mapping[str, object]],
    total: int,
) -> tuple[List[dict], List[dict]]:
    categories = defaultdict(list)
    runner_limits = defaultdict(list)
    for rec in records:
        sid = rec.get("id")
        findings = rec.get("findings", []) or []
        for f in findings:
            fid = f.get("id", "")
            cat = f.get("finding_category")
            severity = f.get("severity")
            if fid.startswith("BEHAVIOUR.") and "SKIPPED" in fid:
                runner_limits["behavioural"].append(sid)
                continue
            if fid.startswith("BROWSER.PAGE_LOAD") and "SKIPPED" in fid:
                runner_limits["browser"].append(sid)
                continue
            if severity not in {"WARN", "FAIL"}:
                continue
            if fid == "BROWSER.CONSOLE_ERRORS_PRESENT":
                categories["browser_runtime"].append((sid, fid))
                continue
            if cat == "missing":
                if fid.startswith("PHP.") or fid.startswith("SQL."):
                    categories["missing_backend"].append((sid, fid))
                else:
                    categories["missing_frontend"].append((sid, fid))
                continue
            if fid.startswith("CONSISTENCY."):
                categories["consistency"].append((sid, fid))
                continue
            if fid.startswith("BEHAVIOUR."):
                categories["behavioural"].append((sid, fid))
                continue
            if fid.startswith("BROWSER."):
                categories["browser_runtime"].append((sid, fid))
                continue
            if fid.endswith("SYNTAX_ERROR") or fid.endswith("PARSE_ERROR"):
                categories["syntax"].append((sid, fid))
                continue
            categories["other"].append((sid, fid))

    def _summary(cat: str, entries: list[tuple[str, str]]) -> dict:
        if not entries:
            return {}
        subs = [s for s, _ in entries]
        ids = [fid for _, fid in entries]
        return {
            "category": cat,
            "students_affected": len(set(subs)),
            "percent": (len(set(subs)) / total * 100) if total else 0,
            "finding_ids": sorted(set(ids)),
            "examples": list(dict.fromkeys(subs))[:3],
        }

    student_sections = [
        _summary("missing_backend", categories["missing_backend"]),
        _summary("missing_frontend", categories["missing_frontend"]),
        _summary("syntax", categories["syntax"]),
        _summary("behavioural_runtime", categories["behavioural"]),
        _summary("browser_runtime", categories["browser_runtime"]),
        _summary("consistency", categories["consistency"]),
        _summary("other", categories["other"]),
    ]
    student_sections = [s for s in student_sections if s]

    runner_sections = [
        _summary("behavioural_skipped", [(s, "BEHAVIOUR.SKIPPED") for s in runner_limits["behavioural"]]),
        _summary("browser_skipped", [(s, "BROWSER.PAGE_LOAD_SKIPPED") for s in runner_limits["browser"]]),
    ]
    runner_sections = [r for r in runner_sections if r]
    return student_sections, runner_sections


__all__ = [
    "generate_assignment_analytics",
    "FINDING_LABELS",
]
