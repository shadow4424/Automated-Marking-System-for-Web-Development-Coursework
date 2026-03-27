"""AMS Web Interface — primary Flask application for the marking system.

Provides routes for:
- Single-submission marking (``/mark``)
- Batch processing multiple submissions (``/batch``)
- Run history and report viewing (``/runs``, ``/runs/<run_id>``)
- Artifact and report downloads

Start locally with: ``python -m flask --app ams.webui run --debug``
"""
from __future__ import annotations

import csv
import io
import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests as _requests
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ams.core.db import (
    assignment_allows_teacher,
    init_db,
    get_assignment,
    list_assignments,
    list_assignments_for_student,
    PREVIEW_STUDENT_ID,
)
from ams.core.attempts import (
    create_attempt,
    create_attempt_storage_dir,
    filter_attempts_for_root,
    get_attempt_by_run_reference,
    get_student_assignment_summary,
    list_attempts,
    recompute_active_attempt,
    sync_attempts_from_storage,
    update_attempt,
    utc_now_iso,
)
from ams.core.pipeline import AssessmentPipeline
from ams.core.config import (
    ScoringMode,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GITHUB_OAUTH_CALLBACK,
)
from ams.core.profiles import get_visible_profile_specs
from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats
from ams.analytics import FINDING_LABELS
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.pdf_exports import build_submission_report_pdf
from ams.io.export_report import (
    build_export_report,
    validate_export_report,
    export_json as _export_json,
    export_txt,
    export_csv_zip,
    export_pdf as _export_pdf,
)
from ams.io.web_storage import (
    allowed_download,
    cleanup_batch_run_storage,
    create_run_dir,
    extract_review_flags_from_report,
    find_run_by_id,
    find_submission_root,
    get_runs_root,
    list_runs,
    load_run_info,
    safe_extract_zip,
    save_metadata,
    save_run_info,
    validate_file_size,
    validate_file_type,
)
from ams.web.validators import validate_is_zipfile
from ams.tools.batch import discover_batch_items, run_batch, validate_submission_filename, write_outputs
from ams.core.job_manager import job_manager

logger = logging.getLogger(__name__)

MAX_UPLOAD_MB = 25
PROFILE_CHOICES = tuple(get_visible_profile_specs().keys())
ALLOWED_DOWNLOADS = {
    "report.json",
    "summary.txt",
    "batch_summary.json",
    "batch_summary.csv",
    "batch_reports",
    "runtime_health_",
    "evaluation_summary",
    "evaluation_results",
}


# --- Jinja helpers -----------------------------------------------------------

import re as _re

_PATH_RE = _re.compile(
    r"(?:[A-Za-z]:)?[\\/](?:[\w .~@#$%&()\-]+[\\/]){2,}[\w .~@#$%&()\-]+\.\w{1,10}$"
)


def _clean_path(value: object) -> str:
    """Jinja filter: shorten absolute file paths to ``submission/file.ext``.

    E.g. ``E:\\Users\\…\\submission\\index.php`` → ``submission/index.php``
    """
    s = str(value).replace("\\", "/")
    # Try to cut at a well-known folder boundary
    for marker in ("submission/", "artifacts/", "test_coursework/"):
        idx = s.find(marker)
        if idx != -1:
            return s[idx:]
    # Fallback: if it looks like a path, show only the last two segments
    if _PATH_RE.match(s):
        parts = s.rsplit("/", 2)
        return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    return s


def _render_evidence_value(val: object) -> str:
    """Return an HTML-safe string for a single evidence value.

    * Paths are shortened via ``_clean_path``
    * Booleans become ✓ / ✗
    * Lists become comma-separated
    * Everything else is stringified
    """
    from markupsafe import Markup, escape

    if isinstance(val, bool):
        return Markup('<span class="text-success">✓</span>') if val else Markup('<span class="text-danger">✗</span>')
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "—"
        items = ", ".join(escape(_clean_path(v)) for v in val)
        return Markup(items)
    s = str(val)
    if _PATH_RE.match(s.replace("\\", "/")):
        return _clean_path(s)
    return s


def _ensure_check_stats(report: dict) -> dict:
    """Enrich a loaded report dict with aggregated check stats if missing.

    Backward-compatible: reports generated before the aggregation layer was
    added will be enriched on load so the template always has the data.
    """
    if "checks" not in report or "check_stats" not in report or "diagnostics" not in report:
        findings = report.get("findings", [])
        checks, diagnostics = aggregate_findings_to_checks(findings)
        if "checks" not in report:
            report["checks"] = [c.to_dict() for c in checks]
        if "check_stats" not in report:
            report["check_stats"] = compute_check_stats(checks)
        if "diagnostics" not in report:
            report["diagnostics"] = diagnostics
    return report


def _load_threat_file_contents(findings: list, run_dir: Path) -> dict:
    """Load source file contents for threat-flagged findings.

    For each THREAT finding that references a file inside the submission
    directory, reads the file and records which line numbers triggered alerts.

    Returns a ``dict`` keyed by the file's path relative to ``submission/``:

    .. code-block:: python

        {
            "index.php": {
                "lines": ["<?php", "system($_GET['cmd']);", ...],
                "threat_lines": [2, ...],
            },
            ...
        }

    Files larger than 200 KB are skipped.  All paths are validated to stay
    within ``run_dir/submission/`` — no traversal is possible.
    """
    MAX_FILE_BYTES = 200 * 1024  # 200 KB per-file cap
    submission_dir = (run_dir / "submission").resolve()

    threat_findings = [
        f for f in findings
        if f.get("severity") == "THREAT"
        and isinstance(f.get("evidence"), dict)
        and f["evidence"].get("file")
    ]
    if not threat_findings:
        return {}

    def _to_rel(raw: str) -> str:
        """Convert an absolute or relative file reference to a path relative to submission/."""
        s = str(raw).replace("\\", "/")
        if "submission/" in s:
            idx = s.rfind("submission/")
            return s[idx + len("submission/"):]
        return Path(raw).name

    file_data: dict[str, dict] = {}

    # First pass — load unique files
    for finding in threat_findings:
        file_rel = _to_rel(finding["evidence"]["file"])
        if not file_rel or file_rel in file_data:
            continue
        candidate = (submission_dir / file_rel).resolve()
        try:
            candidate.relative_to(submission_dir)
        except ValueError:
            continue  # path traversal attempt — skip
        if not candidate.is_file():
            continue
        if candidate.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
            file_data[file_rel] = {"lines": content.splitlines(), "threat_lines": []}
        except Exception:
            pass

    # Second pass — mark threat lines
    for finding in threat_findings:
        file_rel = _to_rel(finding["evidence"]["file"])
        if file_rel not in file_data:
            continue
        try:
            ln = int(finding["evidence"]["line"])
            if ln not in file_data[file_rel]["threat_lines"]:
                file_data[file_rel]["threat_lines"].append(ln)
        except (TypeError, ValueError, KeyError):
            pass

    for key in file_data:
        file_data[key]["threat_lines"].sort()

    return file_data


