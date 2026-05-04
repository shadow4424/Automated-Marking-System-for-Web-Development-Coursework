from __future__ import annotations

import re as _re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from ams.analytics import FINDING_LABELS
from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats

_PATH_RE = _re.compile(
    r"(?:[A-Za-z]:)?[\\/](?:[\w .~@#$%&()\-]+[\\/]){2,}[\w .~@#$%&()\-]+\.\w{1,10}$"
)
DETAIL_COMPONENT_ORDER = ["html", "css", "js", "php", "sql", "api", "browser", "behavioural", "consistency", "other"]
DETAIL_COMPONENT_LABELS = {
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
DETAIL_STAGE_LABELS = {
    "static": "Static",
    "runtime": "Runtime",
    "browser": "Browser",
    "layout": "Layout",
    "quality": "Quality",
    "manual": "Manual",
}
DETAIL_STATUS_PRIORITY = {
    "FAIL": 0,
    "THREAT": 0,
    "PARTIAL": 1,
    "WARN": 1,
    "SKIPPED": 2,
    "PASS": 3,
    "NOT_EVALUATED": 4,
    "UNKNOWN": 5,
}
DETAIL_TONE_BY_STATUS = {
    "FAIL": "danger",
    "THREAT": "danger",
    "PARTIAL": "warning",
    "WARN": "warning",
    "SKIPPED": "muted",
    "PASS": "success",
    "NOT_EVALUATED": "muted",
    "UNKNOWN": "muted",
}
CONFIDENCE_FLAG_TEXT = {
    "runtime_failure": "Runtime checks failed or timed out in this run.",
    "browser_failure": "Browser checks failed or timed out in this run.",
    "browser_console_errors": "Browser console errors reduced trust in the automated result.",
    "runtime_skipped": "Runtime checks were skipped.",
    "browser_skipped": "Browser checks were skipped.",
    "layout_skipped": "Layout checks were skipped.",
}
ARTIFACT_ROOTS = ("artifacts/", "runs/", "reports/", "evaluation/", "submission/")


def clean_path(value: object) -> str:
    """Jinja filter: shorten absolute file paths to submission/file.ext."""
    s = str(value).replace("\\", "/")
    for marker in ("submission/", "artifacts/", "test_coursework/"):
        idx = s.find(marker)
        if idx != -1:
            return s[idx:]
    if _PATH_RE.match(s):
        parts = s.rsplit("/", 2)
        return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    return s


def render_evidence_value(val: object) -> str:
    """Return an HTML-safe string for a single evidence value."""
    from markupsafe import Markup, escape

    if isinstance(val, bool):
        return Markup('<span class="text-success">?</span>') if val else Markup('<span class="text-danger">?</span>')
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "?"
        items = ", ".join(escape(clean_path(v)) for v in val)
        return Markup(items)
    s = str(val)
    if _PATH_RE.match(s.replace("\\", "/")):
        return clean_path(s)
    return s


def ensure_check_stats(report: dict) -> dict:
    """Enrich a loaded report dict with aggregated check stats if missing."""
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


def to_rel(raw: str) -> str:
    """Convert a file reference to a path relative to submission/."""
    s = str(raw).replace("\\", "/")
    if "submission/" in s:
        idx = s.rfind("submission/")
        return s[idx + len("submission/"):]
    return Path(raw).name


def load_threat_file_contents(findings: list, run_dir: Path) -> dict:
    """Load source file contents for threat-flagged findings."""
    max_file_bytes = 200 * 1024
    submission_dir = (run_dir / "submission").resolve()

    threat_findings = [
        f for f in findings
        if f.get("severity") == "THREAT"
        and isinstance(f.get("evidence"), dict)
        and f["evidence"].get("file")
    ]
    if not threat_findings:
        return {}

    file_data: dict[str, dict] = {}
    for finding in threat_findings:
        file_rel = to_rel(finding["evidence"]["file"])
        if not file_rel or file_rel in file_data:
            continue
        candidate = (submission_dir / file_rel).resolve()
        try:
            candidate.relative_to(submission_dir)
        except ValueError:
            continue
        if not candidate.is_file() or candidate.stat().st_size > max_file_bytes:
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
            file_data[file_rel] = {"lines": content.splitlines(), "threat_lines": []}
        except Exception:
            pass

    for finding in threat_findings:
        file_rel = to_rel(finding["evidence"]["file"])
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


def coerce_float(value: object) -> float | None:
    """Coerce the value to a float."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def first_non_empty(values: Sequence[object]) -> str:
    """Return first non-empty value as text."""
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def format_submission_datetime(value: object) -> str:
    """Format the submission datetime."""
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return text[:16].replace("T", " ")


def normalize_status(value: object, *, fallback: str = "UNKNOWN") -> str:
    """Normalise the status."""
    text = str(value or "").strip().upper()
    return text or fallback


def status_tone(status: object) -> str:
    """Return badge tone for a status value."""
    return DETAIL_TONE_BY_STATUS.get(normalize_status(status), "muted")


def stage_label(stage: object) -> str:
    """Return display label for a stage value."""
    key = str(stage or "").strip().lower()
    if not key:
        return "General"
    return DETAIL_STAGE_LABELS.get(key, key.replace("_", " ").title())


def component_label(component: object) -> str:
    """Return display label for a component value."""
    key = str(component or "").strip().lower()
    if not key:
        return "General"
    return DETAIL_COMPONENT_LABELS.get(key, key.replace("_", " ").title())


def component_filter_value(component: object, *, stage: object = None) -> str:
    """Return filter value for a component and stage."""
    comp = str(component or "").strip().lower()
    stage_key = str(stage or "").strip().lower()
    if comp in {"html", "css", "js", "php", "sql", "api", "browser", "behavioral", "behavioural"}:
        return "behavioural" if comp == "behavioral" else comp
    if stage_key == "browser":
        return "browser"
    if stage_key == "runtime":
        return "behavioural"
    return comp or "other"


def humanize_identifier(identifier: object) -> str:
    """Humanise the identifier."""
    text = str(identifier or "").strip()
    if not text:
        return "Unnamed item"
    label, _description = FINDING_LABELS.get(text, ("", ""))
    if label:
        return label
    pretty = text.replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return pretty.title() if pretty else text


def describe_identifier(identifier: object) -> str:
    """Describe the identifier."""
    text = str(identifier or "").strip()
    if not text:
        return ""
    _label, description = FINDING_LABELS.get(text, ("", ""))
    return description


def to_relative_artifact_path(path: str) -> str:
    """Strip any absolute prefix from an artefact path."""
    normalised = path.replace("\\", "/")
    for root in ARTIFACT_ROOTS:
        idx = normalised.find(root)
        if idx >= 0:
            return normalised[idx:]
    return normalised


def add(screenshots: list[str], seen: set[str], raw: str) -> None:
    """Append one normalised screenshot path if it has not been seen yet."""
    normalised = to_relative_artifact_path(raw.strip())
    if normalised not in seen:
        seen.add(normalised)
        screenshots.append(normalised)


def gather_screenshots(evidence: object) -> list[str]:
    """Gather screenshot paths from an evidence mapping."""
    if not isinstance(evidence, Mapping):
        return []
    screenshots: list[str] = []
    seen: set[str] = set()

    direct = evidence.get("screenshot")
    if isinstance(direct, str) and direct.strip():
        add(screenshots, seen, direct)
    for path in evidence.get("screenshot_paths") or []:
        if isinstance(path, str) and path.strip():
            add(screenshots, seen, path)
    ux_review = evidence.get("ux_review")
    if isinstance(ux_review, Mapping):
        shot = ux_review.get("screenshot")
        if isinstance(shot, str) and shot.strip():
            add(screenshots, seen, shot)
    vision = evidence.get("vision_analysis")
    if isinstance(vision, Mapping):
        meta = vision.get("meta")
        if isinstance(meta, Mapping):
            shot = meta.get("screenshot")
            if isinstance(shot, str) and shot.strip():
                add(screenshots, seen, shot)
    return screenshots


def finding_stage(finding: Mapping[str, object]) -> str:
    """Return inferred stage for a finding."""
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


def finding_group_key(finding: Mapping[str, object]) -> str:
    """Return grouping key for a finding."""
    evidence = dict(finding.get("evidence", {}) or {})
    rule_id = str(evidence.get("rule_id") or "").strip()
    if rule_id:
        return rule_id
    return str(finding.get("id") or "").strip()


def normalize_raw_finding(finding: Mapping[str, object]) -> dict[str, Any]:
    """Normalise the raw finding."""
    identifier = str(finding.get("id") or "").strip() or "unknown"
    evidence = dict(finding.get("evidence", {}) or {}) if isinstance(finding.get("evidence"), Mapping) else finding.get("evidence")
    severity = normalize_status(finding.get("severity"), fallback="INFO")
    stage = finding_stage(finding)
    component = str(finding.get("category") or "").strip().lower()
    title = humanize_identifier(identifier)
    message = first_non_empty([
        finding.get("message"),
        describe_identifier(identifier),
        title,
    ])
    screenshots = gather_screenshots(evidence)
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
        "tone": status_tone(severity),
        "component": component,
        "component_label": component_label(component),
        "component_filter": component_filter_value(component, stage=stage),
        "stage": stage,
        "stage_label": stage_label(stage),
        "source": str(finding.get("source") or "").strip(),
        "finding_category": str(finding.get("finding_category") or "").strip(),
        "evidence": evidence,
        "screenshots": screenshots,
        "search_text": search_terms,
    }


def build_decision_summary(
    run: Mapping[str, object],
    report: Mapping[str, object] | None,
    confidence: Mapping[str, object],
    review: Mapping[str, object],
    limitations: list[dict[str, Any]],
    student_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the decision summary."""
    run_status = str(run.get("status") or "").strip().lower()
    overall = coerce_float(((report or {}).get("scores", {}) or {}).get("overall"))
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


__all__ = [
    "ARTIFACT_ROOTS",
    "CONFIDENCE_FLAG_TEXT",
    "DETAIL_COMPONENT_LABELS",
    "DETAIL_COMPONENT_ORDER",
    "DETAIL_STAGE_LABELS",
    "DETAIL_STATUS_PRIORITY",
    "DETAIL_TONE_BY_STATUS",
    "add",
    "build_decision_summary",
    "clean_path",
    "coerce_float",
    "component_filter_value",
    "component_label",
    "describe_identifier",
    "ensure_check_stats",
    "finding_group_key",
    "finding_stage",
    "first_non_empty",
    "format_submission_datetime",
    "gather_screenshots",
    "humanize_identifier",
    "load_threat_file_contents",
    "normalize_raw_finding",
    "normalize_status",
    "render_evidence_value",
    "stage_label",
    "status_tone",
    "to_rel",
    "to_relative_artifact_path",
]