_DETAIL_COMPONENT_ORDER = ["html", "css", "js", "php", "sql", "api", "browser", "behavioural", "consistency", "other"]
_DETAIL_COMPONENT_LABELS = {
    "html": "HTML",
    "css": "CSS",
    "js": "JavaScript",
    "php": "PHP",
    "sql": "SQL",
    "api": "API",
    "browser": "Browser",
    "behavioral": "Behavioural",
    "behavioural": "Behavioural",
    "consistency": "Consistency",
    "security": "Security",
    "other": "Other",
}
_DETAIL_STAGE_LABELS = {
    "static": "Static",
    "runtime": "Runtime",
    "browser": "Browser",
    "layout": "Layout",
    "quality": "Quality",
    "manual": "Manual",
}
_DETAIL_STATUS_PRIORITY = {
    "FAIL": 0,
    "THREAT": 0,
    "PARTIAL": 1,
    "WARN": 1,
    "SKIPPED": 2,
    "PASS": 3,
    "NOT_EVALUATED": 4,
    "UNKNOWN": 5,
}
_DETAIL_TONE_BY_STATUS = {
    "FAIL": "danger",
    "THREAT": "danger",
    "PARTIAL": "warning",
    "WARN": "warning",
    "SKIPPED": "muted",
    "PASS": "success",
    "NOT_EVALUATED": "muted",
    "UNKNOWN": "muted",
}
_CONFIDENCE_FLAG_TEXT = {
    "runtime_failure": "Runtime checks failed or timed out in this run.",
    "browser_failure": "Browser checks failed or timed out in this run.",
    "browser_console_errors": "Browser console errors reduced trust in the automated result.",
    "runtime_skipped": "Runtime checks were skipped.",
    "browser_skipped": "Browser checks were skipped.",
    "layout_skipped": "Layout checks were skipped.",
}


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_non_empty(values: Sequence[object]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _format_submission_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def _normalize_status(value: object, *, fallback: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text or fallback


def _status_tone(status: object) -> str:
    return _DETAIL_TONE_BY_STATUS.get(_normalize_status(status), "muted")


def _stage_label(stage: object) -> str:
    key = str(stage or "").strip().lower()
    if not key:
        return "General"
    return _DETAIL_STAGE_LABELS.get(key, key.replace("_", " ").title())


def _component_label(component: object) -> str:
    key = str(component or "").strip().lower()
    if not key:
        return "General"
    return _DETAIL_COMPONENT_LABELS.get(key, key.replace("_", " ").title())


def _component_filter_value(component: object, *, stage: object = None) -> str:
    comp = str(component or "").strip().lower()
    stage_key = str(stage or "").strip().lower()
    if comp in {"html", "css", "js", "php", "sql", "api", "browser", "behavioral", "behavioural"}:
        return "behavioural" if comp == "behavioral" else comp
    if stage_key == "browser":
        return "browser"
    if stage_key == "runtime":
        return "behavioural"
    return comp or "other"


def _humanize_identifier(identifier: object) -> str:
    text = str(identifier or "").strip()
    if not text:
        return "Unnamed item"
    label, _description = FINDING_LABELS.get(text, ("", ""))
    if label:
        return label
    pretty = text.replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return pretty.title() if pretty else text


def _describe_identifier(identifier: object) -> str:
    text = str(identifier or "").strip()
    if not text:
        return ""
    _label, description = FINDING_LABELS.get(text, ("", ""))
    return description


_ARTIFACT_ROOTS = ("artifacts/", "runs/", "reports/", "evaluation/", "submission/")


def _to_relative_artifact_path(path: str) -> str:
    """Strip any absolute prefix from an artifact path, keeping only the run-relative portion.

    Screenshot paths stored in findings may be absolute Windows paths
    (e.g. ``E:\\...\\runs\\run-id\\artifacts\\page.png``).  The
    ``/runs/<id>/artifacts/<relpath>`` route only accepts paths whose first
    component is one of the known allowed roots.  This function finds the
    first occurrence of a known root segment and returns the suffix from that
    point, with backslashes normalised to forward slashes.
    """
    normalised = path.replace("\\", "/")
    for root in _ARTIFACT_ROOTS:
        idx = normalised.find(root)
        if idx >= 0:
            return normalised[idx:]
    return normalised


def _gather_screenshots(evidence: object) -> list[str]:
    if not isinstance(evidence, Mapping):
        return []
    screenshots: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        normalised = _to_relative_artifact_path(raw.strip())
        if normalised not in seen:
            seen.add(normalised)
            screenshots.append(normalised)

    direct = evidence.get("screenshot")
    if isinstance(direct, str) and direct.strip():
        _add(direct)
    ux_review = evidence.get("ux_review")
    if isinstance(ux_review, Mapping):
        shot = ux_review.get("screenshot")
        if isinstance(shot, str) and shot.strip():
            _add(shot)
    vision = evidence.get("vision_analysis")
    if isinstance(vision, Mapping):
        meta = vision.get("meta")
        if isinstance(meta, Mapping):
            shot = meta.get("screenshot")
            if isinstance(shot, str) and shot.strip():
                _add(shot)
    return screenshots


def _finding_stage(finding: Mapping[str, object]) -> str:
    evidence = dict(finding.get("evidence", {}) or {})
    explicit = str(evidence.get("stage") or "").strip().lower()
    if explicit:
        return explicit
    identifier = str(finding.get("id") or "")
    category = str(finding.get("category") or "").strip().lower()
    if identifier.startswith("BROWSER.") or category == "browser":
        return "browser"
    if identifier.startswith("BEHAVIOUR.") or identifier.startswith("BEHAVIOR.") or category in {"behavioral", "behavioural"}:
        return "runtime"
    return ""


def _finding_group_key(finding: Mapping[str, object]) -> str:
    evidence = dict(finding.get("evidence", {}) or {})
    rule_id = str(evidence.get("rule_id") or "").strip()
    if rule_id:
        return rule_id
    return str(finding.get("id") or "").strip()


def _normalize_raw_finding(finding: Mapping[str, object]) -> dict[str, Any]:
    identifier = str(finding.get("id") or "").strip() or "unknown"
    evidence = dict(finding.get("evidence", {}) or {}) if isinstance(finding.get("evidence"), Mapping) else finding.get("evidence")
    severity = _normalize_status(finding.get("severity"), fallback="INFO")
    stage = _finding_stage(finding)
    component = str(finding.get("category") or "").strip().lower()
    title = _humanize_identifier(identifier)
    message = _first_non_empty(
        [
            finding.get("message"),
            _describe_identifier(identifier),
            title,
        ]
    )
    screenshots = _gather_screenshots(evidence)
    search_terms = " ".join(
        str(part)
        for part in (
            identifier,
            title,
            message,
            component,
            stage,
            finding.get("source"),
            finding.get("finding_category"),
        )
        if str(part or "").strip()
    ).lower()
    return {
        "id": identifier,
        "title": title,
        "message": message,
        "status": severity,
        "badge_label": severity if severity != "THREAT" else "THREAT",
        "tone": _status_tone(severity),
        "component": component,
        "component_label": _component_label(component),
        "component_filter": _component_filter_value(component, stage=stage),
        "stage": stage,
        "stage_label": _stage_label(stage),
        "source": str(finding.get("source") or "").strip(),
        "finding_category": str(finding.get("finding_category") or "").strip(),
        "evidence": evidence,
        "screenshots": screenshots,
        "search_text": search_terms,
    }


def _build_decision_summary(
    run: Mapping[str, object],
    report: Mapping[str, object] | None,
    confidence: Mapping[str, object],
    review: Mapping[str, object],
    limitations: list[dict[str, Any]],
    student_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    run_status = str(run.get("status") or "").strip().lower()
    overall = _coerce_float(((report or {}).get("scores", {}) or {}).get("overall"))
    confidence_level = str(confidence.get("level") or "unknown").strip().lower() or "unknown"
    manual_review_required = bool(review.get("recommended"))
    manual_review_label = "Required" if manual_review_required else "Not required"

    if run_status == "pending":
        outcome = "Awaiting rerun"
        mark_band = "Pending"
        tone = "warning"
        manual_review_label = "Pending"
    elif run_status in {"failed", "error"}:
        outcome = "Manual decision needed"
        mark_band = "Hold"
        tone = "danger"
        manual_review_required = True
        manual_review_label = "Required"
    elif overall is None:
        outcome = "Manual decision needed"
        mark_band = "Hold"
        tone = "danger"
        manual_review_required = True
        manual_review_label = "Required"
    elif overall <= 0:
        outcome = "No meaningful attempt"
        mark_band = "0.0"
        tone = "danger"
    elif overall < 0.7:
        outcome = "Partial attempt"
        mark_band = "0.5"
        tone = "warning"
    else:
        outcome = "Meets exercise objectives"
        mark_band = "1.0"
        tone = "success"

    reasons = []
    for item in student_issues[:2]:
        title = str(item.get("title") or "").strip()
        if title and title not in reasons:
            reasons.append(title)
    for item in limitations:
        title = str(item.get("title") or "").strip()
        if title and title not in reasons:
            reasons.append(title)
        if len(reasons) >= 3:
            break

    confidence_titles = [str(item.get("title") or "").strip().lower() for item in limitations if str(item.get("title") or "").strip()]
    if run_status == "pending":
        explanation = "Confidence will be recalculated after the queued rerun completes."
    elif confidence_titles:
        prefix = {
            "low": "Low confidence because",
            "medium": "Medium confidence because",
            "high": "High confidence, but note that",
        }.get(confidence_level, "Confidence is reduced because")
        explanation = f"{prefix} {', '.join(confidence_titles[:2])}."
    elif confidence_level == "high":
        explanation = "High confidence because all enabled automated stages completed successfully."
    elif confidence_level == "medium":
        explanation = "Medium confidence because some automated stages were incomplete."
    else:
        explanation = "Low confidence because the automated result is missing reliable supporting evidence."

    return {
        "outcome": outcome,
        "mark_band": mark_band,
        "tone": tone,
        "internal_score_percent": int(round(overall * 100)) if overall is not None else None,
        "confidence_level": confidence_level,
        "manual_review_required": manual_review_required,
        "manual_review_label": manual_review_label,
        "reasons": reasons[:3],
        "confidence_explanation": explanation,
    }


def _build_submission_detail_view(
    run: Mapping[str, object],
    report: Mapping[str, object] | None,
) -> dict[str, Any]:
    report_data = dict(report or {})
    score_evidence = dict(report_data.get("score_evidence", {}) or {})
    confidence = dict(score_evidence.get("confidence", {}) or {})
    review = dict(score_evidence.get("review", {}) or {})
    role_mapping = dict(score_evidence.get("role_mapping", {}) or {})
    environment = dict(report_data.get("environment", {}) or {})
    component_scores = dict(((report_data.get("scores", {}) or {}).get("by_component", {}) or {}))
    requirement_results = [
        dict(item)
        for item in list(score_evidence.get("requirements", []) or [])
        if isinstance(item, Mapping)
    ]
    raw_findings = [
        _normalize_raw_finding(finding)
        for finding in list(report_data.get("findings", []) or [])
        if isinstance(finding, Mapping)
    ]
    diagnostics = [
        _normalize_raw_finding(finding)
        for finding in list(report_data.get("diagnostics", []) or [])
        if isinstance(finding, Mapping)
    ]
    checks = [
        dict(item)
        for item in list(report_data.get("checks", []) or [])
        if isinstance(item, Mapping)
    ]
    behavioural_evidence = [dict(item) for item in list(report_data.get("behavioural_evidence", []) or []) if isinstance(item, Mapping)]
    browser_evidence = [dict(item) for item in list(report_data.get("browser_evidence", []) or []) if isinstance(item, Mapping)]

    findings_by_key: dict[str, list[dict[str, Any]]] = {}
    for finding in raw_findings:
        findings_by_key.setdefault(_finding_group_key(finding), []).append(finding)
    checks_by_id = {
        str(check.get("check_id") or "").strip(): check
        for check in checks
        if str(check.get("check_id") or "").strip()
    }

    browser_capture_failed = any(item["id"] == "BROWSER.CAPTURE_FAIL" for item in diagnostics)
    browser_reliable = bool(environment.get("browser_available", True)) and bool(environment.get("browser_tests_run", True)) and not browser_capture_failed

    valid_ux_findings: list[dict[str, Any]] = []
    raw_ux_findings: list[dict[str, Any]] = []
    for finding in raw_findings:
        evidence = finding.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        ux_review = evidence.get("ux_review")
        if not isinstance(ux_review, Mapping):
            continue
        raw_ux_findings.append(finding)
        if str(ux_review.get("status") or "").strip().upper() == "NOT_EVALUATED":
            continue
        if browser_reliable:
            valid_ux_findings.append(finding)
    hidden_ux_keys = {_finding_group_key(finding) for finding in raw_ux_findings} if not browser_reliable else set()

    evidence_items: list[dict[str, Any]] = []
    matched_keys: set[str] = set()

    for requirement in requirement_results:
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        if not requirement_id:
            continue
        matched_keys.add(requirement_id)
        component = str(requirement.get("component") or "").strip().lower()
        stage = str(requirement.get("stage") or "").strip().lower()
        status = _normalize_status(requirement.get("status"), fallback="UNKNOWN")
        check = checks_by_id.get(requirement_id)
        related_findings = list(findings_by_key.get(requirement_id, []))
        detail = _first_non_empty(
            list((check or {}).get("messages", []) or [])
            + [requirement.get("skipped_reason"), (requirement.get("evidence") or {}).get("reason") if isinstance(requirement.get("evidence"), Mapping) else ""]
        )
        evidence_items.append(
            {
                "kind": "requirement",
                "type_label": "Requirement",
                "title": _first_non_empty([requirement.get("description"), _humanize_identifier(requirement_id)]),
                "secondary_id": requirement_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": component,
                "component_label": _component_label(component),
                "component_filter": _component_filter_value(component, stage=stage),
                "stage": stage,
                "stage_label": _stage_label(stage),
                "detail": detail,
                "required": bool(requirement.get("required", True)),
                "score_display": requirement.get("score"),
                "requirement": requirement,
                "check": check,
                "raw_findings": related_findings,
                "search_text": " ".join(
                    [
                        requirement_id,
                        str(requirement.get("description") or ""),
                        component,
                        stage,
                        status,
                        detail,
                    ]
                    + [str(item.get("title") or "") for item in related_findings]
                ).lower(),
            }
        )

    for check_id, check in checks_by_id.items():
        if check_id in matched_keys:
            continue
        if check_id in hidden_ux_keys:
            continue
        related_findings = list(findings_by_key.get(check_id, []))
        first_finding = related_findings[0] if related_findings else {}
        stage = str(first_finding.get("stage") or "").strip().lower()
        component = str(check.get("component") or first_finding.get("component") or "").strip().lower()
        status = _normalize_status(check.get("status"), fallback="UNKNOWN")
        detail = _first_non_empty(list(check.get("messages", []) or []) + [first_finding.get("message")])
        evidence_items.append(
            {
                "kind": "check",
                "type_label": "Check",
                "title": _humanize_identifier(check_id),
                "secondary_id": check_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": component,
                "component_label": _component_label(component),
                "component_filter": _component_filter_value(component, stage=stage),
                "stage": stage,
                "stage_label": _stage_label(stage),
                "detail": detail or _describe_identifier(check_id),
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": check,
                "raw_findings": related_findings,
                "search_text": " ".join(
                    [
                        check_id,
                        _humanize_identifier(check_id),
                        component,
                        stage,
                        status,
                        detail or "",
                    ]
                ).lower(),
            }
        )

    threat_findings = [finding for finding in raw_findings if finding["status"] == "THREAT"]
    for index, finding in enumerate(threat_findings, start=1):
        evidence_items.append(
            {
                "kind": "threat",
                "type_label": "Security",
                "title": finding["title"],
                "secondary_id": finding["id"],
                "status": "FAIL",
                "badge_label": "Threat",
                "tone": "danger",
                "component": "security",
                "component_label": "Security",
                "component_filter": "other",
                "stage": "",
                "stage_label": "Security",
                "detail": finding["message"],
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [finding],
                "search_text": f"{finding['id']} {finding['title']} {finding['message']} security threat".lower(),
                "_sort_index": index,
            }
        )

    for finding in valid_ux_findings:
        evidence = dict(finding.get("evidence", {}) or {})
        ux_review = dict(evidence.get("ux_review", {}) or {})
        page_name = str(evidence.get("page") or ux_review.get("page") or finding["title"]).strip()
        feedback = _first_non_empty([ux_review.get("feedback"), ux_review.get("improvement_recommendation"), finding.get("message")])
        evidence_items.append(
            {
                "kind": "ux",
                "type_label": "UX review",
                "title": page_name or "UX review",
                "secondary_id": finding["id"],
                "status": "PASS" if str(ux_review.get("status") or "").strip().upper() == "PASS" else "WARN",
                "badge_label": str(ux_review.get("status") or "Review").replace("_", " ").title(),
                "tone": "success" if str(ux_review.get("status") or "").strip().upper() == "PASS" else "warning",
                "component": "browser",
                "component_label": "Browser",
                "component_filter": "browser",
                "stage": "browser",
                "stage_label": "Browser",
                "detail": feedback,
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [finding],
                "search_text": f"{finding['id']} {page_name} {feedback} ux browser".lower(),
            }
        )

    for bev in behavioural_evidence:
        test_id = str(bev.get("test_id") or "").strip()
        if not test_id:
            continue
        bev_status = str(bev.get("status") or "").strip().lower()
        status_map = {"pass": "PASS", "fail": "FAIL", "timeout": "FAIL", "skipped": "SKIPPED", "error": "FAIL"}
        status = status_map.get(bev_status, "SKIPPED")
        duration = bev.get("duration_ms")
        detail_parts = []
        if bev.get("stderr"):
            detail_parts.append(str(bev["stderr"])[:300])
        if duration is not None:
            detail_parts.append(f"Duration: {duration} ms")
        detail = " — ".join(detail_parts) if detail_parts else ""
        evidence_items.append(
            {
                "kind": "behavioural",
                "type_label": "Behavioural",
                "title": _humanize_identifier(test_id),
                "secondary_id": test_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": str(bev.get("component") or "").strip().lower(),
                "component_label": "Behavioural",
                "component_filter": "behavioural",
                "stage": "runtime",
                "stage_label": "Behavioural",
                "detail": detail,
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [],
                "search_text": " ".join([
                    test_id,
                    _humanize_identifier(test_id),
                    str(bev.get("component") or ""),
                    bev_status,
                    detail,
                ]).lower(),
                "_bev": bev,
            }
        )

    evidence_items.sort(
        key=lambda item: (
            _DETAIL_STATUS_PRIORITY.get(item["status"], 99),
            0 if item["kind"] == "requirement" else (1 if item["kind"] == "threat" else 2),
            _DETAIL_COMPONENT_ORDER.index(item["component_filter"]) if item["component_filter"] in _DETAIL_COMPONENT_ORDER else len(_DETAIL_COMPONENT_ORDER),
            str(item["title"]).lower(),
            str(item.get("_sort_index") or 0),
        )
    )

    component_cards = []
    for component, data in component_scores.items():
        summary = dict(data.get("requirement_summary", {}) or {})
        score_value = data.get("score")
        if score_value == "SKIPPED" and int(summary.get("requirement_count", 0) or 0) == 0:
            continue
        numeric_score = _coerce_float(score_value)
        if numeric_score is not None:
            tone = "success" if numeric_score >= 0.7 else ("warning" if numeric_score > 0 else "danger")
            score_label = f"{int(round(numeric_score * 100))}%"
        else:
            tone = "muted"
            score_label = str(score_value or "N/A")
        component_cards.append(
            {
                "key": component,
                "label": _component_label(component),
                "score_label": score_label,
                "tone": tone,
                "summary": summary,
                "detail": (
                    f"{int(summary.get('met', 0) or 0)} met, "
                    f"{int(summary.get('partial', 0) or 0)} partial, "
                    f"{int(summary.get('failed', 0) or 0)} failed"
                    + (
                        f", {int(summary.get('skipped', 0) or 0)} skipped"
                        if int(summary.get("skipped", 0) or 0)
                        else ""
                    )
                    + "."
                ),
            }
        )
    component_cards.sort(
        key=lambda item: _DETAIL_COMPONENT_ORDER.index(item["key"]) if item["key"] in _DETAIL_COMPONENT_ORDER else len(_DETAIL_COMPONENT_ORDER)
    )

    student_issues = [
        item
        for item in evidence_items
        if (
            (item["kind"] == "requirement" and item["required"] and item["status"] in {"FAIL", "PARTIAL"})
            or (item["kind"] == "check" and item["status"] in {"FAIL", "WARN"})
            or item["kind"] == "threat"
        )
    ]
    priority_findings = [
        item
        for item in evidence_items
        if item["kind"] == "requirement" and item["required"] and item["status"] in {"FAIL", "PARTIAL"}
    ]

    limitations: list[dict[str, Any]] = []

    def _push_limitation(title: str, detail: str = "", *, tone: str = "warning", secondary_id: str = "") -> None:
        key = (title.strip().lower(), secondary_id.strip().lower())
        if not title or any((item.get("_key") == key) for item in limitations):
            return
        limitations.append(
            {
                "_key": key,
                "title": title,
                "detail": detail or title,
                "tone": tone,
                "secondary_id": secondary_id,
            }
        )

    run_status = str(run.get("status") or "").strip().lower()
    if run_status == "pending":
        _push_limitation(
            "Rerun is still in progress",
            "This submission is being reprocessed in the background, so the current result is incomplete.",
        )
    elif run_status in {"failed", "error"}:
        _push_limitation(
            "The latest rerun failed",
            "The most recent attempt to reprocess this submission did not complete successfully.",
            tone="danger",
        )

    if bool(run.get("llm_error_flagged")):
        _push_limitation(
            "LLM-assisted review failed",
            str(run.get("llm_error_message") or "An LLM-assisted review step failed, so manual review is required."),
            tone="warning",
            secondary_id="llm",
        )

    if environment and not environment.get("php_available", True):
        _push_limitation(
            "PHP runtime was unavailable",
            "Server-side runtime checks could not be completed because PHP was unavailable in this run.",
            secondary_id="php_unavailable",
        )
    if environment and not environment.get("behavioural_tests_run", True):
        _push_limitation(
            "Runtime checks were unavailable",
            "Behavioural runtime stages did not complete, so dynamic server-side evidence is incomplete.",
            secondary_id="runtime_unavailable",
        )
    if environment and not environment.get("browser_available", True):
        _push_limitation(
            "Browser environment was unavailable",
            "Browser-based checks could not be completed in this run.",
            secondary_id="browser_unavailable",
        )
    if environment and not environment.get("browser_tests_run", True):
        _push_limitation(
            "Browser checks were unavailable",
            "UI and browser-driven evidence was incomplete because the browser stage did not complete.",
            secondary_id="browser_tests_unavailable",
        )

    for flag in list(confidence.get("flags", []) or []):
        text = _CONFIDENCE_FLAG_TEXT.get(str(flag))
        if text:
            _push_limitation(text.rstrip("."), text, secondary_id=str(flag))

    for finding in diagnostics:
        if finding["id"] == "BROWSER.CAPTURE_FAIL":
            _push_limitation(
                "Browser screenshot capture failed",
                finding["message"],
                secondary_id=finding["id"],
            )

    if raw_ux_findings and not browser_reliable:
        _push_limitation(
            "UX review is hidden for this run",
            "Browser capture was not reliable in this assessment result, so UX screenshots and review summaries are withheld to avoid contradictory evidence.",
            secondary_id="ux_hidden",
        )

    limitations = limitations[:6]

    stage_status = []
    stage_status.append({"label": "Static checks", "value": "Completed"})
    stage_status.append(
        {
            "label": "Runtime checks",
            "value": "Completed" if environment.get("behavioural_tests_run", True) else "Unavailable",
        }
    )
    stage_status.append(
        {
            "label": "Browser checks",
            "value": "Completed" if environment.get("browser_tests_run", True) else "Unavailable",
        }
    )

    role_context = []
    for role, paths in sorted((role_mapping.get("roles", {}) or {}).items()):
        clean_paths = [str(path) for path in list(paths or []) if str(path).strip()]
        if clean_paths:
            role_context.append(
                {
                    "title": role.replace("_", " ").title(),
                    "detail": ", ".join(_clean_path(path) for path in clean_paths[:4]),
                }
            )

    decision = _build_decision_summary(run, report_data, confidence, review, limitations, student_issues)

    skipped_requirements = [
        item
        for item in evidence_items
        if item["kind"] == "requirement" and item["status"] == "SKIPPED" and item["required"]
    ]

    return {
        "decision": decision,
        "student_issues": student_issues[:4],
        "limitations": limitations,
        "component_cards": component_cards,
        "assessment_context": {
            "roles": role_context,
            "stages": stage_status,
            "profile": str(score_evidence.get("profile") or run.get("profile") or "Unknown"),
            "evaluation_state": (
                "Partially evaluated"
                if limitations
                else "Fully evaluated"
            ) if report_data else "Awaiting result",
        },
        "priority_findings": priority_findings[:6],
        "evidence_items": evidence_items,
        "debug": {
            "skipped_requirements": skipped_requirements,
            "diagnostics": diagnostics,
            "behavioural_evidence": behavioural_evidence,
            "browser_evidence": browser_evidence,
            "environment": environment,
            "llm_feedback": report_data.get("llm_feedback"),
        },
    }


def _submission_identity(student_id: str | None, assignment_id: str | None) -> tuple[str, str] | None:
    student_val = (student_id or "").strip()
    assignment_val = (assignment_id or "").strip()
    if not student_val or not assignment_val:
        return None
    return assignment_val, student_val


def _discover_pending_batch_submissions(submissions_root: Path, assignment_id: str, upload_timestamp: str) -> list[dict]:
    pending_by_identity: dict[tuple[str, str], dict] = {}

    for item in discover_batch_items(submissions_root):
        is_valid, parsed_student_id, parsed_assignment_id = validate_submission_filename(item.path.name)
        if not is_valid or parsed_assignment_id != assignment_id:
            continue

        student_id = MetadataValidator.sanitize_identifier(parsed_student_id)
        identity = _submission_identity(student_id, assignment_id)
        if identity is None:
            continue

        pending_by_identity[identity] = {
            "submission_id": item.id,
            "student_name": student_id,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": item.path.name,
            "upload_timestamp": upload_timestamp,
            "status": "pending",
        }

    return sorted(pending_by_identity.values(), key=lambda sub: (sub.get("student_id", ""), sub.get("submission_id", "")))


def _batch_report_path(run_dir: Path, record: Mapping[str, object]) -> Path | None:
    report_path = record.get("report_path")
    if isinstance(report_path, str) and report_path:
        candidate = Path(report_path)
        if candidate.exists():
            return candidate

    submission_id = record.get("id")
    if isinstance(submission_id, str) and submission_id:
        candidate = run_dir / "runs" / submission_id / "report.json"
        if candidate.exists():
            return candidate

    return None


def _rebuild_batch_outputs(run_dir: Path, run_info: dict, records: list[dict]) -> None:
    if not records:
        shutil.rmtree(run_dir, ignore_errors=True)
        return

    profile = run_info.get("profile", "frontend")
    write_outputs(run_dir, records, profile=profile)

    updated_run_info = dict(run_info)
    updated_run_info["batch_summary"] = {"records": records}
    save_run_info(run_dir, updated_run_info)
    _write_run_index_batch(run_dir, updated_run_info)
    cleanup_batch_run_storage(run_dir, updated_run_info)


def _replace_existing_submissions(
    runs_root: Path,
    submissions: list[tuple[str, str]],
    *,
    current_run_id: str,
) -> None:
    # Submission attempts are immutable. This compatibility shim intentionally does nothing.
    return


def create_app(config: Mapping[str, object] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024  # security: limit upload size
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        import secrets
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.secret_key = app.config["SECRET_KEY"]
    
    # Web runs are persisted submission records, so startup cleanup must be opt-in.
    if app.config.get("AMS_ENABLE_STARTUP_RUN_CLEANUP", False):
        try:
            from ams.io.workspace import WorkspaceManager

            max_age_hours = app.config.get("AMS_STARTUP_RUN_MAX_AGE_HOURS")
            WorkspaceManager(get_runs_root(app)).cleanup_old_runs(
                max_age_hours=int(max_age_hours) if max_age_hours is not None else None
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Workspace cleanup failed: {e}")
    
    # Register Jinja filters
    app.jinja_env.filters["clean_path"] = _clean_path
    app.jinja_env.filters["format_submission_datetime"] = _format_submission_datetime
    app.jinja_env.globals["render_evidence_value"] = _render_evidence_value

    # ── Sandbox status context processor ─────────────────────────────
    @app.context_processor
    def inject_sandbox_status():
        from ams.sandbox.config import get_sandbox_status
        ctx = {"sandbox_status": get_sandbox_status()}
        try:
            from ams.sandbox.forensics import list_retained_containers
            ctx["threat_containers"] = list_retained_containers()
        except Exception:
            ctx["threat_containers"] = []
        return ctx

    # ── RBAC: initialise database & register blueprints ───────────────
    init_db()
    try:
        sync_attempts_from_storage(get_runs_root(app))
    except Exception as exc:
        logger.warning("Attempt backfill failed during startup: %s", exc)

    from ams.web.auth import auth_bp, inject_user_context
    from ams.web.routes_admin import admin_bp
    from ams.web.routes_teacher import teacher_bp
    from ams.web.routes_student import student_bp
    from ams.web.routes_account import account_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(account_bp)

    app.context_processor(inject_user_context)

    @app.context_processor
    def inject_released_aids():
        """Expose released assignment IDs so the job widget can gate 'View' links."""
        if session.get("user_role") == "student" and session.get("user_id"):
            aids = [
                a["assignmentID"]
                for a in list_assignments_for_student(session["user_id"])
                if a.get("marks_released")
            ]
            return {"released_assignment_ids": aids}
        return {"released_assignment_ids": []}

    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:
    from ams.web.auth import login_required, teacher_or_admin_required, get_current_user

    def _assignment_submission_locked(assignment: Mapping[str, Any] | None) -> bool:
        return bool((assignment or {}).get("marks_released"))

    def _submission_lock_message() -> str:
        return "Grades have already been released for this assignment, so new submissions are locked."

    @app.route("/")
    def home():
        if "user_id" in session and session.get("2fa_verified"):
            user = get_current_user()
            if user:
                if user["role"] == "admin":
                    return redirect(url_for("admin.dashboard"))
                elif user["role"] == "teacher":
                    return redirect(url_for("teacher.dashboard"))
                else:
                    return redirect(url_for("student.dashboard"))
        return redirect(url_for("auth.login"))

    @app.route("/mark", methods=["GET", "POST"])
    @login_required
    def mark():
        def _mark_assignment_context(include_released: bool = False) -> tuple[str, str, str, list[dict], bool]:
            user_role = session.get("user_role", "")
            view_as_role = session.get("view_as_role")
            effective_role = view_as_role or user_role
            user_id = session.get("user_id", "")
            is_preview = False

            if effective_role == "student":
                now = datetime.now().strftime("%Y-%m-%dT%H:%M")
                if user_role == "student":
                    assignments = [
                        assignment
                        for assignment in list_assignments_for_student(user_id)
                        if not assignment.get("due_date") or assignment["due_date"] >= now
                    ]
                else:
                    is_preview = True
                    assignments = [
                        assignment
                        for assignment in list_assignments()
                        if not assignment.get("due_date") or assignment["due_date"] >= now
                    ]
            else:
                assignments = list_assignments() if user_role == "admin" else list_assignments(teacher_id=user_id)

            if not include_released:
                assignments = [
                    assignment
                    for assignment in assignments
                    if not _assignment_submission_locked(assignment)
                ]
            return user_role, effective_role, user_id, assignments, is_preview

        def _render_mark_page(status_code: int = 200, selected_assignment_id: str = ""):
            github_connected = bool(session.get("github_token"))
            github_user = session.get("github_user", "")
            user_role, effective_role, user_id, assignment_options, is_preview = _mark_assignment_context()
            effective_student_id = PREVIEW_STUDENT_ID if is_preview else user_id
            student_assignments = assignment_options if effective_role == "student" else []
            teacher_assignments = assignment_options if effective_role != "student" else []
            return (
                render_template(
                    "mark.html",
                    profiles=PROFILE_CHOICES,
                    github_connected=github_connected,
                    github_user=github_user,
                    user_role=user_role,
                    effective_role=effective_role,
                    user_id=effective_student_id,
                    student_assignments=student_assignments,
                    assignments=teacher_assignments,
                    is_preview=is_preview,
                    selected_assignment_id=selected_assignment_id,
                ),
                status_code,
            )

        if request.method == "GET":
            return _render_mark_page(selected_assignment_id=request.args.get("assignment_id", "").strip())

        # ── Sandbox enforcement ──────────────────────────────────────
        selected_assignment_id = request.form.get("assignment_id", "").strip()
        user_role, effective_role, user_id, assignment_options, is_preview = _mark_assignment_context(include_released=True)
        assignment_map = {
            str(assignment.get("assignmentID") or "").strip(): assignment
            for assignment in assignment_options
        }

        from ams.sandbox.config import get_sandbox_status, get_sandbox_config, SandboxMode
        _sb = get_sandbox_status()
        _cfg = get_sandbox_config()
        if _cfg.mode == SandboxMode.DOCKER and not _sb["enforced"]:
            flash(
                "Sandbox is required but Docker is not available. "
                "Cannot process submissions without sandboxing. "
                f"({_sb['message']})",
                "error",
            )
            return _render_mark_page(status_code=503, selected_assignment_id=selected_assignment_id)

        # GitHub state (needed for error paths and template rendering)
        github_connected = bool(session.get("github_token"))
        github_user = session.get("github_user", "")

        file = request.files.get("submission")
        github_repo = request.form.get("github_repo", "").strip()
        github_branch = request.form.get("github_branch", "").strip()
        student_id = request.form.get("student_id", "").strip()
        assignment_id = request.form.get("assignment_id", "").strip()
        scoring_mode_str = request.form.get("scoring_mode", "static_plus_llm").strip()

        # ── Determine submission source (ZIP upload vs GitHub) ──
        using_github = bool(github_repo)
        tmp_zip_path: Path | None = None
        run_dir: Path | None = None
        attempt_id = ""
        attempt_number = 0

        if using_github:
            # ------ GitHub submission path ------
            github_token = session.get("github_token")
            if not github_token:
                flash("Please link your GitHub account first.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            # Validate repo format (owner/repo)
            if "/" not in github_repo or github_repo.count("/") != 1:
                flash("Invalid GitHub repository format. Use owner/repo.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            branch_suffix = f"_{github_branch}" if github_branch else ""
            original_filename = f"{github_repo.replace('/', '_')}{branch_suffix}.zip"

        else:
            # ------ ZIP upload path ------
            if not file or not file.filename:
                flash("Please upload a .zip file or select a GitHub repository.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            if not validate_file_type(file.filename):
                flash("Invalid file type. Please upload a .zip file.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            original_filename = MetadataValidator.sanitize_filename(file.filename)

        # ── Strict ZIP content validation (magic-byte check) ─────
        # Validate and convert scoring mode
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            flash(f"Invalid scoring mode: {scoring_mode_str}", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            flash(f"Invalid Assignment ID: {assignment_error}", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
        
        # Sanitize identifiers
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        selected_assignment_id = assignment_id
        assignment = assignment_map.get(assignment_id)
        if assignment is None:
            flash("Select a valid assignment from the list.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
        if _assignment_submission_locked(assignment):
            flash(_submission_lock_message(), "error")
            return _render_mark_page(status_code=403, selected_assignment_id=selected_assignment_id)

        if effective_role == "student":
            student_id = PREVIEW_STUDENT_ID if is_preview else user_id
        else:
            student_id = request.form.get("student_id", "").strip()

        valid_student, student_error = MetadataValidator.validate_student_id(student_id)
        if not valid_student:
            flash(f"Invalid Student ID: {student_error}", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        student_id = MetadataValidator.sanitize_identifier(student_id)
        profile = str(assignment.get("profile") or "frontend_interactive").strip()
        original_filename = MetadataValidator.sanitize_filename(original_filename)
        
        uploader_extra: dict = {
            "ip_address": request.remote_addr or "unknown",
            "user_agent": request.headers.get("User-Agent", "unknown")[:200],
        }
        if using_github:
            uploader_extra["source"] = "github"
            uploader_extra["github_repo"] = github_repo

        metadata = SubmissionMetadata(
            student_id=student_id,
            assignment_id=assignment_id,
            timestamp=datetime.now(timezone.utc),
            original_filename=original_filename,
            uploader_metadata=uploader_extra,
        )
        
        runs_root = get_runs_root(app)
        created_at = utc_now_iso()
        source_type = (
            "student_github_submission"
            if using_github and effective_role == "student"
            else "teacher_github_submission"
            if using_github
            else "student_zip_upload"
            if effective_role == "student"
            else "teacher_upload"
        )
        source_ref = github_repo if using_github else ""
        attempt = create_attempt(
            assignment_id=assignment_id,
            student_id=student_id,
            source_type=source_type,
            source_actor_user_id=str(user_id or ""),
            original_filename=original_filename,
            source_ref=source_ref,
            created_at=created_at,
            submitted_at=created_at,
        )
        attempt_id = str(attempt.get("id") or "")
        attempt_number = int(attempt.get("attempt_number") or 0)
        run_id = str(attempt.get("run_id") or attempt_id)
        run_dir = create_attempt_storage_dir(runs_root, assignment_id, student_id, attempt_number, attempt_id)
        update_attempt(attempt_id, run_dir=str(run_dir))

        initial_run_info = {
            "id": run_id,
            "mode": "mark",
            "profile": profile,
            "scoring_mode": scoring_mode.value,
            "created_at": created_at,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": original_filename,
            "source": "github" if using_github else "upload",
            "source_type": source_type,
            "source_actor_user_id": str(user_id or ""),
            "source_ref": source_ref,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "ingestion_status": "pending",
            "pipeline_status": "pending",
            "validity_status": "pending",
            "status": "pending",
        }
        if using_github:
            initial_run_info["github_repo"] = github_repo
        save_run_info(run_dir, initial_run_info)
        
        try:
            if using_github:
                try:
                    if github_branch:
                        zipball_url = f"https://api.github.com/repos/{github_repo}/zipball/{github_branch}"
                    else:
                        zipball_url = f"https://api.github.com/repos/{github_repo}/zipball"
                    gh_resp = _requests.get(
                        zipball_url,
                        headers={
                            "Authorization": f"Bearer {github_token}",
                            "Accept": "application/vnd.github+json",
                        },
                        stream=True,
                        timeout=60,
                    )
                    gh_resp.raise_for_status()
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                        for chunk in gh_resp.iter_content(chunk_size=8192):
                            tmp_file.write(chunk)
                        tmp_zip_path = Path(tmp_file.name)
                except _requests.RequestException as exc:
                    error_message = f"Failed to download repository from GitHub: {exc}"
                    logger.warning("GitHub zipball download failed for %s: %s", github_repo, exc)
                    failed_info = dict(
                        initial_run_info,
                        status="failed",
                        ingestion_status="failed",
                        pipeline_status="failed",
                        validity_status="invalid",
                        error=error_message,
                    )
                    save_run_info(run_dir, failed_info)
                    update_attempt(
                        attempt_id,
                        ingestion_status="failed",
                        pipeline_status="failed",
                        validity_status="invalid",
                        error_message=error_message,
                    )
                    recompute_active_attempt(runs_root, assignment_id, student_id)
                    flash(error_message, "error")
                    return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
            else:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                    file.save(tmp_file.name)
                    tmp_zip_path = Path(tmp_file.name)

            # Validate file size
            valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
            if not valid_size:
                error_message = size_error or "File size exceeds maximum limit."
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error=error_message,
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message=error_message,
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                flash(error_message)
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
            
            if not validate_is_zipfile(tmp_zip_path):
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error="The uploaded file is not a valid ZIP archive.",
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message="The uploaded file is not a valid ZIP archive.",
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                flash("The uploaded file is not a valid ZIP archive.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            upload_zip = run_dir / original_filename
            shutil.copy2(tmp_zip_path, upload_zip)
            update_attempt(attempt_id, ingestion_status="stored")

            # Extract for processing
            extracted = run_dir / "uploaded_extract"
            extracted.mkdir(parents=True, exist_ok=True)
            try:
                safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            except Exception as exc:
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error=str(exc),
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message=str(exc),
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                flash(f"Failed to extract ZIP archive: {exc}", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
            
            # ── Find true root of submission (bypassing macOS folders or zip wrappers)
            submission_root = find_submission_root(extracted)
            update_attempt(attempt_id, ingestion_status="completed")
            
            # ── Zero-content guard ───────────────────────────────────────
            # Reject the submission instantly if there are zero relevant web files
            SUPPORTED_EXTENSIONS = {".html", ".css", ".js", ".php", ".sql"}
            has_web_files = any(
                f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS 
                for f in submission_root.rglob("*")
            )

            if not has_web_files:
                error_message = "No web development files (HTML, CSS, JS, PHP, SQL) were found in this repository. Please select the correct repository."
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="completed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error=error_message,
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    ingestion_status="completed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message=error_message,
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                flash("No web development files (HTML, CSS, JS, PHP, SQL) were found in this repository. Please select the correct repository.", "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

            pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
            
            # Pass metadata to pipeline via context
            app.logger.debug(
                "mark run extract complete",
                extra={
                    "upload_zip": str(upload_zip),
                    "extracted": str(extracted),
                    "submission_root": str(submission_root),
                    "profile": profile,
                    "student_id": student_id,
                    "assignment_id": assignment_id,
                    "source": "github" if using_github else "upload",
                },
            )
            
            # ── Background execution ─────────────────────────────────
            # Heavy pipeline work is submitted to the thread pool so the
            # HTTP request returns immediately with a job ID.
            meta_dict = metadata.to_dict()
            meta_dict.update(
                {
                    "attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                    "source_type": source_type,
                    "source_actor_user_id": str(user_id or ""),
                    "created_at": created_at,
                    "submitted_at": created_at,
                }
            )

            def _run_mark_job() -> dict:
                """Executed in the thread pool."""
                try:
                    report_path = pipeline.run(
                        submission_path=submission_root,
                        workspace_path=run_dir,
                        profile=profile,
                        metadata=meta_dict,
                    )
                    report_data = json.loads(report_path.read_text(encoding="utf-8"))
                    review_flags = extract_review_flags_from_report(report_data)
                    score_evidence = report_data.get("score_evidence", {}) or {}
                    confidence = (
                        str((score_evidence.get("confidence", {}) or {}).get("level") or "")
                        or str((report_data.get("summary", {}) or {}).get("confidence") or "")
                    )
                    manual_review_required = bool((score_evidence.get("review", {}) or {}).get("recommended"))
                    llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
                    pipeline_status = "failed" if llm_error_flagged else "completed"
                    validity_status = "invalid" if llm_error_flagged else "valid"
                    run_info = {
                        "id": run_id,
                        "mode": "mark",
                        "profile": profile,
                        "scoring_mode": scoring_mode.value,
                        "created_at": initial_run_info["created_at"],
                        "report": report_path.name,
                        "summary": "summary.txt",
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": original_filename,
                        "source": "github" if using_github else "upload",
                        "source_type": source_type,
                        "source_actor_user_id": str(user_id or ""),
                        "source_ref": source_ref,
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "ingestion_status": "completed",
                        "pipeline_status": pipeline_status,
                        "validity_status": validity_status,
                        "confidence": confidence,
                        "manual_review_required": manual_review_required,
                        "status": "llm_error" if llm_error_flagged else "completed",
                        "threat_flagged": bool(review_flags.get("threat_flagged")),
                        "threat_count": int(review_flags.get("threat_count") or 0),
                        "llm_error_flagged": llm_error_flagged,
                        "llm_error_message": review_flags.get("llm_error_message"),
                        "llm_error_messages": list(review_flags.get("llm_error_messages") or []),
                    }
                    if using_github:
                        run_info["github_repo"] = github_repo
                    save_run_info(run_dir, run_info)
                    _write_run_index_mark(run_dir, run_info, report_path)
                    update_attempt(
                        attempt_id,
                        run_dir=str(run_dir),
                        report_path=str(report_path),
                        ingestion_status="completed",
                        pipeline_status=pipeline_status,
                        validity_status=validity_status,
                        overall_score=(report_data.get("scores", {}) or {}).get("overall"),
                        confidence=confidence,
                        manual_review_required=manual_review_required,
                        error_message=str(review_flags.get("llm_error_message") or ""),
                    )
                    recompute_active_attempt(runs_root, assignment_id, student_id)
                    return {"run_id": run_id}
                except Exception as exc:
                    failed_info = dict(
                        initial_run_info,
                        status="failed",
                        ingestion_status="completed",
                        pipeline_status="failed",
                        validity_status="invalid",
                        error=str(exc),
                    )
                    save_run_info(run_dir, failed_info)
                    update_attempt(
                        attempt_id,
                        run_dir=str(run_dir),
                        ingestion_status="completed",
                        pipeline_status="failed",
                        validity_status="invalid",
                        error_message=str(exc),
                    )
                    recompute_active_attempt(runs_root, assignment_id, student_id)
                    raise

            job_id = job_manager.submit_job("single_mark", _run_mark_job)
            return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
        finally:
            # Clean up temporary file
            try:
                if tmp_zip_path is not None:
                    tmp_zip_path.unlink()
            except Exception:
                pass

    @app.route("/batch", methods=["GET", "POST"])
    @teacher_or_admin_required
    def batch():
        def _available_batch_assignments(include_released: bool = False) -> list[dict]:
            if session.get("user_role") == "admin":
                assignments = list_assignments()
            else:
                assignments = list_assignments(teacher_id=session.get("user_id"))
            if not include_released:
                assignments = [
                    assignment
                    for assignment in assignments
                    if not _assignment_submission_locked(assignment)
                ]
            return assignments

        assignment_options = _available_batch_assignments()
        selected_assignment_id = request.form.get("assignment_id", "").strip() if request.method == "POST" else ""

        if request.method == "GET":
            github_connected = bool(session.get("github_token"))
            github_user = session.get("github_user", "")
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
            )

        # ── Sandbox enforcement ──────────────────────────────────────
        from ams.sandbox.config import get_sandbox_status, get_sandbox_config, SandboxMode
        _sb = get_sandbox_status()
        _cfg = get_sandbox_config()
        if _cfg.mode == SandboxMode.DOCKER and not _sb["enforced"]:
            flash(
                "Sandbox is required but Docker is not available. "
                "Cannot process submissions without sandboxing. "
                f"({_sb['message']})",
                "error",
            )
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=bool(session.get("github_token")),
                github_user=session.get("github_user", ""),
            ), 503

        file = request.files.get("submission")
        assignment_id = request.form.get("assignment_id", "").strip()
        scoring_mode = ScoringMode("static_plus_llm")  # Always use static + LLM
        github_connected = bool(session.get("github_token"))
        github_user = session.get("github_user", "")
        assignment_options_all = _available_batch_assignments(include_released=True)
        assignment_map = {
            str(assignment.get("assignmentID") or "").strip(): assignment
            for assignment in assignment_options_all
        }
        
        # ── Determine submission source (ZIP upload vs GitHub) ──
        submission_method = request.form.get("submission_method", "upload")
        github_repo = request.form.get("github_repo", "").strip()
        github_branch = request.form.get("github_branch", "").strip()
        using_github = (submission_method == "github") and bool(github_repo)
        
        tmp_zip_path: Path | None = None
        
        if using_github:
            # ------ GitHub submission path ------
            github_token = session.get("github_token")
            if not github_token:
                flash("Please link your GitHub account first.", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=False,
                    github_user=github_user,
                ), 400

            # Validate repo format (owner/repo)
            if "/" not in github_repo or github_repo.count("/") != 1:
                flash("Invalid GitHub repository format. Use owner/repo.", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400

            try:
                # Branch-specific zipball
                if github_branch:
                    zipball_url = f"https://api.github.com/repos/{github_repo}/zipball/{github_branch}"
                else:
                    zipball_url = f"https://api.github.com/repos/{github_repo}/zipball"
                gh_resp = _requests.get(
                    zipball_url,
                    headers={
                        "Authorization": f"Bearer {github_token}",
                        "Accept": "application/vnd.github+json",
                    },
                    stream=True,
                    timeout=60,
                )
                gh_resp.raise_for_status()
            except _requests.RequestException as exc:
                logger.warning("GitHub zipball download failed for %s: %s", github_repo, exc)
                flash(f"Failed to download repository from GitHub: {exc}", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400

            # Save to a temporary ZIP file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                for chunk in gh_resp.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = Path(tmp_file.name)

            branch_suffix = f"_{github_branch}" if github_branch else ""
            original_filename = f"{github_repo.replace('/', '_')}{branch_suffix}.zip"

        else:
            # ------ ZIP upload path ------
            if not file or not file.filename:
                flash("Please upload a .zip file or select a GitHub repository.", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400
            
            if not validate_file_type(file.filename):
                flash("Invalid file type. Please upload a .zip file.", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400
            
            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                file.save(tmp_file.name)
                tmp_zip_path = Path(tmp_file.name)

            original_filename = MetadataValidator.sanitize_filename(file.filename)
        
        # Validate and resolve assignment
        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            if tmp_zip_path.exists():
                try:
                    tmp_zip_path.unlink()
                except Exception:
                    pass
            flash(f"Invalid Assignment ID: {assignment_error}", "error")
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
            ), 400
        
        # Sanitize
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        assignment = assignment_map.get(assignment_id)
        if assignment is None:
            if tmp_zip_path.exists():
                try:
                    tmp_zip_path.unlink()
                except Exception:
                    pass
            flash("Select a valid assignment from the list.", "error")
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
            ), 400
        if _assignment_submission_locked(assignment):
            if tmp_zip_path and tmp_zip_path.exists():
                try:
                    tmp_zip_path.unlink()
                except Exception:
                    pass
            flash(_submission_lock_message(), "error")
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
            ), 403
        profile = str(assignment.get("profile") or "frontend_interactive").strip()
        
        runs_root = get_runs_root(app)
        
        # ── Strict ZIP content validation (magic-byte check) ─────
        if not validate_is_zipfile(tmp_zip_path):
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass
            flash("The uploaded file is not a valid ZIP archive.", "error")
            return render_template(
                "batch.html",
                assignments=assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
            ), 400

        try:
            # Validate file size
            valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
            if not valid_size:
                flash(size_error or "File size exceeds maximum limit.", "error")
                return render_template(
                    "batch.html",
                    assignments=assignment_options,
                    selected_assignment_id=selected_assignment_id,
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400
            
            uploader_extra: dict = {
                "ip_address": request.remote_addr or "unknown",
                "user_agent": request.headers.get("User-Agent", "unknown")[:200],
            }
            if using_github:
                uploader_extra["source"] = "github"
                uploader_extra["github_repo"] = github_repo

            batch_metadata = SubmissionMetadata(
                student_id="batch",  # Special identifier for batch runs
                assignment_id=assignment_id,
                timestamp=datetime.now(timezone.utc),
                original_filename=original_filename,
                uploader_metadata=uploader_extra,
            )
            
            run_id, run_dir = create_run_dir(
                runs_root=runs_root,
                mode="batch",
                profile=profile,
                metadata=batch_metadata,
            )
            
            # Store batch zip
            upload_zip = run_dir / original_filename
            shutil.copy2(tmp_zip_path, upload_zip)
            
            # Save batch metadata
            save_metadata(run_dir, batch_metadata)
            
            extracted = run_dir / "batch_inputs"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            batch_inputs_root = find_submission_root(extracted)
            created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            pending_submissions = _discover_pending_batch_submissions(
                batch_inputs_root,
                assignment_id,
                created_at,
            )
            
            # Run batch with metadata context — off the request thread
            initial_run_info = {
                "id": run_id,
                "mode": "batch",
                "profile": profile,
                "scoring_mode": scoring_mode.value,
                "created_at": created_at,
                "assignment_id": assignment_id,
                "original_filename": original_filename,
                "source": "github" if using_github else "upload",
                "status": "pending",
                "pending_submissions": pending_submissions,
            }
            if using_github:
                initial_run_info["github_repo"] = github_repo
            save_run_info(run_dir, initial_run_info)
            _replace_existing_submissions(
                runs_root,
                [
                    (
                        str(submission.get("assignment_id") or ""),
                        str(submission.get("student_id") or ""),
                    )
                    for submission in pending_submissions
                ],
                current_run_id=run_id,
            )

            def _run_batch_job() -> dict:
                """Executed in the thread pool."""
                try:
                    batch_data = run_batch(
                        submissions_dir=extracted,
                        out_root=run_dir,
                        profile=profile,
                        keep_individual_runs=True,
                        assignment_id=assignment_id,
                        scoring_mode=scoring_mode,
                    )
                    run_info = {
                        "id": run_id,
                        "mode": "batch",
                        "profile": profile,
                        "scoring_mode": scoring_mode.value,
                        "created_at": initial_run_info["created_at"],
                        "summary": "batch_summary.json",
                        "batch_summary": batch_data,
                        "assignment_id": assignment_id,
                        "original_filename": original_filename,
                        "source": "github" if using_github else "upload",
                        "status": "completed",
                    }
                    if using_github:
                        run_info["github_repo"] = github_repo

                    save_run_info(run_dir, run_info)
                    _write_run_index_batch(run_dir, run_info)
                    cleanup_batch_run_storage(run_dir, run_info)
                    return {"run_id": run_id}
                except Exception as exc:
                    failed_info = dict(initial_run_info, status="failed", error=str(exc))
                    save_run_info(run_dir, failed_info)
                    raise

            job_id = job_manager.submit_job("batch_mark", _run_batch_job)
            return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
        finally:
            # Clean up temporary file
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass

    # ── Job polling API ──────────────────────────────────────────────
    @app.route("/api/jobs/<job_id>")
    def job_status(job_id: str):
        """Return the current state of a background job as JSON."""
        status = job_manager.get_job_status(job_id)
        if status is None:
            return jsonify({"error": "Job not found"}), 404
        # Convert Path results to strings for JSON serialisation
        result = status.get("result")
        if isinstance(result, dict):
            status["result"] = {
                k: str(v) if hasattr(v, "__fspath__") else v
                for k, v in result.items()
            }
        elif hasattr(result, "__fspath__"):
            status["result"] = str(result)
        return jsonify(status)

    @app.route("/runs")
    @login_required
    def runs():
        runs_root = get_runs_root(app)
        all_runs = list_runs(runs_root, only_active=False)
        mode_filter = request.args.get("mode") or ""
        profile_filter = request.args.get("profile") or ""
        query = request.args.get("q") or ""

        def _match(run: dict) -> bool:
            if mode_filter and run.get("mode") != mode_filter:
                return False
            if profile_filter and run.get("profile") != profile_filter:
                return False
            if query and query.lower() not in run.get("id", "").lower():
                subs = run.get("submissions", []) or []
                hit = False
                for sub in subs:
                    for key in ["submission_id", "student_name", "student_id", "original_filename"]:
                        val = sub.get(key)
                        if isinstance(val, str) and query.lower() in val.lower():
                            hit = True
                            break
                    if hit:
                        break
                if not hit:
                    return False
            return True

        filtered = [r for r in all_runs if _match(r)]
        return render_template(
            "runs.html",
            runs=filtered,
            mode_filter=mode_filter,
            profile_filter=profile_filter,
            query=query,
        )

    @app.route("/runs/<run_id>/delete", methods=["POST"])
    @teacher_or_admin_required
    def delete_run(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            flash("Run not found.", "error")
            return redirect(url_for("runs"))
        shutil.rmtree(run_dir, ignore_errors=True)
        flash(f"Run '{run_id[:24]}…' deleted.", "success")
        return redirect(url_for("runs"))

    def _assignment_has_unresolved_release_blockers(assignment_id: str) -> bool:
        for run in list_runs(get_runs_root(app)):
            if run.get("mode") == "batch":
                for submission in list(run.get("submissions", []) or []):
                    if str(submission.get("assignment_id") or "").strip() != assignment_id:
                        continue
                    if submission.get("invalid") is True:
                        continue
                    if str(submission.get("status") or "").strip().lower().startswith("invalid"):
                        continue
                    if (
                        submission.get("threat_flagged")
                        or submission.get("threat_count")
                        or submission.get("llm_error_flagged")
                    ):
                        return True
                continue
            if (
                str(run.get("assignment_id") or "").strip() == assignment_id
                and (run.get("threat_flagged") or run.get("llm_error_flagged"))
            ):
                return True
        return False

    def _user_can_access_assignment(assignment_id: str) -> bool:
        user = get_current_user()
        assignment = get_assignment(assignment_id)
        if user is None or assignment is None:
            return False
        return assignment_allows_teacher(assignment, user["userID"], user["role"])

    def _flash_assignment_review_state(assignment_id: str, resolved_message: str) -> None:
        flash(resolved_message, "success")
        if _assignment_has_unresolved_release_blockers(assignment_id):
            flash(
                "Other flagged submissions still need attention before grades can be released.",
                "warning",
            )
        else:
            flash("No flagged submissions remain. Grades can now be released.", "success")

    def _load_batch_summary_records(run_dir: Path) -> dict | None:
        summary_path = run_dir / "batch_summary.json"
        if not summary_path.exists():
            return None
        try:
            batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(batch_summary, dict):
            return None
        records = batch_summary.get("records", []) or []
        if not isinstance(records, list):
            return None
        batch_summary["records"] = list(records)
        return batch_summary

    def _persist_batch_outputs(run_dir: Path, run_info: dict, records: list[dict]) -> dict:
        profile = str(run_info.get("profile") or "frontend")
        write_outputs(run_dir, records, profile=profile)
        updated_batch_summary = _load_batch_summary_records(run_dir) or {"records": list(records)}
        updated_run_info = dict(run_info)
        updated_run_info["batch_summary"] = updated_batch_summary
        updated_run_info["summary"] = "batch_summary.json"
        save_run_info(run_dir, updated_run_info)
        _write_run_index_batch(run_dir, updated_run_info)
        cleanup_batch_run_storage(run_dir, updated_run_info)
        return updated_run_info

    def _safe_delete_within_run(run_dir: Path, candidate: Path | str | None) -> None:
        if not candidate:
            return
        path = Path(candidate)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        try:
            path.relative_to(run_dir.resolve())
        except Exception:
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _rerun_timestamp(value: object) -> datetime:
        text = str(value or "").strip()
        if text:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _build_rerun_metadata(
        *,
        student_id: object,
        assignment_id: object,
        original_filename: object,
        timestamp: object,
        source: object = "",
        github_repo: object = "",
    ) -> dict:
        uploader_extra: dict[str, str] = {}
        if str(source or "").strip():
            uploader_extra["source"] = str(source)
        if str(github_repo or "").strip():
            uploader_extra["github_repo"] = str(github_repo)
        metadata = SubmissionMetadata(
            student_id=MetadataValidator.sanitize_identifier(str(student_id or "unknown")),
            assignment_id=MetadataValidator.sanitize_identifier(str(assignment_id or "unknown_assignment")),
            timestamp=_rerun_timestamp(timestamp),
            original_filename=MetadataValidator.sanitize_filename(str(original_filename or "submission.zip")),
            uploader_metadata=uploader_extra,
        )
        return metadata.to_dict()

    def _build_pipeline(run_info: Mapping[str, object]) -> AssessmentPipeline:
        scoring_mode_str = str(run_info.get("scoring_mode") or "static_plus_llm")
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            scoring_mode = ScoringMode("static_plus_llm")
        return AssessmentPipeline(scoring_mode=scoring_mode)

    def _clear_rerun_outputs(workspace_path: Path) -> None:
        for filename in ("report.json", "report.html", "summary.txt"):
            _safe_delete_within_run(workspace_path, workspace_path / filename)
        for dirname in ("artifacts", "evaluation", "reports"):
            _safe_delete_within_run(workspace_path, workspace_path / dirname)

    def _prepare_source_tree(source_root: Path, staging_root: Path) -> Path:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
        shutil.copytree(source_root, staging_root, dirs_exist_ok=True)
        return find_submission_root(staging_root)

    def _prepare_zip_source(zip_path: Path, extract_root: Path) -> Path:
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(zip_path, extract_root, max_size_mb=MAX_UPLOAD_MB)
        return find_submission_root(extract_root)

    def _resolve_mark_rerun_source(run_dir: Path, run_info: Mapping[str, object]) -> Path:
        uploaded_extract = run_dir / "uploaded_extract"
        if uploaded_extract.exists():
            return find_submission_root(uploaded_extract)

        original_filename = str(run_info.get("original_filename") or "").strip()
        if original_filename:
            upload_zip = run_dir / original_filename
            if upload_zip.exists():
                return _prepare_zip_source(upload_zip, run_dir / "rerun_source")

        zip_candidates = list(run_dir.glob("*.zip"))
        if zip_candidates:
            return _prepare_zip_source(zip_candidates[0], run_dir / "rerun_source")

        submission_root = run_dir / "submission"
        if submission_root.exists():
            return _prepare_source_tree(submission_root, run_dir / "rerun_source")

        raise FileNotFoundError("Stored submission content is unavailable.")

    def _resolve_batch_rerun_source(run_dir: Path, submission_id: str, record: Mapping[str, object]) -> tuple[Path, Path]:
        submission_dir = run_dir / "runs" / submission_id
        submission_root = submission_dir / "submission"
        if submission_root.exists():
            return submission_dir, _prepare_source_tree(submission_root, submission_dir / "rerun_source")

        extracted_dir = submission_dir / "extracted"
        if extracted_dir.exists():
            return submission_dir, find_submission_root(extracted_dir)

        source_value = str(record.get("path") or "").strip()
        if source_value:
            source_path = Path(source_value)
            if not source_path.is_absolute():
                source_path = (Path.cwd() / source_path).resolve()
            else:
                source_path = source_path.resolve()
            if source_path.is_file():
                return submission_dir, _prepare_zip_source(source_path, submission_dir / "rerun_source")
            if source_path.is_dir():
                return submission_dir, _prepare_source_tree(source_path, submission_dir / "rerun_source")

        raise FileNotFoundError("Stored submission content is unavailable.")

    def _apply_batch_report(record: dict, report_path: Path, report: Mapping[str, object], assignment_id: str) -> None:
        meta = report.get("metadata", {}) or {}
        submission_meta = meta.get("submission_metadata", {}) or {}
        scores = report.get("scores", {}) or {}
        by_component = scores.get("by_component", {}) or {}
        review_flags = extract_review_flags_from_report(report)

        record["report_path"] = str(report_path)
        record["student_id"] = submission_meta.get("student_id") or record.get("student_id")
        record["assignment_id"] = submission_meta.get("assignment_id") or record.get("assignment_id") or assignment_id
        record["original_filename"] = submission_meta.get("original_filename") or record.get("original_filename")
        record["overall"] = scores.get("overall")
        record["components"] = {
            component: ((by_component.get(component) or {}).get("score"))
            for component in ("html", "css", "js", "php", "sql", "api")
        }
        llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
        record["status"] = "llm_error" if llm_error_flagged else "ok"
        record["pipeline_status"] = "failed" if llm_error_flagged else "completed"
        record["validity_status"] = "invalid" if llm_error_flagged else "valid"
        record["rerun_pending"] = False
        record["invalid"] = False
        record.pop("error", None)
        record.pop("validation_error", None)
        record["threat_flagged"] = bool(review_flags.get("threat_flagged"))
        if review_flags.get("threat_count"):
            record["threat_count"] = int(review_flags.get("threat_count") or 0)
        else:
            record.pop("threat_count", None)
        record["llm_error_flagged"] = llm_error_flagged
        record["llm_error_message"] = review_flags.get("llm_error_message")
        record["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])

    def _rerun_mark_submission(run_dir: Path, run_info: Mapping[str, object]) -> dict:
        submission_source = _resolve_mark_rerun_source(run_dir, run_info)
        metadata = _build_rerun_metadata(
            student_id=run_info.get("student_id"),
            assignment_id=run_info.get("assignment_id"),
            original_filename=run_info.get("original_filename"),
            timestamp=run_info.get("created_at"),
            source=run_info.get("source"),
            github_repo=run_info.get("github_repo"),
        )
        _clear_rerun_outputs(run_dir)
        try:
            report_path = _build_pipeline(run_info).run(
                submission_path=submission_source,
                workspace_path=run_dir,
                profile=str(run_info.get("profile") or "frontend"),
                metadata=metadata,
                skip_threat_scan=True,
            )
        except Exception as exc:
            failed_run_info = dict(run_info)
            failed_run_info["status"] = "failed"
            failed_run_info["error"] = str(exc)
            failed_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            failed_run_info.pop("rerun_pending", None)
            save_run_info(run_dir, failed_run_info)
            raise
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        review_flags = extract_review_flags_from_report(report_data)
        llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
        updated_run_info = dict(run_info)
        updated_run_info["report"] = report_path.name
        updated_run_info["summary"] = "summary.txt"
        updated_run_info["status"] = "llm_error" if llm_error_flagged else "completed"
        updated_run_info["pipeline_status"] = "failed" if llm_error_flagged else "completed"
        updated_run_info["validity_status"] = "invalid" if llm_error_flagged else "valid"
        updated_run_info["threat_flagged"] = bool(review_flags.get("threat_flagged"))
        if review_flags.get("threat_count"):
            updated_run_info["threat_count"] = int(review_flags.get("threat_count") or 0)
        else:
            updated_run_info.pop("threat_count", None)
        updated_run_info["llm_error_flagged"] = llm_error_flagged
        updated_run_info["llm_error_message"] = review_flags.get("llm_error_message")
        updated_run_info["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
        updated_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        updated_run_info.pop("rerun_pending", None)
        updated_run_info.pop("error", None)
        updated_run_info.pop("threat_override", None)
        updated_run_info.pop("threat_override_at", None)
        save_run_info(run_dir, updated_run_info)
        _write_run_index_mark(run_dir, updated_run_info, report_path)
        runs_root = get_runs_root(app)
        assignment_id = str(updated_run_info.get("assignment_id") or "")
        student_id = str(updated_run_info.get("student_id") or "")
        if assignment_id and student_id:
            sync_attempts_from_storage(runs_root)
            recompute_active_attempt(runs_root, assignment_id, student_id)
        return updated_run_info

    def _rerun_batch_submission(run_dir: Path, run_info: Mapping[str, object], submission_id: str) -> dict:
        batch_summary = _load_batch_summary_records(run_dir)
        if batch_summary is None:
            raise FileNotFoundError("Batch summary could not be loaded.")
        records = list(batch_summary.get("records", []) or [])
        target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
        if target is None:
            raise FileNotFoundError("Batch submission record not found.")

        submission_dir, submission_source = _resolve_batch_rerun_source(run_dir, submission_id, target)
        metadata = _build_rerun_metadata(
            student_id=target.get("student_id"),
            assignment_id=target.get("assignment_id") or run_info.get("assignment_id"),
            original_filename=target.get("original_filename"),
            timestamp=target.get("upload_timestamp") or run_info.get("created_at"),
            source=run_info.get("source"),
            github_repo=run_info.get("github_repo"),
        )
        _clear_rerun_outputs(submission_dir)
        try:
            report_path = _build_pipeline(run_info).run(
                submission_path=submission_source,
                workspace_path=submission_dir,
                profile=str(run_info.get("profile") or "frontend"),
                metadata=metadata,
                skip_threat_scan=True,
            )
        except Exception as exc:
            target["report_path"] = None
            target["status"] = "error"
            target["rerun_pending"] = False
            target["overall"] = None
            target["components"] = {
                component: None for component in ("html", "css", "js", "php", "sql")
            }
            target["error"] = str(exc)
            target["invalid"] = False
            failed_run_info = dict(run_info)
            failed_run_info["status"] = "failed"
            failed_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            _persist_batch_outputs(run_dir, failed_run_info, records)
            raise
        report = json.loads(report_path.read_text(encoding="utf-8"))
        _apply_batch_report(target, report_path, report, str(run_info.get("assignment_id") or ""))
        updated_run_info = dict(run_info)
        updated_run_info["status"] = "completed"
        updated_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        updated_run_info.pop("error", None)
        updated_run_info = _persist_batch_outputs(run_dir, updated_run_info, records)
        save_run_info(run_dir, updated_run_info)
        runs_root = get_runs_root(app)
        assignment_id = str(target.get("assignment_id") or run_info.get("assignment_id") or "")
        student_id = str(target.get("student_id") or "")
        if assignment_id and student_id:
            sync_attempts_from_storage(runs_root)
            recompute_active_attempt(runs_root, assignment_id, student_id)
        return target

    def _is_async_job_request() -> bool:
        return request.headers.get("X-AMS-Async") == "1"

    def _build_rerun_job_response(
        *,
        job_id: str,
        run_id: str,
        label: str,
        assignment_id: str,
        view_url: str,
        refresh_url: str,
    ):
        payload = {
            "job_id": job_id,
            "status": "accepted",
            "run_id": run_id,
            "assignment_id": assignment_id,
            "label": label,
            "view_url": view_url,
            "refresh_url": refresh_url,
        }
        if _is_async_job_request():
            return jsonify(payload), 202
        flash(f"{label} queued. The submission will update when background processing finishes.", "success")
        return redirect(refresh_url)

    def _queue_mark_submission_rerun(
        run_dir: Path,
        run_info: Mapping[str, object],
        *,
        view_url: str,
        refresh_url: str,
    ):
        current_status = str(run_info.get("status") or "").strip().lower()
        if current_status == "pending":
            raise RuntimeError("Submission is already queued for rerun.")

        queued_run_info = dict(run_info)
        if "threat_flagged" not in queued_run_info or "llm_error_flagged" not in queued_run_info:
            report_path = run_dir / str(run_info.get("report") or "report.json")
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    review_flags = extract_review_flags_from_report(report)
                    queued_run_info["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                    queued_run_info["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                    queued_run_info["llm_error_message"] = review_flags.get("llm_error_message")
                    queued_run_info["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
                except Exception:
                    pass
        queued_run_info["status"] = "pending"
        queued_run_info["rerun_pending"] = True
        queued_run_info["last_rerun_requested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued_run_info.pop("error", None)
        save_run_info(run_dir, queued_run_info)

        def _run_mark_rerun_job() -> dict:
            updated = _rerun_mark_submission(run_dir, queued_run_info)
            return {
                "run_id": str(updated.get("id") or run_dir.name),
                "assignment_id": str(updated.get("assignment_id") or ""),
                "view_url": view_url,
                "refresh_url": refresh_url,
            }

        job_id = job_manager.submit_job("submission_rerun", _run_mark_rerun_job)
        assignment_id = str(queued_run_info.get("assignment_id") or "")
        label = f"Rerun: {queued_run_info.get('student_id') or run_dir.name}"
        return _build_rerun_job_response(
            job_id=job_id,
            run_id=str(queued_run_info.get("id") or run_dir.name),
            label=label,
            assignment_id=assignment_id,
            view_url=view_url,
            refresh_url=refresh_url,
        )

    def _queue_batch_submission_rerun(
        run_dir: Path,
        run_info: Mapping[str, object],
        submission_id: str,
        *,
        view_url: str,
        refresh_url: str,
    ):
        batch_summary = _load_batch_summary_records(run_dir)
        if batch_summary is None:
            raise FileNotFoundError("Batch summary could not be loaded.")

        records = list(batch_summary.get("records", []) or [])
        target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
        if target is None:
            raise FileNotFoundError("Batch submission record not found.")
        if str(target.get("status") or "").strip().lower() == "pending":
            raise RuntimeError("Submission is already queued for rerun.")

        target["status"] = "pending"
        target["rerun_pending"] = True
        target["overall"] = None
        target["components"] = {
            component: None for component in ("html", "css", "js", "php", "sql")
        }
        target.pop("error", None)
        target.pop("validation_error", None)

        queued_run_info = dict(run_info)
        queued_run_info["status"] = "pending"
        queued_run_info["last_rerun_requested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued_run_info.pop("error", None)
        _persist_batch_outputs(run_dir, queued_run_info, records)

        def _run_batch_rerun_job() -> dict:
            updated = _rerun_batch_submission(run_dir, queued_run_info, submission_id)
            return {
                "run_id": str(queued_run_info.get("id") or run_dir.name),
                "submission_id": submission_id,
                "assignment_id": str(updated.get("assignment_id") or queued_run_info.get("assignment_id") or ""),
                "student_id": str(updated.get("student_id") or submission_id),
                "view_url": view_url,
                "refresh_url": refresh_url,
            }

        job_id = job_manager.submit_job("submission_rerun", _run_batch_rerun_job)
        assignment_id = str(target.get("assignment_id") or queued_run_info.get("assignment_id") or "")
        label = f"Rerun: {target.get('student_id') or submission_id}"
        return _build_rerun_job_response(
            job_id=job_id,
            run_id=str(queued_run_info.get("id") or run_dir.name),
            label=label,
            assignment_id=assignment_id,
            view_url=view_url,
            refresh_url=refresh_url,
        )

    @app.route("/teacher/assignment/<assignment_id>/threats/delete", methods=["POST"])
    @teacher_or_admin_required
    def assignment_threat_delete(assignment_id: str):
        if not _user_can_access_assignment(assignment_id):
            flash("You do not have access to this assignment.", "error")
            return redirect(url_for("teacher.dashboard"))

        run_id = str(request.form.get("run_id") or "").strip()
        submission_id = str(request.form.get("submission_id") or "").strip()
        if not run_id:
            flash("Threat resolution failed: missing run ID.", "error")
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            flash("Threat resolution failed: submission not found.", "error")
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

        run_info = load_run_info(run_dir) or {}
        if run_info.get("mode") == "batch":
            if not submission_id:
                flash("Threat resolution failed: missing batch submission ID.", "error")
                return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

            batch_summary = _load_batch_summary_records(run_dir)
            if batch_summary is None:
                flash("Threat resolution failed: batch summary could not be loaded.", "error")
                return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

            records = list(batch_summary.get("records", []) or [])
            target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
            if target is None:
                flash("Threat resolution failed: flagged submission record not found.", "error")
                return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

            _safe_delete_within_run(run_dir, run_dir / "runs" / submission_id)
            _safe_delete_within_run(run_dir, target.get("path"))
            remaining = [record for record in records if str(record.get("id") or "") != submission_id]
            _persist_batch_outputs(run_dir, run_info, remaining)
            _flash_assignment_review_state(
                assignment_id,
                f"Flagged submission for '{target.get('student_id') or submission_id}' deleted.",
            )
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

        shutil.rmtree(run_dir, ignore_errors=True)
        _flash_assignment_review_state(
            assignment_id,
            f"Flagged submission for '{run_info.get('student_id') or run_id}' deleted.",
        )
        return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

    @app.route("/teacher/assignment/<assignment_id>/submissions/rerun", methods=["POST"])
    @app.route("/teacher/assignment/<assignment_id>/threats/reprocess", methods=["POST"])
    @teacher_or_admin_required
    def assignment_submission_rerun(assignment_id: str):
        if not _user_can_access_assignment(assignment_id):
            if _is_async_job_request():
                return jsonify({"error": "You do not have access to this assignment."}), 403
            flash("You do not have access to this assignment.", "error")
            return redirect(url_for("teacher.dashboard"))

        run_id = str(request.form.get("run_id") or "").strip()
        submission_id = str(request.form.get("submission_id") or "").strip()
        if not run_id:
            if _is_async_job_request():
                return jsonify({"error": "Rerun failed: missing run ID."}), 400
            flash("Rerun failed: missing run ID.", "error")
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            if _is_async_job_request():
                return jsonify({"error": "Rerun failed: submission not found."}), 404
            flash("Rerun failed: submission not found.", "error")
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

        run_info = load_run_info(run_dir) or {}
        try:
            if run_info.get("mode") == "batch":
                if not submission_id:
                    if _is_async_job_request():
                        return jsonify({"error": "Rerun failed: missing batch submission ID."}), 400
                    flash("Rerun failed: missing batch submission ID.", "error")
                    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))
                return _queue_batch_submission_rerun(
                    run_dir,
                    run_info,
                    submission_id,
                    view_url=url_for("batch_submission_view", run_id=run_id, submission_id=submission_id),
                    refresh_url=url_for("teacher.assignment_detail", assignment_id=assignment_id),
                )
            return _queue_mark_submission_rerun(
                run_dir,
                run_info,
                view_url=url_for("run_detail", run_id=run_id),
                refresh_url=url_for("teacher.assignment_detail", assignment_id=assignment_id),
            )
        except Exception as exc:
            if _is_async_job_request():
                return jsonify({"error": str(exc)}), 400
            flash(f"Rerun failed: {exc}", "error")
            return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

    @app.route("/runs/<run_id>")
    @login_required
    def run_detail(run_id: str):
        runs_root = get_runs_root(app)
        sync_attempts_from_storage(runs_root)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        run_info = load_run_info(run_dir)

        # ── RBAC: student access control ──────────────────────────────
        user = get_current_user()
        if user and user["role"] == "student":
            # Students can only view their own runs
            run_student_id = run_info.get("student_id", "")
            if run_student_id != user["userID"]:
                # Check batch submissions too
                batch_summary = run_info.get("batch_summary", [])
                if isinstance(batch_summary, Mapping):
                    batch_summary = batch_summary.get("records", [])
                found = False
                if isinstance(batch_summary, list):
                    for rec in batch_summary:
                        if rec.get("student_id") == user["userID"]:
                            found = True
                            break
                if not found:
                    flash("You do not have access to this submission.", "error")
                    return redirect(url_for("student.dashboard"))

        # Check marks_released status for grade visibility
        assignment_id = run_info.get("assignment_id", "")
        assignment = get_assignment(assignment_id) if assignment_id else None
        marks_released = assignment["marks_released"] if assignment else True  # default True for non-assignment runs

        attempt = get_attempt_by_run_reference(run_id, runs_root=runs_root)
        attempt_history = []
        attempt_summary = None
        if assignment_id and run_info.get("student_id"):
            attempt_history = filter_attempts_for_root(
                list_attempts(
                    assignment_id=str(assignment_id),
                    student_id=str(run_info.get("student_id") or ""),
                    newest_first=True,
                ),
                runs_root,
            )
            attempt_summary = get_student_assignment_summary(
                str(assignment_id),
                str(run_info.get("student_id") or ""),
            )
        if attempt:
            run_info = dict(run_info, **{
                "attempt_id": attempt.get("id"),
                "attempt_number": attempt.get("attempt_number"),
                "source_type": attempt.get("source_type"),
                "source_actor_user_id": attempt.get("source_actor_user_id"),
                "submitted_at": attempt.get("submitted_at"),
                "validity_status": attempt.get("validity_status"),
                "confidence": attempt.get("confidence"),
                "manual_review_required": bool(attempt.get("manual_review_required")),
                "is_active": bool(attempt.get("is_active")),
                "selection_reason": attempt.get("selection_reason"),
            })

        context = {
            "run": run_info,
            "run_id": run_id,
            "marks_released": marks_released,
            "attempt": attempt,
            "attempt_history": attempt_history,
            "attempt_summary": attempt_summary,
        }
        if run_info.get("mode") == "mark":
            report_path = run_dir / run_info.get("report", "report.json")
            run_status = str(run_info.get("status") or "").strip().lower()
            if run_status not in {"pending", "failed", "error"} and report_path.exists():
                context["report"] = _ensure_check_stats(
                    json.loads(report_path.read_text(encoding="utf-8"))
                )
                review_flags = extract_review_flags_from_report(context["report"])
                context["run"] = dict(
                    run_info,
                    threat_flagged=bool(review_flags.get("threat_flagged")),
                    threat_count=int(review_flags.get("threat_count") or 0),
                    llm_error_flagged=bool(review_flags.get("llm_error_flagged")),
                    llm_error_message=review_flags.get("llm_error_message"),
                    llm_error_messages=list(review_flags.get("llm_error_messages") or []),
                    status=(
                        "llm_error"
                        if review_flags.get("llm_error_flagged")
                        and run_status in {"", "ok", "completed", "complete", "success", "succeeded"}
                        else run_info.get("status")
                    ),
                )
                context["threat_file_contents"] = _load_threat_file_contents(
                    context["report"].get("findings", []), run_dir
                )
            context["detail_view"] = _build_submission_detail_view(context["run"], context.get("report"))
            return render_template("run_detail.html", **context)
        else:
            # Batch runs: redirect to assignment detail page (batch summary removed)
            assignment_id = run_info.get("assignment_id", "")
            if assignment_id:
                return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))
            # Fallback: redirect to runs list if no assignment ID
            return redirect(url_for("runs"))

    @app.route("/runs/<run_id>/rerun", methods=["POST"])
    @teacher_or_admin_required
    def run_submission_rerun(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            if _is_async_job_request():
                return jsonify({"error": "Submission not found."}), 404
            flash("Rerun failed: submission not found.", "error")
            return redirect(url_for("runs"))

        run_info = load_run_info(run_dir) or {}
        if run_info.get("mode") != "mark":
            assignment_id = str(run_info.get("assignment_id") or "").strip()
            if _is_async_job_request():
                return jsonify({"error": "Use the assignment submission rerun action for batch submissions."}), 400
            flash("Rerun failed: use the assignment submission rerun action for batch submissions.", "error")
            if assignment_id:
                return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))
            return redirect(url_for("runs"))

        try:
            return _queue_mark_submission_rerun(
                run_dir,
                run_info,
                view_url=url_for("run_detail", run_id=run_id),
                refresh_url=url_for("run_detail", run_id=run_id),
            )
        except Exception as exc:
            if _is_async_job_request():
                return jsonify({"error": str(exc)}), 400
            flash(f"Rerun failed: {exc}", "error")
            return redirect(url_for("run_detail", run_id=run_id))

    @app.route("/runs/<run_id>/override-threat", methods=["POST"])
    @teacher_or_admin_required
    def override_threat(run_id: str):
        """Backward-compatible alias for the standard single-submission rerun."""
        return run_submission_rerun(run_id)
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return jsonify({"error": "Run not found"}), 404

        run_info = load_run_info(run_dir)
        if not run_info:
            return jsonify({"error": "Run info not found"}), 404

        if run_info.get("mode") != "mark":
            return jsonify({"error": "Override is only supported for single-mark runs"}), 400

        original_filename = run_info.get("original_filename", "")
        upload_zip = run_dir / original_filename
        if not upload_zip.exists():
            zips = list(run_dir.glob("*.zip"))
            if not zips:
                return jsonify({"error": "Original submission ZIP not found — cannot reprocess"}), 404
            upload_zip = zips[0]

        profile = run_info.get("profile", "frontend")
        scoring_mode_str = run_info.get("scoring_mode", "static_plus_llm")
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            scoring_mode = ScoringMode("static_plus_llm")

        pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
        meta_dict = dict(run_info)

        def _run_override_job() -> dict:
            """Re-extract and re-assess the submission with threat scan disabled."""
            extracted = run_dir / "uploaded_extract"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            submission_root = find_submission_root(extracted)
            pipeline.run(
                submission_path=submission_root,
                workspace_path=run_dir,
                profile=profile,
                metadata=meta_dict,
                skip_threat_scan=True,
            )
            # Persist override timestamp in run_info so the dashboard reflects it
            updated = dict(run_info)
            updated["threat_override"] = True
            updated["threat_override_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            save_run_info(run_dir, updated)
            return {"run_id": run_id}

        job_id = job_manager.submit_job("threat_override", _run_override_job)
        return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202



    @app.route("/runs/<run_id>/artifacts/<path:relpath>")
    @login_required
    def run_artifact(run_id: str, relpath: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        allowed_roots = {"artifacts", "runs", "reports", "evaluation", "submission"}
        rel_parts = Path(relpath).parts
        if not rel_parts or rel_parts[0] not in allowed_roots:
            return "Not allowed", 403
        candidate = (run_dir / Path(relpath)).resolve()
        try:
            candidate.relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        if not candidate.exists() or not candidate.is_file():
            return "File not found", 404
        # Serve images inline for vision analysis screenshots; download others
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
        as_download = candidate.suffix.lower() not in image_exts
        return send_file(candidate, as_attachment=as_download, download_name=candidate.name)

    @app.route("/batch/<run_id>/submissions/<submission_id>/view")
    @login_required
    def batch_submission_view(run_id: str, submission_id: str):
        """View a batch submission's report in the browser (like a single submission)."""
        runs_root = get_runs_root(app)
        sync_attempts_from_storage(runs_root)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        
        submission_dir = run_dir / "runs" / submission_id
        report_path = submission_dir / "report.json"
        
        # Security check
        try:
            report_path.resolve().relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        
        run_info = load_run_info(run_dir) or {}
        batch_summary = _load_batch_summary_records(run_dir) or {}
        record = next(
            (
                item
                for item in list(batch_summary.get("records", []) or [])
                if str(item.get("id") or "") == submission_id
            ),
            None,
        )
        record_status = str((record or {}).get("status") or "ok").strip().lower()

        report = None
        if record_status not in {"pending", "failed", "error"}:
            if not report_path.exists():
                return "Report not found", 404
            report = _ensure_check_stats(
                json.loads(report_path.read_text(encoding="utf-8"))
            )
            review_flags = extract_review_flags_from_report(report)
            if record is not None:
                record["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                record["threat_count"] = int(review_flags.get("threat_count") or 0)
                record["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                record["llm_error_message"] = review_flags.get("llm_error_message")
                record["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
                if record["llm_error_flagged"] and record_status in {"", "ok", "completed", "complete", "success", "succeeded"}:
                    record_status = "llm_error"

        # Extract real student_id from batch_summary or report metadata
        # (submission_id is the full stem like "testStudent_test_assignment1",
        #  we need just the parsed student part e.g. "testStudent")
        real_student_id = submission_id  # fallback
        real_assignment_id = run_info.get("assignment_id", "")
        attempt = get_attempt_by_run_reference(run_id, submission_id, runs_root=runs_root)

        # Try report metadata first (most reliable — set during pipeline run)
        report_meta = (report or {}).get("metadata", {}).get("submission_metadata", {})
        if report_meta.get("student_id"):
            real_student_id = report_meta["student_id"]
        elif record and record.get("student_id"):
            real_student_id = str(record.get("student_id"))
        if report_meta.get("assignment_id"):
            real_assignment_id = report_meta["assignment_id"]
        elif record and record.get("assignment_id"):
            real_assignment_id = str(record.get("assignment_id"))

        # Fallback: try batch_summary.json records
        if real_student_id == submission_id:
            for rec in batch_summary.get("records", []) or []:
                if rec.get("id") == submission_id:
                    real_student_id = rec.get("student_id", submission_id)
                    real_assignment_id = rec.get("assignment_id", real_assignment_id)
                    break

        user = get_current_user()
        if user and user["role"] == "student" and real_student_id != user["userID"]:
            flash("You do not have access to this submission.", "error")
            return redirect(url_for("student.dashboard"))

        assignment = get_assignment(real_assignment_id) if real_assignment_id else None
        marks_released = assignment["marks_released"] if assignment else True
        attempt_history = []
        attempt_summary = None
        if real_assignment_id and real_student_id:
            attempt_history = filter_attempts_for_root(
                list_attempts(
                    assignment_id=str(real_assignment_id),
                    student_id=str(real_student_id),
                    newest_first=True,
                ),
                runs_root,
            )
            attempt_summary = get_student_assignment_summary(
                str(real_assignment_id),
                str(real_student_id),
            )
        back_url = (
            url_for("student.coursework")
            if user and user["role"] == "student"
            else (
                url_for("teacher.assignment_detail", assignment_id=run_info.get("assignment_id", ""))
                if run_info.get("assignment_id")
                else url_for("runs")
            )
        )

        submission_run_info = {
            "mode": "mark",
            "profile": run_info.get("profile", "frontend"),
            "assignment_id": real_assignment_id,
            "student_id": real_student_id,
            "created_at": run_info.get("created_at", ""),
            "status": record_status,
            "llm_error_flagged": bool((record or {}).get("llm_error_flagged")),
            "llm_error_message": (record or {}).get("llm_error_message"),
            "llm_error_messages": list((record or {}).get("llm_error_messages") or []),
        }
        if attempt:
            submission_run_info.update(
                {
                    "attempt_id": attempt.get("id"),
                    "attempt_number": attempt.get("attempt_number"),
                    "source_type": attempt.get("source_type"),
                    "source_actor_user_id": attempt.get("source_actor_user_id"),
                    "submitted_at": attempt.get("submitted_at"),
                    "validity_status": attempt.get("validity_status"),
                    "confidence": attempt.get("confidence"),
                    "manual_review_required": bool(attempt.get("manual_review_required")),
                    "is_active": bool(attempt.get("is_active")),
                    "selection_reason": attempt.get("selection_reason"),
                }
            )

        # submission_dir doubles as the "run_dir" for threat file loading
        # because batch sub-runs store their files under runs/<id>/submission/
        detail_run_info = submission_run_info
        detail_report = report
        return render_template(
            "run_detail.html",
            run=detail_run_info,
            run_id=run_id,
            report=detail_report,
            marks_released=marks_released,
            detail_view=_build_submission_detail_view(detail_run_info, detail_report),
            threat_file_contents=_load_threat_file_contents(
                (detail_report or {}).get("findings", []), submission_dir
            ),
            attempt=attempt,
            attempt_history=attempt_history,
            attempt_summary=attempt_summary,
            batch_submission_id=submission_id,  # Flag to show back button
            back_url=back_url,
        )

    @app.route("/batch/<run_id>/submissions/<submission_id>/report.json")
    @login_required
    def batch_submission_report(run_id: str, submission_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        report_path = (run_dir / "runs" / submission_id / "report.json").resolve()
        try:
            report_path.relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        if not report_path.exists():
            return "Report not found", 404
        run_info = load_run_info(run_dir) or {}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            return "Report not found", 404
        report_meta = report.get("metadata", {}).get("submission_metadata", {})
        real_student_id = str(report_meta.get("student_id") or "")
        user = get_current_user()
        if user and user["role"] == "student" and real_student_id and real_student_id != user["userID"]:
            flash("You do not have access to this submission.", "error")
            return redirect(url_for("student.dashboard"))
        profile = run_info.get("profile", "")
        dl_name = f"report_{submission_id}_{profile}_{run_id}.json"
        return send_file(report_path, as_attachment=True, download_name=dl_name)

    def _export_report_content(
        report: dict, run_id: str, fmt: str
    ) -> tuple:
        """Build an ExportReport from a raw report dict and render the requested format.

        Returns (content, mimetype, file_extension).
        Raises ValueError if the report is too incomplete to export meaningfully.
        Supported fmt values: "json", "txt", "csv", "pdf".
        """
        er = build_export_report(report, run_id=run_id)
        validate_export_report(er)
        if fmt == "json":
            return _export_json(er), "application/json", "json"
        if fmt == "txt":
            return export_txt(er), "text/plain", "txt"
        if fmt == "csv":
            return export_csv_zip(er), "application/zip", "zip"
        if fmt == "pdf":
            return _export_pdf(er), "application/pdf", "pdf"
        raise ValueError(f"Unknown export format: {fmt}")

    @app.route("/batch/<run_id>/submissions/<submission_id>/export/<format>")
    @login_required
    def batch_submission_export(run_id: str, submission_id: str, format: str):
        """Export batch submission in various formats (csv, txt, pdf, json)."""
        if format not in ("csv", "txt", "pdf", "json"):
            return "Invalid format", 400

        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        report_path = (run_dir / "runs" / submission_id / "report.json").resolve()
        try:
            report_path.relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        if not report_path.exists():
            return "Report not found", 404

        run_info = load_run_info(run_dir) or {}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            return "Report not found", 404

        report_meta = report.get("metadata", {}).get("submission_metadata", {})
        real_student_id = str(report_meta.get("student_id") or "")
        user = get_current_user()
        if user and user["role"] == "student" and real_student_id and real_student_id != user["userID"]:
            flash("You do not have access to this submission.", "error")
            return redirect(url_for("student.dashboard"))

        profile = run_info.get("profile", "")
        base_name = f"report_{submission_id}_{profile}_{run_id}"

        try:
            content, mimetype, ext = _export_report_content(report, run_id=run_id, fmt=format)
        except ValueError as exc:
            app.logger.warning("Export failed for batch submission %s: %s", submission_id, exc)
            return "Report data insufficient for export", 422
        return Response(
            content,
            mimetype=mimetype,
            headers={"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'},
        )

    @app.route("/run/<run_id>/export/<format>")
    @login_required
    def individual_submission_export(run_id: str, format: str):
        """Export individual submission in various formats (csv, txt, pdf, json)."""
        if format not in ("csv", "txt", "pdf", "json"):
            return "Invalid format", 400

        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        report_path = run_dir / "report.json"
        if not report_path.exists():
            return "Report not found", 404

        run_info = load_run_info(run_dir) or {}
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            return "Report not found", 404

        report_meta = report.get("metadata", {}).get("submission_metadata", {})
        real_student_id = str(report_meta.get("student_id") or "")
        user = get_current_user()
        if user and user["role"] == "student" and real_student_id and real_student_id != user["userID"]:
            flash("You do not have access to this submission.", "error")
            return redirect(url_for("student.dashboard"))

        profile = run_info.get("profile", "")
        base_name = f"report_{profile}_{run_id}"

        try:
            content, mimetype, ext = _export_report_content(report, run_id=run_id, fmt=format)
        except ValueError as exc:
            app.logger.warning("Export failed for run %s: %s", run_id, exc)
            return "Report data insufficient for export", 422
        return Response(
            content,
            mimetype=mimetype,
            headers={"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'},
        )

    @app.route("/batch/<run_id>/submissions/<submission_id>/rerun", methods=["POST"])
    @teacher_or_admin_required
    def batch_submission_rerun(run_id: str, submission_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            if _is_async_job_request():
                return jsonify({"error": "Submission not found."}), 404
            flash("Rerun failed: submission not found.", "error")
            return redirect(url_for("runs"))

        run_info = load_run_info(run_dir) or {}
        try:
            return _queue_batch_submission_rerun(
                run_dir,
                run_info,
                submission_id,
                view_url=url_for("batch_submission_view", run_id=run_id, submission_id=submission_id),
                refresh_url=url_for("batch_submission_view", run_id=run_id, submission_id=submission_id),
            )
        except Exception as exc:
            if _is_async_job_request():
                return jsonify({"error": str(exc)}), 400
            flash(f"Rerun failed: {exc}", "error")
            return redirect(url_for("batch_submission_view", run_id=run_id, submission_id=submission_id))

    @app.route("/run/<run_id>/bundle")
    @login_required
    def download_bundle(run_id: str):
        """Download grading-relevant artifacts for a run as a ZIP bundle.

        Included:
          - report.html, report.json, summary.txt (top-level reports)
          - submission/  (student code, full tree)
          - artifacts/   (screenshots only — .png, .jpg, .jpeg, .gif, .webp)
          - batch files  (batch_summary.* and per-submission reports)

        Excluded:
          - uploaded_extract/  (duplicate of submission)
          - *.zip              (original upload archive)
          - run_*.json, metadata.json  (system bookkeeping)
          - .trace / .log files inside artifacts
        """
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)

        if run_dir is None:
            return "Run not found", 404

        run_info = load_run_info(run_dir) or {}
        profile = run_info.get("profile", "")
        mode = run_info.get("mode", "mark")

        # Image extensions kept from artifacts/
        _ARTIFACT_IMAGE_EXTS: frozenset[str] = frozenset(
            {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        )

        zip_buffer = BytesIO()

        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # ── 1. Top-level report files ──
                top_level_files = [
                    "report.json",
                    "summary.txt",
                ]

                # Add batch-specific top-level files
                if mode == "batch":
                    top_level_files.extend([
                        "batch_summary.json",
                        "batch_summary.csv",
                    ])

                for filename in top_level_files:
                    file_path = run_dir / filename
                    if file_path.is_file():
                        try:
                            zf.write(file_path, arcname=filename)
                        except Exception:
                            pass

                # ── 2. submission/ (full tree — student code) ──
                submission_dir = run_dir / "submission"
                if submission_dir.is_dir():
                    for fpath in submission_dir.rglob("*"):
                        if fpath.is_file():
                            try:
                                zf.write(fpath, arcname=fpath.relative_to(run_dir))
                            except Exception:
                                pass

                # ── 3. artifacts/ (images only, skip .trace / .log) ──
                artifacts_dir = run_dir / "artifacts"
                if artifacts_dir.is_dir():
                    for fpath in artifacts_dir.rglob("*"):
                        if fpath.is_file() and fpath.suffix.lower() in _ARTIFACT_IMAGE_EXTS:
                            try:
                                zf.write(fpath, arcname=fpath.relative_to(run_dir))
                            except Exception:
                                pass



            # Prepare response
            zip_buffer.seek(0)
            dl_name = f"run_{profile}_{run_id}.zip"
            return send_file(
                zip_buffer,
                mimetype="application/zip",
                as_attachment=True,
                download_name=dl_name,
            )

        except Exception as e:
            app.logger.error(f"Error creating bundle for run {run_id}: {e}")
            return "Error creating bundle", 500

    @app.route("/download/<run_id>/<filename>")
    @login_required
    def download(run_id: str, filename: str):
        if not allowed_download(filename, allowed=ALLOWED_DOWNLOADS):
            return "Not allowed", 403
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404

        target = _resolve_download_path(run_dir, filename)
        try:
            target.resolve().relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        if not target.exists() or not target.is_file():
            return "File not found", 404
        run_info = load_run_info(run_dir) or {}
        profile = run_info.get("profile", "")
        dl_name = filename
        if filename.startswith("report"):
            dl_name = f"report_{profile}_{run_id}.json"
        elif filename.startswith("summary"):
            dl_name = f"summary_{profile}_{run_id}.txt"
        elif filename.startswith("batch_summary"):
            suffix = ".csv" if filename.endswith(".csv") else ".json"
            dl_name = f"batch_summary_{profile}_{run_id}{suffix}"
        elif filename.startswith("batch_reports"):
            dl_name = f"batch_reports_{profile}_{run_id}.zip"
        return send_file(target, as_attachment=True, download_name=dl_name)

    # ── Threats dashboard ────────────────────────────────────────────

    @app.route("/threats")
    @teacher_or_admin_required
    def threats():
        from ams.sandbox.forensics import list_retained_containers
        containers = list_retained_containers()
        return render_template("threats.html", containers=containers)

    @app.route("/threats/<container_name>/inspect")
    @teacher_or_admin_required
    def threat_inspect(container_name: str):
        from ams.sandbox.forensics import inspect_container
        info = inspect_container(container_name)
        if info is None:
            flash("Container not found or not inspectable.", "error")
            return redirect(url_for("threats"))
        return render_template(
            "threats.html",
            containers=[],
            inspected=info,
        )

    @app.route("/threats/<container_name>/cleanup", methods=["POST"])
    @teacher_or_admin_required
    def threat_cleanup(container_name: str):
        from ams.sandbox.forensics import cleanup_container
        ok = cleanup_container(container_name)
        if ok:
            flash(f"Container {container_name} removed.", "success")
        else:
            flash(f"Failed to remove container {container_name}.", "error")
        return redirect(url_for("threats"))

    # ── GitHub OAuth + API endpoints ────────────────────────────────

    @app.route("/api/github/login")
    def github_login():
        """Redirect the user to GitHub's OAuth authorization page."""
        import secrets as _secrets

        if not GITHUB_CLIENT_ID:
            flash("GitHub integration is not configured (missing Client ID).")
            return redirect(url_for("mark"))

        state = _secrets.token_urlsafe(32)
        session["github_oauth_state"] = state

        params = (
            f"client_id={GITHUB_CLIENT_ID}"
            f"&redirect_uri={GITHUB_OAUTH_CALLBACK}"
            f"&scope=repo"
            f"&state={state}"
        )
        return redirect(f"https://github.com/login/oauth/authorize?{params}")

    @app.route("/api/github/callback")
    def github_callback():
        """Handle the OAuth redirect from GitHub.

        Exchanges the temporary ``code`` for an ``access_token``, then stores
        the token and user info in the session.
        """
        code = request.args.get("code", "")
        state = request.args.get("state", "")

        # CSRF protection — validate state
        expected_state = session.pop("github_oauth_state", None)
        if not code or not state or state != expected_state:
            flash("GitHub authorization failed (invalid state). Please try again.")
            return redirect(url_for("mark"))

        # Exchange code for access_token
        try:
            token_resp = _requests.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": GITHUB_OAUTH_CALLBACK,
                },
                timeout=15,
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
        except _requests.RequestException as exc:
            logger.warning("GitHub OAuth token exchange failed: %s", exc)
            flash("Failed to connect to GitHub. Please try again.")
            return redirect(url_for("mark"))

        access_token = token_data.get("access_token")
        if not access_token:
            error_desc = token_data.get("error_description", "Unknown error")
            flash(f"GitHub authorization failed: {error_desc}")
            return redirect(url_for("mark"))

        # Fetch user info for display
        try:
            user_resp = _requests.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            user_resp.raise_for_status()
            user_info = user_resp.json()
        except _requests.RequestException:
            user_info = {}

        session["github_token"] = access_token
        session["github_user"] = user_info.get("login", "")
        session["github_avatar"] = user_info.get("avatar_url", "")

        flash(f"Connected to GitHub as {user_info.get('login', 'unknown')}.", "success")
        return redirect(url_for("mark"))

    @app.route("/api/github/disconnect", methods=["POST"])
    def github_disconnect():
        """Clear the GitHub OAuth token from the session."""
        session.pop("github_token", None)
        session.pop("github_user", None)
        session.pop("github_avatar", None)
        return jsonify({"status": "disconnected"})

    @app.route("/api/github/repos")
    def github_repos():
        """Return the authenticated user's GitHub repositories as JSON."""
        token = session.get("github_token")
        if not token:
            return jsonify({"error": "GitHub account not linked"}), 401

        try:
            resp = _requests.get(
                "https://api.github.com/user/repos",
                headers={"Authorization": f"Bearer {token}"},
                params={"sort": "updated", "per_page": 100},
                timeout=15,
            )
            resp.raise_for_status()
            repos = resp.json()
        except _requests.RequestException as exc:
            return jsonify({"error": f"Failed to fetch repositories: {exc}"}), 502

        return jsonify([
            {
                "full_name": r["full_name"],
                "name": r["name"],
                "private": r["private"],
                "updated_at": r.get("updated_at", ""),
                "description": r.get("description") or "",
                "default_branch": r.get("default_branch", "main"),
            }
            for r in repos
        ])

    @app.route("/api/github/repos/<owner>/<repo>/branches")
    def github_branches(owner: str, repo: str):
        """Return branches for a specific repository."""
        token = session.get("github_token")
        if not token:
            return jsonify({"error": "GitHub account not linked"}), 401

        full_name = f"{owner}/{repo}"

        # Fetch default branch name from the repo metadata
        default_branch = "main"
        try:
            repo_resp = _requests.get(
                f"https://api.github.com/repos/{full_name}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            repo_resp.raise_for_status()
            default_branch = repo_resp.json().get("default_branch", "main")
        except _requests.RequestException:
            pass  # fall back to 'main'

        try:
            resp = _requests.get(
                f"https://api.github.com/repos/{full_name}/branches",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 100},
                timeout=15,
            )
            resp.raise_for_status()
            branches = resp.json()
        except _requests.RequestException as exc:
            return jsonify({"error": f"Failed to fetch branches: {exc}"}), 502

        return jsonify([
            {
                "name": b["name"],
                "is_default": b["name"] == default_branch,
            }
            for b in branches
        ])


app = create_app()





def _resolve_download_path(run_dir: Path, filename: str) -> Path:
    """Resolve the path to a downloadable file within a run directory."""
    # Direct match
    candidate = run_dir / filename
    if candidate.exists():
        return candidate.resolve()

    # Check evaluation directory
    evaluation_dir = run_dir / "evaluation"
    candidate = evaluation_dir / filename
    if candidate.exists():
        return candidate.resolve()

    # For files like "batch_reports.zip", search for matching prefix pattern
    base_name = filename.rsplit(".", 1)[0]  # e.g., "batch_reports"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

    # Search run_dir for files starting with the base name
    for f in run_dir.glob(f"{base_name}*.{ext}"):
        if f.is_file():
            return f.resolve()

    # Fallback to original path
    return (run_dir / filename).resolve()


def _build_batch_readme(run_id: str, profile: str, batch_summary: Mapping[str, object]) -> str:
    lines = [
        "Automated Marking System - Batch Reports",
        "",
        f"Run ID: {run_id}",
        f"Profile: {profile}",
        "",
        "Contents:",
        f"- {run_id}/batch_summary.json",
        f"- {run_id}/batch_summary.csv",
        f"- {run_id}/evaluation/ (if present)",
        f"- {run_id}/submissions/<submission_id>/report.json",
    ]
    return "\n".join(lines) + "\n"





def _write_batch_reports_zip(run_dir: Path, profile: str, run_id: str) -> None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return
    batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = batch_summary.get("records", []) or []

    zip_path = run_dir / f"batch_reports_{profile}_{run_id}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(summary_path, f"{run_id}/batch_summary.json")
        for filename in ("batch_summary.csv",):
            file = run_dir / filename
            if file.is_file():
                zf.write(file, f"{run_id}/{filename}")
        readme = _build_batch_readme(run_id, profile, batch_summary)
        zf.writestr(f"{run_id}/README.txt", readme)
        evaluation_dir = run_dir / "evaluation"
        if evaluation_dir.exists():
            for file in sorted(evaluation_dir.rglob("*")):
                if file.is_file():
                    arc = f"{run_id}/evaluation/{file.relative_to(evaluation_dir).as_posix()}"
                    zf.write(file, arc)
        for rec in records:
            rpath = rec.get("report_path")
            if rpath and Path(rpath).exists():
                path = Path(rpath)
                submission_id = rec.get("id", "submission")
                arc = f"{run_id}/submissions/{submission_id}/report.json"
                zf.write(path, arc)


def _write_run_index_mark(run_dir: Path, run_info: dict, report_path: Path) -> None:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return
    
    meta = report.get("metadata", {}) or {}
    submission_meta = meta.get("submission_metadata") or {}
    ident = meta.get("student_identity", {}) or {}
    review_flags = extract_review_flags_from_report(report)
    
    sub_entry = {
        "submission_id": meta.get("submission_name"),
        "student_name": ident.get("name_normalized") or ident.get("name_raw"),
        "student_id": submission_meta.get("student_id") or ident.get("student_id") or run_info.get("student_id"),
        "assignment_id": submission_meta.get("assignment_id") or run_info.get("assignment_id"),
        "original_filename": submission_meta.get("original_filename") or meta.get("original_filename") or run_info.get("original_filename"),
        "upload_timestamp": submission_meta.get("timestamp") or run_info.get("created_at"),
        "attempt_id": submission_meta.get("attempt_id") or run_info.get("attempt_id"),
        "attempt_number": submission_meta.get("attempt_number") or run_info.get("attempt_number"),
        "source_type": submission_meta.get("source_type") or run_info.get("source_type"),
        "validity_status": submission_meta.get("validity_status") or run_info.get("validity_status"),
        "is_active": submission_meta.get("is_active") if submission_meta.get("is_active") is not None else run_info.get("is_active"),
        "threat_count": int(review_flags.get("threat_count") or 0),
        "threat_flagged": bool(review_flags.get("threat_flagged")),
        "llm_error_flagged": bool(review_flags.get("llm_error_flagged")),
        "llm_error_message": review_flags.get("llm_error_message"),
        "llm_error_messages": list(review_flags.get("llm_error_messages") or []),
    }
    
    index = {
        "run_id": run_info.get("id"),
        "mode": run_info.get("mode"),
        "profile": run_info.get("profile"),
        "created_at": run_info.get("created_at"),
        "overall": report.get("scores", {}).get("overall"),
        "status": "llm_error" if review_flags.get("llm_error_flagged") else "ok",
        "attempt_id": run_info.get("attempt_id"),
        "attempt_number": run_info.get("attempt_number"),
        "source_type": run_info.get("source_type"),
        "validity_status": run_info.get("validity_status"),
        "is_active": run_info.get("is_active"),
        "submissions": [sub_entry],
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


def _write_run_index_batch(run_dir: Path, run_info: dict) -> None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return
    try:
        batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return
    submissions = []
    records = batch_summary.get("records", []) or []
    for rec in records:
        entry = {
            "submission_id": rec.get("id"),
            "student_name": None,
            "student_id": rec.get("student_id"),
            "assignment_id": rec.get("assignment_id"),
            "original_filename": rec.get("original_filename"),
            "upload_timestamp": rec.get("upload_timestamp"),
            "attempt_id": rec.get("attempt_id"),
            "attempt_number": rec.get("attempt_number"),
            "source_type": rec.get("source_type"),
            "validity_status": rec.get("validity_status"),
            "is_active": rec.get("is_active"),
            "overall": rec.get("overall"),
            "components": rec.get("components") or {},
            "threat_count": rec.get("threat_count"),
            "threat_flagged": bool(rec.get("threat_flagged") or rec.get("threat_count")),
            "llm_error_flagged": bool(rec.get("llm_error_flagged")),
            "llm_error_message": rec.get("llm_error_message"),
            "llm_error_messages": list(rec.get("llm_error_messages") or []),
            "status": rec.get("status"),
            "invalid": bool(rec.get("invalid")),
            "error": rec.get("error") or rec.get("validation_error"),
        }
        rpath = rec.get("report_path")
        if rpath and Path(rpath).exists():
            try:
                rep = json.loads(Path(rpath).read_text(encoding="utf-8"))
                meta = rep.get("metadata", {}) or {}
                submission_meta = meta.get("submission_metadata") or {}
                ident = meta.get("student_identity", {}) or {}
                scores = rep.get("scores", {}) or {}
                findings = rep.get("findings", []) or []
                entry["student_name"] = ident.get("name_normalized") or ident.get("name_raw")
                entry["student_id"] = submission_meta.get("student_id") or ident.get("student_id") or entry["student_id"]
                entry["assignment_id"] = submission_meta.get("assignment_id") or entry["assignment_id"]
                entry["original_filename"] = submission_meta.get("original_filename") or meta.get("original_filename") or entry["original_filename"]
                entry["upload_timestamp"] = submission_meta.get("timestamp") or entry["upload_timestamp"]
                if scores.get("overall") is not None:
                    entry["overall"] = scores.get("overall")
                by_component = scores.get("by_component") or {}
                if isinstance(by_component, dict):
                    entry["components"] = {
                        component: (component_scores or {}).get("score")
                        for component, component_scores in by_component.items()
                    }
                review_flags = extract_review_flags_from_report(rep)
                entry["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                if review_flags.get("threat_count"):
                    entry["threat_count"] = int(review_flags.get("threat_count") or 0)
                entry["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                entry["llm_error_message"] = review_flags.get("llm_error_message")
                entry["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
                if entry["llm_error_flagged"] and str(entry.get("status") or "").strip().lower() in {"", "ok", "completed", "complete", "success", "succeeded"}:
                    entry["status"] = "llm_error"
            except Exception:
                pass
        submissions.append(entry)
    index = {
        "run_id": run_info.get("id"),
        "mode": run_info.get("mode"),
        "profile": run_info.get("profile"),
        "created_at": run_info.get("created_at"),
        "overall": None,
        "status": "ok",
        "submissions": submissions,
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
