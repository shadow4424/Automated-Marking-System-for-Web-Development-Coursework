"""Teacher blueprint for assignment and analytics management."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from ams.analytics import generate_assignment_analytics
from ams.core.db import (
    assignment_allows_teacher,
    assignment_teacher_ids,
    create_assignment,
    delete_assignment,
    get_assignment,
    get_user,
    list_assignments,
    list_users,
    release_marks,
    update_assignment_students,
    update_assignment_teachers,
    withhold_marks,
)
from ams.io.web_storage import get_runs_root, list_runs, purge_assignment_storage
from ams.core.factory import get_llm_provider
from ams.llm.utils import clean_json_response
from ams.pdf_exports import build_records_pdf
from ams.web.auth import get_current_user, teacher_or_admin_required

logger = logging.getLogger(__name__)

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")

TEACHING_INSIGHT_PRIORITIES = {"high", "medium", "low"}
TEACHING_INSIGHT_TYPES = {"pattern", "strength", "weakness", "anomaly", "cause", "recommendation", "trend"}
RELIABILITY_EVIDENCE_KEYS = {
    "manual_review",
    "fully_evaluated",
    "partially_evaluated",
    "not_analysable",
    "confidence_mix",
    "limitation_incidents",
    "major_limitations",
    "runtime_skip_count",
    "browser_skip_count",
    "runtime_failure_count",
    "browser_failure_count",
}
PERCENTLIKE_PATH_HINTS = ("percent", "score", "average", "median", "mean", "min", "max", "gap")
COUNTLIKE_PATH_HINTS = (
    "count",
    "students",
    "submissions",
    "incident",
    "rule_count",
    "rules_affected",
    "active_in_scope",
    "assigned_students",
    "missing_assigned",
    "manual_review",
    "fully_evaluated",
    "partially_evaluated",
    "not_analysable",
    "runtime_skip_count",
    "browser_skip_count",
    "runtime_failure_count",
    "browser_failure_count",
    "categories",
    "evaluable",
)
NUMERIC_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?%?(?!\w)")
STRUCTURAL_RANGE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?%?(?!\w)")
STRUCTURAL_RANGE_HINTS = (
    "band",
    "interval",
    "bucket",
    "label",
    "range",
    "mark band",
    "score band",
    "grade band",
)
SEMANTIC_CAUSE_MARKERS = ("because", "due to", "caused by", "as a result", "likely because", "driven by")
SEMANTIC_CLAIM_EVIDENCE_KEYS = {
    "strongest_requirement",
    "weakest_requirement",
    "requirement_coverage_summary",
    "component_performance_summary",
    "top_failing_rule",
    "top_failing_rules",
    "major_rule_categories",
    "major_limitations",
    "static_vs_behavioural_mismatch",
    "high_priority_flagged_submissions",
    "confidence_mix",
    "manual_review",
}


def _user_can_access_assignment(assignment: Mapping[str, Any] | None) -> bool:
    user = get_current_user()
    if user is None:
        return False
    return assignment_allows_teacher(dict(assignment) if assignment is not None else None, user["userID"], user["role"])


def _teacher_user_lookup(user_id: str) -> dict[str, Any]:
    user = get_user(user_id)
    if user:
        return user
    return {"userID": user_id, "firstName": user_id, "lastName": "", "email": ""}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _format_freshness_label(value: str | None) -> tuple[str, str]:
    dt = _parse_iso_datetime(value)
    if dt is None:
        return "Updated recently", ""

    exact = dt.strftime("%d %b %Y, %H:%M UTC")
    delta_seconds = max((datetime.now(timezone.utc) - dt).total_seconds(), 0)
    if delta_seconds < 90:
        return "Updated just now", exact
    if delta_seconds < 3600:
        minutes = max(int(delta_seconds // 60), 1)
        return f"Updated {minutes} min ago", exact
    if delta_seconds < 86400 and datetime.now(timezone.utc).date() == dt.date():
        return f"Updated today at {dt.strftime('%H:%M UTC')}", exact
    return f"Updated {exact}", exact


def _submission_matches_assignment(submission: Mapping[str, Any], assignment_id: str) -> bool:
    return str(submission.get("assignment_id") or "").strip() == assignment_id


def _batch_submission_display_status(submission: Mapping[str, Any]) -> str:
    if submission.get("threat_flagged") or submission.get("threat_count"):
        return "threat"
    if submission.get("llm_error_flagged"):
        return "llm_error"
    status = str(submission.get("status") or "").strip().lower()
    if status in {"pending", "queued", "running"}:
        return "pending"
    if status in {"failed", "error"} or submission.get("invalid") is True:
        return "failed"
    return "completed"


def _build_assignment_run_rows(assignment_id: str) -> list[dict]:
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root, only_active=False)
    assignment_runs: list[dict] = []

    for run in all_runs:
        matching_submissions = [
            submission
            for submission in list(run.get("submissions", []) or [])
            if _submission_matches_assignment(submission, assignment_id)
        ]

        if run.get("mode") == "batch":
            for submission in matching_submissions:
                submission_row = dict(run)
                submission_row["student_id"] = submission.get("student_id") or submission.get("student_name") or "Unknown"
                submission_row["assignment_id"] = submission.get("assignment_id") or run.get("assignment_id")
                submission_row["created_at"] = submission.get("upload_timestamp") or run.get("created_at")
                submission_row["_batch_submission_id"] = (
                    submission.get("submission_id")
                    or submission.get("student_id")
                    or submission.get("student_name")
                )
                submission_row["_submission_record"] = submission
                overall = submission.get("overall")
                submission_row["score"] = float(overall) * 100 if isinstance(overall, (int, float)) else None
                submission_row["attempt_id"] = submission.get("attempt_id")
                submission_row["attempt_number"] = submission.get("attempt_number")
                submission_row["is_active"] = bool(submission.get("is_active"))
                submission_row["validity_status"] = submission.get("validity_status")
                submission_row["source_type"] = submission.get("source_type")
                submission_row["confidence"] = submission.get("confidence")
                submission_row["manual_review_required"] = bool(submission.get("manual_review_required"))
                if submission.get("submitted_at"):
                    submission_row["created_at"] = submission.get("submitted_at")
                submission_row["status"] = _batch_submission_display_status(submission)
                submission_row["threat_flagged"] = bool(submission.get("threat_flagged") or submission.get("threat_count"))
                submission_row["llm_error_flagged"] = bool(submission.get("llm_error_flagged"))
                submission_row["llm_error_message"] = submission.get("llm_error_message")
                submission_row["llm_error_messages"] = list(submission.get("llm_error_messages") or [])
                assignment_runs.append(submission_row)
            continue

        if str(run.get("assignment_id") or "").strip() == assignment_id or matching_submissions:
            run_row = dict(run)
            if matching_submissions and "_submission_record" not in run_row:
                run_row["_submission_record"] = matching_submissions[0]
            if matching_submissions:
                primary_submission = matching_submissions[0]
                run_row["attempt_id"] = primary_submission.get("attempt_id") or run_row.get("attempt_id")
                run_row["attempt_number"] = primary_submission.get("attempt_number") or run_row.get("attempt_number")
                run_row["is_active"] = bool(
                    primary_submission.get("is_active")
                    if primary_submission.get("is_active") is not None
                    else run_row.get("is_active")
                )
                run_row["validity_status"] = primary_submission.get("validity_status") or run_row.get("validity_status")
                run_row["source_type"] = primary_submission.get("source_type") or run_row.get("source_type")
                run_row["confidence"] = primary_submission.get("confidence") or run_row.get("confidence")
                run_row["manual_review_required"] = bool(
                    primary_submission.get("manual_review_required")
                    if primary_submission.get("manual_review_required") is not None
                    else run_row.get("manual_review_required")
                )
            if matching_submissions and not run_row.get("student_id"):
                run_row["student_id"] = matching_submissions[0].get("student_id") or matching_submissions[0].get("student_name")
            run_row["threat_flagged"] = bool(
                run_row.get("threat_flagged")
                or any(
                    bool(submission.get("threat_flagged") or submission.get("threat_count"))
                    for submission in matching_submissions
                )
            )
            run_row["llm_error_flagged"] = bool(
                run_row.get("llm_error_flagged")
                or any(bool(submission.get("llm_error_flagged")) for submission in matching_submissions)
            )
            if not run_row.get("llm_error_messages"):
                messages: list[str] = []
                for submission in matching_submissions:
                    for item in list(submission.get("llm_error_messages") or []):
                        if str(item).strip() and str(item) not in messages:
                            messages.append(str(item))
                if messages:
                    run_row["llm_error_messages"] = messages
                    run_row["llm_error_message"] = messages[0]
            if (
                run_row.get("llm_error_flagged")
                and str(run_row.get("status") or "").strip().lower() in {"", "ok", "completed", "complete", "success", "succeeded"}
            ):
                run_row["status"] = "llm_error"
            assignment_runs.append(run_row)

    assignment_runs.sort(
        key=lambda row: (
            str(row.get("created_at") or ""),
            str(row.get("student_id") or ""),
            str(row.get("id") or ""),
        ),
        reverse=True,
    )
    return assignment_runs


def _assignment_submission_detail_url(row: Mapping[str, Any]) -> str:
    if row.get("mode") == "batch" and row.get("_batch_submission_id"):
        return url_for("batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
    return url_for("run_detail", run_id=row.get("id"))


def _assignment_submission_row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("attempt_id") or row.get("id") or ""),
        str(row.get("_batch_submission_id") or ""),
    )


def _build_assignment_submission_groups(assignment_runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in assignment_runs:
        student_id = str(row.get("student_id") or "Unknown")
        grouped.setdefault(student_id, []).append(dict(row))

    groups: list[dict[str, Any]] = []
    for student_id, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                str(row.get("created_at") or ""),
                int(row.get("attempt_number") or 0),
                str(row.get("id") or ""),
                str(row.get("_batch_submission_id") or ""),
            ),
            reverse=True,
        )
        latest_row = rows[0]
        active_row = next((row for row in rows if row.get("is_active")), None)
        primary_row = active_row or latest_row

        latest_key = (
            str(latest_row.get("attempt_id") or latest_row.get("id") or ""),
            str(latest_row.get("_batch_submission_id") or ""),
        )
        active_key = (
            str(primary_row.get("attempt_id") or primary_row.get("id") or ""),
            str(primary_row.get("_batch_submission_id") or ""),
        )
        has_fallback = active_row is not None and active_key != latest_key

        selection_reason = str(primary_row.get("selection_reason") or "").strip()
        if has_fallback and not selection_reason:
            selection_reason = "The newest attempt is not usable, so the previous valid attempt remains active."
        elif primary_row.get("is_active") and not selection_reason:
            selection_reason = "Active because it is the most recent valid submission."

        primary_key = _assignment_submission_row_key(primary_row)
        history_attempts = [
            row
            for row in rows
            if _assignment_submission_row_key(row) != primary_key
        ]

        groups.append(
            {
                "student_id": student_id,
                "primary": primary_row,
                "latest": latest_row,
                "active": active_row,
                "attempts": rows,
                "attempt_count": len(rows),
                "has_history": len(rows) > 1,
                "has_fallback": has_fallback,
                "history_attempts": history_attempts,
                "history_count": len(history_attempts),
                "selection_reason": selection_reason,
                "detail_url": _assignment_submission_detail_url(primary_row),
            }
        )

    groups.sort(
        key=lambda group: (
            str(group["primary"].get("created_at") or ""),
            str(group["student_id"] or ""),
        ),
        reverse=True,
    )
    return groups


def _build_threat_resolution_rows(assignment_runs: Sequence[Mapping[str, Any]]) -> list[dict]:
    threat_rows: list[dict] = []
    for row in assignment_runs:
        submission = row.get("_submission_record") if isinstance(row.get("_submission_record"), Mapping) else {}
        threat_flagged = bool(
            row.get("threat_flagged")
            or (isinstance(submission, Mapping) and (submission.get("threat_flagged") or submission.get("threat_count")))
            or str(row.get("status") or "").strip().lower() == "threat"
        )
        if not threat_flagged:
            continue
        threat_rows.append(
            {
                "run_id": str(row.get("id") or ""),
                "student_id": str(row.get("student_id") or "Unknown"),
                "mode": str(row.get("mode") or "mark"),
                "submission_id": str(row.get("_batch_submission_id") or ""),
                "detail_url": (
                    url_for("batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
                    if row.get("mode") == "batch" and row.get("_batch_submission_id")
                    else url_for("run_detail", run_id=row.get("id"))
                ),
                "threat_count": int(
                    submission.get("threat_count") or 0
                ) if isinstance(submission, Mapping) else 0,
            }
        )
    return threat_rows


def _build_llm_error_resolution_rows(assignment_runs: Sequence[Mapping[str, Any]]) -> list[dict]:
    llm_rows: list[dict] = []
    for row in assignment_runs:
        submission = row.get("_submission_record") if isinstance(row.get("_submission_record"), Mapping) else {}
        llm_error_flagged = bool(
            row.get("llm_error_flagged")
            or (isinstance(submission, Mapping) and submission.get("llm_error_flagged"))
            or str(row.get("status") or "").strip().lower() == "llm_error"
        )
        if not llm_error_flagged:
            continue
        messages = [
            str(item).strip()
            for item in list(row.get("llm_error_messages") or submission.get("llm_error_messages") or [])
            if str(item).strip()
        ]
        first_message = str(
            row.get("llm_error_message")
            or (submission.get("llm_error_message") if isinstance(submission, Mapping) else "")
            or (messages[0] if messages else "")
        ).strip()
        if first_message and first_message not in messages:
            messages.insert(0, first_message)
        llm_rows.append(
            {
                "run_id": str(row.get("id") or ""),
                "student_id": str(row.get("student_id") or "Unknown"),
                "mode": str(row.get("mode") or "mark"),
                "submission_id": str(row.get("_batch_submission_id") or ""),
                "detail_url": (
                    url_for("batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
                    if row.get("mode") == "batch" and row.get("_batch_submission_id")
                    else url_for("run_detail", run_id=row.get("id"))
                ),
                "llm_error_message": first_message or "LLM-assisted marking failed and requires review.",
                "llm_error_messages": messages,
            }
        )
    return llm_rows


def _filtered_needs_attention_rows(analytics: dict, args) -> list[dict]:
    rows = list(analytics.get("needs_attention", []) or [])
    severity = str(args.get("severity", "")).strip().lower()
    score_band = str(args.get("score_band", "")).strip().lower()
    grade = str(args.get("grade", "")).strip().lower()
    confidence = str(args.get("confidence", "")).strip().lower()
    reason = str(args.get("reason", "")).strip().lower()
    flag = str(args.get("flag", "")).strip().lower()
    student = str(args.get("student", "")).strip().lower()
    rule = str(args.get("rule", "")).strip()
    signal_students = {item.strip().lower() for item in str(args.get("signal_students", "")).split(",") if item.strip()}
    signal_rules = {item.strip() for item in str(args.get("signal_rules", "")).split(",") if item.strip()}
    sort_key = str(args.get("sort", "severity")).strip().lower()

    def _matches(row: dict) -> bool:
        row_flags = [str(item).lower() for item in row.get("flags", []) or []]
        row_rules = [str(item) for item in row.get("matched_rule_ids", []) or []]
        row_student = str(row.get("student_id", "")).lower()
        row_score = row.get("overall")
        if isinstance(row_score, (int, float)):
            score_match = (
                not score_band
                or (score_band == "below_50" and row_score < 0.5)
                or (score_band == "between_50_69" and 0.5 <= row_score < 0.7)
                or (score_band == "70_plus" and row_score >= 0.7)
            )
        else:
            score_match = not score_band or score_band == "no_score"
        signal_match = True
        if signal_students or signal_rules:
            signal_match = row_student in signal_students or any(item in signal_rules for item in row_rules)
        return (
            (not severity or str(row.get("severity", "")).lower() == severity)
            and score_match
            and (not grade or str(row.get("grade", "")).lower() == grade)
            and (not confidence or str(row.get("confidence", "")).lower() == confidence)
            and (not reason or str(row.get("reason", "")).lower() == reason)
            and (not flag or flag in row_flags)
            and (not student or student in row_student)
            and (not rule or rule in row_rules)
            and signal_match
        )

    filtered = [row for row in rows if _matches(row)]
    severity_order = {"high": 0, "medium": 1, "low": 2}
    if sort_key == "score_asc":
        filtered.sort(key=lambda row: (float(row.get("sort_overall", -1.0) or -1.0), str(row.get("student_id", ""))))
    elif sort_key == "score_desc":
        filtered.sort(key=lambda row: (-float(row.get("sort_overall", -1.0) or -1.0), str(row.get("student_id", ""))))
    elif sort_key == "grade":
        filtered.sort(key=lambda row: (-int(row.get("sort_grade", 0) or 0), str(row.get("student_id", ""))))
    else:
        filtered.sort(
            key=lambda row: (
                severity_order.get(str(row.get("severity", "low")).lower(), 3),
                float(row.get("sort_overall", -1.0) or -1.0),
                str(row.get("student_id", "")),
            )
        )
    return filtered


def _csv_response(filename: str, fieldnames: list[str], rows: list[dict]) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_response(filename: str, rows: list[dict]) -> Response:
    import json
    return Response(
        json.dumps(rows, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _txt_response(filename: str, title: str, fieldnames: list[str], rows: list[dict]) -> Response:
    lines = []
    lines.append("=" * 70)
    lines.append(title.upper())
    lines.append("=" * 70)
    lines.append("")

    for i, row in enumerate(rows, 1):
        lines.append(f"--- Entry {i} ---")
        for field in fieldnames:
            val = row.get(field, "")
            lines.append(f"  {field}: {val}")
        lines.append("")

    lines.append("=" * 70)
    lines.append(f"Total entries: {len(rows)}")
    return Response(
        "\n".join(lines),
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _pdf_response(filename: str, title: str, fieldnames: list[str], rows: list[dict]) -> Response:
    """Generate a PDF report as a direct file download."""
    pdf = build_records_pdf(title, fieldnames, rows, record_label="Row")
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _filtered_top_rule_rows(analytics: Mapping[str, object], args) -> list[dict]:
    rows = list(analytics.get("top_failing_rules", []) or [])
    severity = str(args.get("severity", "")).strip().upper()
    component = str(args.get("component", "")).strip().lower()
    impact_type = str(args.get("impact_type", "")).strip().lower()
    return [
        row
        for row in rows
        if (not severity or str(row.get("severity", "")).upper() == severity)
        and (not component or str(row.get("component", "")).lower() == component)
        and (not impact_type or str(row.get("impact_type", "")).lower() == impact_type)
    ]


def _normalize_teaching_insights(insights: Sequence[object] | None) -> list[dict]:
    normalized: list[dict] = []
    for index, insight in enumerate(list(insights or []), start=1):
        if isinstance(insight, Mapping):
            insight_type = str(insight.get("type") or insight.get("insight_type") or f"insight_{index}")
            priority = str(insight.get("priority") or "medium").strip().lower()
            if priority not in TEACHING_INSIGHT_PRIORITIES:
                priority = "medium"
            evidence_keys = [
                str(key)
                for key in list(insight.get("evidence_keys", insight.get("supporting_metric_keys", [])) or [])
                if str(key).strip()
            ]
            normalized.append(
                {
                    "insight_type": insight_type,
                    "type": insight_type,
                    "priority": priority,
                    "title": str(insight.get("title") or "").strip(),
                    "text": str(insight.get("text") or "").strip(),
                    "supporting_metric_keys": evidence_keys,
                    "evidence_keys": evidence_keys,
                }
            )
            continue
        text = str(insight or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "insight_type": f"insight_{index}",
                "type": f"insight_{index}",
                "priority": "medium",
                "title": "",
                "text": text,
                "supporting_metric_keys": [],
                "evidence_keys": [],
            }
        )
    return normalized


def _numeric_tokens(value: object) -> set[str]:
    return set(NUMERIC_TOKEN_RE.findall(str(value or "")))


def _validation_failure(category: str, message: str, *, field: str | None = None, value: object = None) -> dict[str, Any]:
    failure: dict[str, Any] = {
        "category": str(category or "schema_error"),
        "message": str(message or "Validation failed."),
    }
    if field:
        failure["field"] = field
    if value is not None:
        if isinstance(value, (str, int, float, bool)) or value is None:
            failure["value"] = value
        else:
            failure["value"] = str(value)[:200]
    return failure


def _iter_numeric_context_values(value: object, path: str = ""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_numeric_context_values(child, child_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _iter_numeric_context_values(child, child_path)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield path, float(value)


def _is_percentlike_numeric_path(path: str, value: float) -> bool:
    path_lower = str(path or "").lower()
    if any(token in path_lower for token in PERCENTLIKE_PATH_HINTS):
        return True
    if any(token in path_lower for token in COUNTLIKE_PATH_HINTS):
        return False
    return not float(value).is_integer()


def _build_numeric_grounding(context: Mapping[str, object]) -> dict[str, Any]:
    active_in_scope = int(context.get("active_in_scope") or 0)
    assigned_students = int(context.get("assigned_students") or 0)
    denominators = [value for value in {active_in_scope, assigned_students} if value > 0]
    exact_counts: set[int] = set()
    percentlike_values: list[float] = []

    for path, numeric_value in _iter_numeric_context_values(context):
        if _is_percentlike_numeric_path(path, numeric_value):
            percentlike_values.append(float(numeric_value))
            continue
        if float(numeric_value).is_integer():
            exact_count = int(numeric_value)
            exact_counts.add(exact_count)
            for denominator in denominators:
                if 0 <= exact_count <= denominator:
                    percentlike_values.append((exact_count / denominator) * 100.0)
        else:
            percentlike_values.append(float(numeric_value))

    return {
        "counts": exact_counts,
        "percentlike_values": percentlike_values,
    }


def _extract_numeric_mentions(text: str) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    source = str(text or "")
    lowered = source.lower()
    structural_range_spans: list[tuple[int, int]] = []
    for range_match in STRUCTURAL_RANGE_RE.finditer(source):
        start, end = range_match.span()
        window = lowered[max(0, start - 20): min(len(lowered), end + 20)]
        local_context = source[max(0, start - 4): min(len(source), end + 8)]
        if any(token in window for token in STRUCTURAL_RANGE_HINTS) or "'" in local_context or '"' in local_context:
            structural_range_spans.append((start, end))
    for match in NUMERIC_TOKEN_RE.finditer(source):
        start, end = match.span()
        if any(span_start <= start and end <= span_end for span_start, span_end in structural_range_spans):
            continue
        raw = match.group(0)
        bare = raw[:-1] if raw.endswith("%") else raw
        try:
            value = float(bare)
        except ValueError:
            continue
        window = lowered[max(0, start - 18): min(len(lowered), end + 24)]
        kind = "count"
        if raw.endswith("%") or "." in bare or any(token in window for token in ("percent", "percentage", "score", "mark", "average", "median", "mean")):
            kind = "percent"
        mentions.append(
            {
                "raw": raw,
                "value": value,
                "kind": kind,
                "start": start,
                "end": end,
                "window": window,
            }
        )
    return mentions


def _numeric_tolerance(raw: str, kind: str) -> float:
    if kind == "count":
        return 0.0
    bare = str(raw or "").rstrip("%")
    if "." not in bare:
        return 0.51
    decimals = len(bare.split(".", 1)[1])
    if decimals == 1:
        return 0.11
    return 0.02


def _validate_numeric_grounding(text: str, context: Mapping[str, object], field: str) -> dict[str, Any] | None:
    grounding = _build_numeric_grounding(context)
    allowed_counts = grounding["counts"]
    allowed_percentlike = grounding["percentlike_values"]
    for mention in _extract_numeric_mentions(text):
        if mention["kind"] == "count":
            if not float(mention["value"]).is_integer() or int(mention["value"]) not in allowed_counts:
                return _validation_failure(
                    "numeric_mismatch",
                    f"Numeric claim '{mention['raw']}' is not grounded in the analytics payload.",
                    field=field,
                    value=mention["raw"],
                )
            continue
        tolerance = _numeric_tolerance(mention["raw"], mention["kind"])
        if not any(abs(float(mention["value"]) - float(candidate)) <= tolerance for candidate in allowed_percentlike):
            return _validation_failure(
                "numeric_mismatch",
                f"Numeric claim '{mention['raw']}' is not a supported rounded value from the analytics payload.",
                field=field,
                value=mention["raw"],
            )
    return None


def _supports_majority_claim(evidence_keys: Sequence[str], context: Mapping[str, object]) -> bool:
    active_in_scope = int(context.get("active_in_scope") or 0)
    if active_in_scope <= 0:
        return False

    def is_majority(value: object) -> bool:
        try:
            return float(value or 0) > (active_in_scope / 2)
        except (TypeError, ValueError):
            return False

    evidence = set(str(key) for key in evidence_keys)
    if "dominant_score_band" in evidence:
        dominant = dict(context.get("dominant_score_band", {}) or {})
        if is_majority(dominant.get("count")):
            return True
    if "score_band_distribution" in evidence:
        for band in list(context.get("score_band_distribution", []) or []):
            if is_majority((band or {}).get("count")):
                return True
    if "confidence_mix" in evidence:
        mix = dict(context.get("confidence_mix", {}) or {})
        for level in ("high", "medium", "low"):
            if is_majority((mix.get(level) or {}).get("count")):
                return True
    if "manual_review" in evidence and is_majority(context.get("manual_review")):
        return True
    if "top_failing_rule" in evidence:
        top_rule = dict(context.get("top_failing_rule", {}) or {})
        if is_majority(top_rule.get("submissions_affected")):
            return True
    if "top_failing_rules" in evidence:
        for item in list(context.get("top_failing_rules", []) or []):
            if is_majority((item or {}).get("submissions_affected")):
                return True
    if "major_limitations" in evidence:
        for item in list(context.get("major_limitations", []) or []):
            if is_majority((item or {}).get("incident_count")):
                return True
    if "requirement_coverage_summary" in evidence:
        for row in list(context.get("requirement_coverage_summary", []) or []):
            row = dict(row or {})
            if any(
                is_majority(row.get(key))
                for key in ("met_count", "partial_count", "unmet_count", "not_evaluable_count")
            ):
                return True
    if "high_priority_flagged_submissions" in evidence:
        flagged = dict(context.get("high_priority_flagged_submissions", {}) or {})
        if any(
            is_majority(flagged.get(key))
            for key in ("count", "medium_or_higher_count", "low_confidence_count", "manual_review_count")
        ):
            return True
    return False


def _validate_confidence_scope_claim(text: str, context: Mapping[str, object], *, field: str = "text") -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    mix = dict(context.get("confidence_mix", {}) or {})
    active_in_scope = int(context.get("active_in_scope") or 0)
    for level in ("low", "medium", "high"):
        if f"{level} confidence" not in lowered:
            continue
        level_data = dict(mix.get(level, {}) or {})
        expected_count = int(level_data.get("count", 0) or 0)
        expected_percent = float(level_data.get("percent", 0) or 0.0)
        majority_match = re.search(rf"(most students|most submissions|majority)[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence", lowered)
        if majority_match and active_in_scope and expected_count <= (active_in_scope / 2):
            return _validation_failure(
                "unsupported_claim",
                f"{level.title()} confidence is described as a majority without supporting cohort evidence.",
                field=field,
                value=text,
            )
        fraction_match = re.search(rf"(\d+(?:\.\d+)?)\s+out of\s+(\d+)[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence", lowered)
        if fraction_match:
            claimed_count = int(float(fraction_match.group(1)))
            claimed_total = int(float(fraction_match.group(2)))
            if claimed_count != expected_count or (active_in_scope and claimed_total != active_in_scope):
                return _validation_failure(
                    "unsupported_claim",
                    f"{level.title()} confidence scope claim is not supported by confidence mix data.",
                    field=field,
                    value=text,
                )
        percent_match = re.search(rf"(\d+(?:\.\d+)?)%[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence", lowered)
        if percent_match:
            claimed_percent = float(percent_match.group(1))
            if abs(claimed_percent - expected_percent) > _numeric_tolerance(percent_match.group(1) + "%", "percent"):
                return _validation_failure(
                    "unsupported_claim",
                    f"{level.title()} confidence percentage claim is not supported by confidence mix data.",
                    field=field,
                    value=text,
                )
    return None


def _validate_manual_review_claim(text: str, context: Mapping[str, object], *, field: str = "text") -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    if "manual review" not in lowered:
        return None
    expected_count = int(context.get("manual_review", 0) or 0)
    active_in_scope = int(context.get("active_in_scope", 0) or 0)
    direct_match = re.search(r"manual review(?: is)? recommended(?: for)?\s+(\d+(?:\.\d+)?)", lowered)
    if direct_match:
        claimed_count = int(float(direct_match.group(1)))
        if claimed_count != expected_count:
            return _validation_failure(
                "unsupported_claim",
                "Manual review scope claim is not supported by the analytics payload.",
                field=field,
                value=text,
            )
    fraction_match = re.search(r"(\d+(?:\.\d+)?)\s+out of\s+(\d+)[^.]*manual review", lowered)
    if fraction_match:
        claimed_count = int(float(fraction_match.group(1)))
        claimed_total = int(float(fraction_match.group(2)))
        if claimed_count != expected_count or (active_in_scope and claimed_total != active_in_scope):
            return _validation_failure(
                "unsupported_claim",
                "Manual review scope claim is not supported by the analytics payload.",
                field=field,
                value=text,
            )
    return None


def _validate_semantic_claims(
    text: str,
    evidence_keys: Sequence[str],
    context: Mapping[str, object],
    *,
    field: str = "text",
) -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    evidence = {str(key).strip() for key in evidence_keys if str(key).strip()}
    if any(marker in lowered for marker in ("most students", "most submissions", "majority", "dominant")):
        if not _supports_majority_claim(list(evidence), context):
            return _validation_failure(
                "unsupported_claim",
                "Majority or dominant language is not supported by the provided analytics.",
                field=field,
                value=text,
            )
    if "most common" in lowered and not evidence.intersection({"top_failing_rule", "top_failing_rules", "major_rule_categories"}):
        return _validation_failure(
            "unsupported_claim",
            "Most-common language requires rule-level evidence keys.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "strongest" in lowered and not evidence.intersection({"strongest_requirement", "component_performance_summary", "requirement_coverage_summary"}):
        return _validation_failure(
            "unsupported_claim",
            "Strongest-area language requires requirement or component evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "weakest" in lowered and not evidence.intersection({"weakest_requirement", "component_performance_summary", "requirement_coverage_summary"}):
        return _validation_failure(
            "unsupported_claim",
            "Weakest-area language requires requirement or component evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "manual review" in lowered and not evidence.intersection({"manual_review", "high_priority_flagged_submissions", "major_limitations"}):
        return _validation_failure(
            "unsupported_claim",
            "Manual-review language requires review or limitation evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "confidence" in lowered and not evidence.intersection({"confidence_mix", "high_priority_flagged_submissions", "major_limitations", "manual_review"}):
        return _validation_failure(
            "unsupported_claim",
            "Confidence language requires confidence or limitation evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if any(marker in lowered for marker in SEMANTIC_CAUSE_MARKERS) and not evidence.intersection(SEMANTIC_CLAIM_EVIDENCE_KEYS):
        return _validation_failure(
            "unsupported_claim",
            "Causal language requires stronger supporting evidence keys.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    confidence_scope_error = _validate_confidence_scope_claim(text, context, field=field)
    if confidence_scope_error is not None:
        return confidence_scope_error
    manual_review_error = _validate_manual_review_claim(text, context, field=field)
    if manual_review_error is not None:
        return manual_review_error
    return None


def _user_facing_teaching_summary_fallback(reason: Mapping[str, object] | None, *, validation_rejected: bool) -> str:
    if not validation_rejected:
        return "LLM summary was unavailable. Deterministic wording remains in place."
    code = str((reason or {}).get("category") or "").strip().lower()
    label_map = {
        "invalid_json": "invalid JSON response",
        "schema_error": "schema validation failed",
        "missing_required_fields": "required fields were missing",
        "invalid_priority": "priority validation failed",
        "invalid_type": "insight type validation failed",
        "unsupported_evidence_key": "unsupported evidence key detected",
        "numeric_mismatch": "numeric validation failed",
        "unsupported_claim": "unsupported claim detected",
        "too_many_insights": "too many insights were returned",
        "too_few_insights": "too few insights were returned",
    }
    label = label_map.get(code, "validation failed")
    return f"LLM summary was generated but rejected during validation; deterministic wording is shown instead ({label})."


def _validate_enhanced_teaching_summary(
    candidate: object,
    context: Mapping[str, object],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(candidate, Mapping):
        return None, _validation_failure("schema_error", "LLM summary root must be a JSON object.", field="summary")
    if str(candidate.get("summary_mode") or "").strip() != "llm_teacher_insight":
        return None, _validation_failure(
            "missing_required_fields",
            "LLM summary must include summary_mode='llm_teacher_insight'.",
            field="summary_mode",
            value=candidate.get("summary_mode"),
        )
    headline = str(candidate.get("headline") or "").strip()
    if not headline or len(headline) < 24 or len(headline) > 240:
        return None, _validation_failure(
            "missing_required_fields" if not headline else "schema_error",
            "Headline is missing or outside the supported length.",
            field="headline",
            value=headline,
        )
    raw_insights = candidate.get("insights")
    if not isinstance(raw_insights, list):
        return None, _validation_failure("schema_error", "Insights must be returned as a JSON array.", field="insights")
    if len(raw_insights) < 4:
        return None, _validation_failure("too_few_insights", "LLM summary returned fewer than 4 insights.", field="insights", value=len(raw_insights))
    if len(raw_insights) > 6:
        return None, _validation_failure("too_many_insights", "LLM summary returned more than 6 insights.", field="insights", value=len(raw_insights))

    allowed_evidence_keys = {str(key).strip() for key in context.keys() if str(key).strip()}
    headline_numeric_error = _validate_numeric_grounding(headline, context, "headline")
    if headline_numeric_error is not None:
        return None, headline_numeric_error

    has_strength_or_pattern = False
    has_weakness = False
    has_recommendation = False
    has_reliability_interpretation = False
    reliability_issues_present = any(
        bool(context.get(key))
        for key in (
            "manual_review",
            "partially_evaluated",
            "not_analysable",
            "limitation_incidents",
            "runtime_skip_count",
            "browser_skip_count",
            "runtime_failure_count",
            "browser_failure_count",
        )
    )
    validated: list[dict] = []
    seen_titles: set[str] = set()
    for index, generated in enumerate(raw_insights, start=1):
        if not isinstance(generated, Mapping):
            return None, _validation_failure(
                "schema_error",
                "Each insight must be a JSON object.",
                field=f"insights[{index}]",
            )
        priority = str(generated.get("priority") or "").strip().lower()
        insight_type = str(generated.get("type") or generated.get("insight_type") or "").strip().lower()
        title = str(generated.get("title") or "").strip()
        text = str(generated.get("text") or "").strip()
        evidence_keys = [
            str(key).strip()
            for key in list(generated.get("evidence_keys", generated.get("supporting_metric_keys", [])) or [])
            if str(key).strip()
        ]
        if priority not in TEACHING_INSIGHT_PRIORITIES:
            return None, _validation_failure(
                "invalid_priority",
                "Insight priority is not one of high, medium, or low.",
                field=f"insights[{index}].priority",
                value=priority,
            )
        if insight_type not in TEACHING_INSIGHT_TYPES:
            return None, _validation_failure(
                "invalid_type",
                "Insight type is not part of the supported teacher-insight taxonomy.",
                field=f"insights[{index}].type",
                value=insight_type,
            )
        if not title or len(title) > 90:
            return None, _validation_failure(
                "missing_required_fields" if not title else "schema_error",
                "Insight title is missing or too long.",
                field=f"insights[{index}].title",
                value=title,
            )
        if title.lower() in seen_titles:
            return None, _validation_failure(
                "schema_error",
                "Insight titles must be unique within the summary.",
                field=f"insights[{index}].title",
                value=title,
            )
        seen_titles.add(title.lower())
        if not text or len(text) < 40 or len(text) > 420:
            return None, _validation_failure(
                "missing_required_fields" if not text else "schema_error",
                "Insight text is missing or outside the supported length.",
                field=f"insights[{index}].text",
                value=text,
            )
        if not evidence_keys:
            return None, _validation_failure(
                "missing_required_fields",
                "Each insight must include at least one evidence key.",
                field=f"insights[{index}].evidence_keys",
            )
        unsupported_key = next((key for key in evidence_keys if key not in allowed_evidence_keys), None)
        if unsupported_key is not None:
            return None, _validation_failure(
                "unsupported_evidence_key",
                "Insight references an evidence key that is not present in the analytics payload.",
                field=f"insights[{index}].evidence_keys",
                value=unsupported_key,
            )
        semantic_error = _validate_semantic_claims(text, evidence_keys, context, field=f"insights[{index}].text")
        if semantic_error is not None:
            return None, semantic_error
        numeric_error = _validate_numeric_grounding(text, context, f"insights[{index}].text")
        if numeric_error is not None:
            return None, numeric_error
        if insight_type in {"strength", "pattern"}:
            has_strength_or_pattern = True
        if insight_type == "weakness":
            has_weakness = True
        if insight_type == "recommendation":
            has_recommendation = True
        if set(evidence_keys).intersection(RELIABILITY_EVIDENCE_KEYS):
            has_reliability_interpretation = True
        validated.append(
            {
                "insight_type": insight_type,
                "type": insight_type,
                "priority": priority,
                "title": title,
                "text": text,
                "supporting_metric_keys": evidence_keys,
                "evidence_keys": evidence_keys,
            }
        )
    if not has_strength_or_pattern or not has_weakness or not has_recommendation:
        return None, _validation_failure(
            "schema_error",
            "The summary must include at least one strength or positive pattern, one weakness, and one recommendation.",
            field="insights",
        )
    if reliability_issues_present and not has_reliability_interpretation:
        return None, _validation_failure(
            "unsupported_claim",
            "Reliability issues are present but no reliability-aware insight was returned.",
            field="insights",
        )
    return (
        {
            "summary_mode": "llm_teacher_insight",
            "headline": headline,
            "insights": validated,
        },
        None,
    )


def _llm_summary_enabled() -> bool:
    if current_app.testing and "AMS_ENABLE_ANALYTICS_LLM_SUMMARY" not in current_app.config:
        return False
    value = current_app.config.get("AMS_ENABLE_ANALYTICS_LLM_SUMMARY", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return value is not False


def _maybe_enhance_teaching_insights(analytics: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    baseline = _normalize_teaching_insights(analytics.get("teaching_insights"))
    context = dict(analytics.get("teaching_insight_context", {}) or {})
    deterministic_result = {
        "summary_mode": "deterministic",
        "headline": "",
        "insights": baseline,
    }
    if not baseline:
        return deterministic_result, "deterministic", {}
    if not _llm_summary_enabled():
        return deterministic_result, "deterministic", {}

    try:
        provider = get_llm_provider()
        prompt_payload = {
            "assignment_analytics": context,
            "valid_evidence_keys": sorted(str(key) for key in context.keys()),
        }
        system_prompt = (
            "You are generating a teacher-facing assignment analytics insight summary for an automated marking system.\n"
            "Use only the structured analytics provided for this one assignment.\n"
            "Your job is to interpret the evidence for a teacher or marker, not to restate metrics.\n"
            "Do not paraphrase the deterministic summary. Do not merely restate counts, percentages, or rankings. "
            "Do not give generic advice that could apply to any cohort.\n"
            "Every insight must explain significance: what the pattern means, why it may be happening, why it matters, "
            "or what the teacher should do next.\n"
            "Prioritise the most important cohort pattern first, then meaningful strengths, recurring weaknesses, "
            "notable anomalies, grounded contributing factors, and practical next actions.\n"
            "If reliability or confidence issues are present, treat them as part of the interpretation rather than a footnote.\n"
            "Remain grounded in the provided analytics only. Do not invent facts, causes, or recommendations that are not plausibly supported.\n"
            "Write for teachers and markers in concise academic/admin language.\n"
            "Avoid shallow outputs such as 'JavaScript is strongest and SQL is weakest', "
            "'4 submissions were partially evaluated', or 'Rule X affected 4 submissions' unless you explain what that means.\n"
            "Return JSON only in this exact structure:\n"
            "{\n"
            '  "summary_mode": "llm_teacher_insight",\n'
            '  "headline": "<one-sentence overall interpretation>",\n'
            '  "insights": [\n'
            "    {\n"
            '      "priority": "high|medium|low",\n'
            '      "type": "pattern|strength|weakness|anomaly|cause|recommendation|trend",\n'
            '      "title": "<short teacher-facing label>",\n'
            '      "text": "<1-3 sentence grounded interpretation>",\n'
            '      "evidence_keys": ["<analytics_key>", "<analytics_key>"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Provide 4 to 6 insights. Include at least one weakness, at least one recommendation, and at least one positive pattern or strength. "
            "If reliability issues are present, include at least one reliability-aware interpretation."
        )
        response = provider.complete(
            json.dumps(prompt_payload, indent=2, sort_keys=True),
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=1400,
            json_mode=True,
        )
        if not response.success:
            reason = _validation_failure(
                "generation_unavailable",
                str(response.error or "LLM summary enhancement failed"),
                field="provider",
            )
            logger.info("Teaching insight generation failed: %s", json.dumps(reason, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "unavailable",
                "fallback_reason_code": reason["category"],
                "fallback_reason": _user_facing_teaching_summary_fallback(None, validation_rejected=False),
            }
        try:
            payload = json.loads(clean_json_response(response.content))
        except json.JSONDecodeError:
            reason = _validation_failure("invalid_json", "LLM summary response could not be parsed as valid JSON.")
            logger.info("Teaching insight validation failed: %s", json.dumps(reason, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "rejected",
                "fallback_reason_code": reason["category"],
                "fallback_reason": _user_facing_teaching_summary_fallback(reason, validation_rejected=True),
            }
        validated, reason = _validate_enhanced_teaching_summary(payload, context)
        if validated is None:
            logger.info("Teaching insight validation failed: %s", json.dumps(reason or {}, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "rejected",
                "fallback_reason_code": str((reason or {}).get("category") or "schema_error"),
                "fallback_reason": _user_facing_teaching_summary_fallback(reason, validation_rejected=True),
            }
        return validated, "llm", {}
    except Exception as exc:
        reason = _validation_failure("generation_unavailable", str(exc or "LLM summary enhancement failed"), field="provider")
        logger.info("Teaching insight generation failed: %s", json.dumps(reason, sort_keys=True))
        return deterministic_result, "deterministic", {
            "validation_status": "unavailable",
            "fallback_reason_code": "generation_unavailable",
            "fallback_reason": _user_facing_teaching_summary_fallback(None, validation_rejected=False),
        }


@teacher_bp.route("/")
@teacher_or_admin_required
def dashboard():
    user = get_current_user()
    if user["role"] == "admin":
        assignments = list_assignments()
    else:
        assignments = list_assignments(teacher_id=user["userID"])
    students = list_users(role="student")
    return render_template(
        "teacher_dashboard.html",
        assignments=assignments,
        students=students,
    )


@teacher_bp.route("/create-assignment", methods=["GET", "POST"])
@teacher_or_admin_required
def create_assignment_route():
    if request.method == "GET":
        students = list_users(role="student")
        teachers = list_users(role="teacher")
        return render_template("teacher_create_assignment.html", students=students, teachers=teachers)

    user = get_current_user()
    assignment_id = request.form.get("assignment_id", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    profile = request.form.get("profile", "frontend_interactive").strip()
    due_date = request.form.get("due_date", "").strip()
    selected_students = request.form.getlist("students")
    valid_teacher_ids = {teacher["userID"] for teacher in list_users(role="teacher")}
    selected_teachers = [
        teacher_id
        for teacher_id in request.form.getlist("teachers")
        if teacher_id in valid_teacher_ids and teacher_id != user["userID"]
    ]

    if not assignment_id or not title:
        flash("Assignment ID and Title are required.", "error")
        return redirect(url_for("teacher.dashboard"))

    ok = create_assignment(
        assignment_id=assignment_id,
        teacher_id=user["userID"],
        title=title,
        description=description,
        profile=profile,
        assigned_students=selected_students,
        assigned_teachers=selected_teachers,
        due_date=due_date,
    )
    if ok:
        flash(f"Assignment '{assignment_id}' created successfully.", "success")
    else:
        flash(f"Assignment ID '{assignment_id}' already exists.", "error")
    return redirect(url_for("teacher.dashboard"))


@teacher_bp.route("/assignment/<assignment_id>/students", methods=["POST"])
@teacher_or_admin_required
def update_students(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    selected = request.form.getlist("students")
    update_assignment_students(assignment_id, selected)
    flash(f"Student list updated for '{assignment_id}'.", "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>/teachers", methods=["POST"])
@teacher_or_admin_required
def update_teachers(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    valid_teacher_ids = {teacher["userID"] for teacher in list_users(role="teacher")}
    selected_teachers = [
        teacher_id
        for teacher_id in request.form.getlist("teachers")
        if teacher_id in valid_teacher_ids and teacher_id != assignment.get("teacherID")
    ]
    update_assignment_teachers(assignment_id, selected_teachers)
    flash(f"Teaching team updated for '{assignment_id}'.", "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>")
@teacher_or_admin_required
def assignment_detail(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    student_details = []
    for sid in assignment.get("assigned_students", []):
        user = get_user(sid)
        if user:
            student_details.append(user)
        else:
            student_details.append({"userID": sid, "firstName": sid, "lastName": "", "email": ""})

    teacher_details = [_teacher_user_lookup(teacher_id) for teacher_id in assignment_teacher_ids(assignment)]
    all_students = list_users(role="student")
    all_teachers = [
        teacher
        for teacher in list_users(role="teacher")
        if teacher["userID"] != assignment.get("teacherID")
    ]
    assignment_runs = _build_assignment_run_rows(assignment_id)
    submission_groups = _build_assignment_submission_groups(assignment_runs)
    threat_rows = _build_threat_resolution_rows(assignment_runs)
    llm_error_rows = _build_llm_error_resolution_rows(assignment_runs)

    return render_template(
        "assignment_detail.html",
        assignment=assignment,
        student_details=student_details,
        teacher_details=teacher_details,
        all_teachers=all_teachers,
        all_students=all_students,
        runs=assignment_runs,
        submission_groups=submission_groups,
        threat_rows=threat_rows,
        llm_error_rows=llm_error_rows,
        has_unresolved_threats=bool(threat_rows),
        has_unresolved_llm_errors=bool(llm_error_rows),
        has_release_blockers=bool(threat_rows or llm_error_rows),
    )


@teacher_bp.route("/assignment/<assignment_id>/release", methods=["POST"])
@teacher_or_admin_required
def release(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    assignment_runs = _build_assignment_run_rows(assignment_id)
    threat_rows = _build_threat_resolution_rows(assignment_runs)
    llm_error_rows = _build_llm_error_resolution_rows(assignment_runs)
    if threat_rows or llm_error_rows:
        flash(
            "Grades cannot be released while flagged submissions remain. "
            "Resolve all threat-detected or LLM-error submissions first.",
            "error",
        )
        return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))
    release_marks(assignment_id)
    flash(f"Marks released for '{assignment_id}'.", "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>/withhold", methods=["POST"])
@teacher_or_admin_required
def withhold(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    withhold_marks(assignment_id)
    flash(f"Marks withheld for '{assignment_id}'.", "info")
    return redirect(url_for("teacher.dashboard"))


@teacher_bp.route("/assignment/<assignment_id>/analytics")
@teacher_or_admin_required
def view_analytics(assignment_id: str):
    """Render fresh analytics for a single assignment."""
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics generation failed for %s: %s", assignment_id, exc)
        flash(f"Analytics generation failed: {exc}", "error")
        return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

    coverage = dict(analytics.get("coverage", {}) or {})
    assigned_count = int(coverage.get("assigned_students") or len(assignment.get("assigned_students", []) or []))
    submitted_count = int(coverage.get("active_in_scope") or analytics.get("submission_count") or analytics.get("overall", {}).get("total") or 0)
    missing_count = int(coverage.get("missing_assigned") or max(assigned_count - submitted_count, 0))
    coverage_percent = int(coverage.get("coverage_percent") or (round((submitted_count / assigned_count) * 100) if assigned_count else 0))
    updated_label, updated_exact = _format_freshness_label(analytics.get("generated_at"))
    teaching_insights = _normalize_teaching_insights(analytics.get("teaching_insights"))
    teaching_summary_source = "deterministic"

    return render_template(
        "assignment_analytics.html",
        assignment=assignment,
        analytics=analytics,
        teaching_insights=teaching_insights,
        teaching_summary_source=teaching_summary_source,
        assigned_count=assigned_count,
        submitted_count=submitted_count,
        missing_count=missing_count,
        coverage_percent=coverage_percent,
        updated_label=updated_label,
        updated_exact=updated_exact,
        llm_summary_enabled=_llm_summary_enabled(),
    )


@teacher_bp.route("/assignment/<assignment_id>/analytics/teaching-insights.json")
@teacher_or_admin_required
def teaching_insights_json(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        return jsonify({"error": "Assignment not found."}), 404
    if not _user_can_access_assignment(assignment):
        return jsonify({"error": "You do not have access to this assignment."}), 403

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Teaching insight generation failed for %s: %s", assignment_id, exc)
        return jsonify({"error": str(exc)}), 500

    summary, source, meta = _maybe_enhance_teaching_insights(analytics)
    payload = {
        "assignment_id": assignment_id,
        "source": source,
        "summary_mode": summary.get("summary_mode", "deterministic"),
        "headline": summary.get("headline", ""),
        "insights": summary.get("insights", []),
    }
    payload.update(meta)
    return jsonify(payload)


@teacher_bp.route("/assignment/<assignment_id>/analytics/export/<export_kind>.csv")
@teacher_or_admin_required
def export_analytics_csv(assignment_id: str, export_kind: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics export failed for %s: %s", assignment_id, exc)
        flash(f"Analytics export failed: {exc}", "error")
        return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))

    if export_kind == "needs-attention":
        rows = _filtered_needs_attention_rows(analytics, request.args)
        export_rows = [
            {
                "student_id": row.get("student_id", ""),
                "submission_id": row.get("submission_id", ""),
                "severity": row.get("severity", ""),
                "score_percent": round(float(row.get("overall", 0) or 0) * 100, 2) if row.get("overall") is not None else "",
                "grade": row.get("grade", ""),
                "confidence": row.get("confidence", ""),
                "evaluation_state": row.get("evaluation_state", ""),
                "reason": row.get("reason", ""),
                "reason_detail": row.get("reason_detail", ""),
                "flags": "; ".join(row.get("flags", []) or []),
                "related_rule_ids": "; ".join(row.get("matched_rule_ids", []) or []),
                "limitation_details": "; ".join(row.get("limitation_details", []) or []),
                "evidence_excerpt": row.get("evidence_excerpt", ""),
                "manual_review_recommended": "yes" if row.get("manual_review_recommended") else "no",
                "review_note": row.get("review_note", ""),
            }
            for row in rows
        ]
        return _csv_response(
            f"{assignment_id}_needs_attention.csv",
            [
                "student_id",
                "submission_id",
                "severity",
                "score_percent",
                "grade",
                "confidence",
                "evaluation_state",
                "reason",
                "reason_detail",
                "flags",
                "related_rule_ids",
                "limitation_details",
                "evidence_excerpt",
                "manual_review_recommended",
                "review_note",
            ],
            export_rows,
        )

    if export_kind == "rules":
        rows = _filtered_top_rule_rows(analytics, request.args)
        export_rows = [
            {
                "rule_id": row.get("rule_id", ""),
                "label": row.get("label", ""),
                "component": row.get("component", ""),
                "severity": row.get("severity", ""),
                "students_affected": row.get("students_affected", 0),
                "percent_of_active_submissions": round(float(row.get("percent", 0) or 0), 2),
                "incident_count": row.get("incident_count", 0),
                "fail_incidents": row.get("fail_incidents", 0),
                "warning_incidents": row.get("warning_incidents", 0),
                "impact_type": row.get("impact_type", ""),
                "score_impact": row.get("score_impact", ""),
                "example_students": "; ".join(row.get("examples", []) or []),
                "messages": "; ".join(row.get("messages", []) or []),
            }
            for row in rows
        ]
        return _csv_response(
            f"{assignment_id}_top_failing_rules.csv",
            [
                "rule_id",
                "label",
                "component",
                "severity",
                "students_affected",
                "percent_of_active_submissions",
                "incident_count",
                "fail_incidents",
                "warning_incidents",
                "impact_type",
                "score_impact",
                "example_students",
                "messages",
            ],
            export_rows,
        )

    flash("Unknown analytics export.", "error")
    return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>/analytics/export/<export_kind>/<format>")
@teacher_or_admin_required
def export_analytics(assignment_id: str, export_kind: str, format: str):
    """Export analytics in various formats (csv, json, txt, pdf)."""
    if format not in ("csv", "json", "txt", "pdf"):
        flash("Invalid export format.", "error")
        return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))

    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics export failed for %s: %s", assignment_id, exc)
        flash(f"Analytics export failed: {exc}", "error")
        return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))

    if export_kind == "needs-attention":
        rows = _filtered_needs_attention_rows(analytics, request.args)
        fieldnames = [
            "student_id",
            "submission_id",
            "severity",
            "score_percent",
            "grade",
            "confidence",
            "evaluation_state",
            "reason",
            "reason_detail",
            "flags",
            "related_rule_ids",
            "limitation_details",
            "evidence_excerpt",
            "manual_review_recommended",
            "review_note",
        ]
        export_rows = [
            {
                "student_id": row.get("student_id", ""),
                "submission_id": row.get("submission_id", ""),
                "severity": row.get("severity", ""),
                "score_percent": round(float(row.get("overall", 0) or 0) * 100, 2) if row.get("overall") is not None else "",
                "grade": row.get("grade", ""),
                "confidence": row.get("confidence", ""),
                "evaluation_state": row.get("evaluation_state", ""),
                "reason": row.get("reason", ""),
                "reason_detail": row.get("reason_detail", ""),
                "flags": "; ".join(row.get("flags", []) or []),
                "related_rule_ids": "; ".join(row.get("matched_rule_ids", []) or []),
                "limitation_details": "; ".join(row.get("limitation_details", []) or []),
                "evidence_excerpt": row.get("evidence_excerpt", ""),
                "manual_review_recommended": "yes" if row.get("manual_review_recommended") else "no",
                "review_note": row.get("review_note", ""),
            }
            for row in rows
        ]
        title = f"Review Queue - {assignment_id}"
        base_name = f"{assignment_id}_needs_attention"

    elif export_kind == "rules":
        rows = _filtered_top_rule_rows(analytics, request.args)
        fieldnames = [
            "rule_id",
            "label",
            "component",
            "severity",
            "students_affected",
            "percent_of_active_submissions",
            "incident_count",
            "fail_incidents",
            "warning_incidents",
            "impact_type",
            "score_impact",
            "example_students",
            "messages",
        ]
        export_rows = [
            {
                "rule_id": row.get("rule_id", ""),
                "label": row.get("label", ""),
                "component": row.get("component", ""),
                "severity": row.get("severity", ""),
                "students_affected": row.get("students_affected", 0),
                "percent_of_active_submissions": round(float(row.get("percent", 0) or 0), 2),
                "incident_count": row.get("incident_count", 0),
                "fail_incidents": row.get("fail_incidents", 0),
                "warning_incidents": row.get("warning_incidents", 0),
                "impact_type": row.get("impact_type", ""),
                "score_impact": row.get("score_impact", ""),
                "example_students": "; ".join(row.get("examples", []) or []),
                "messages": "; ".join(row.get("messages", []) or []),
            }
            for row in rows
        ]
        title = f"Rule Summary - {assignment_id}"
        base_name = f"{assignment_id}_top_failing_rules"
    else:
        flash("Unknown analytics export type.", "error")
        return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))

    # Return the appropriate format
    if format == "csv":
        return _csv_response(f"{base_name}.csv", fieldnames, export_rows)
    elif format == "json":
        return _json_response(f"{base_name}.json", export_rows)
    elif format == "txt":
        return _txt_response(f"{base_name}.txt", title, fieldnames, export_rows)
    elif format == "pdf":
        return _pdf_response(f"{base_name}.pdf", title, fieldnames, export_rows)

    flash("Invalid export format.", "error")
    return redirect(url_for("teacher.view_analytics", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>/delete", methods=["POST"])
@teacher_or_admin_required
def delete_assignment_route(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))
    if not _user_can_access_assignment(assignment):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher.dashboard"))

    if delete_assignment(assignment_id):
        removed_count = purge_assignment_storage(get_runs_root(current_app), assignment_id)
        flash(
            f"Assignment '{assignment_id}' deleted and {removed_count} stored run artefact(s) removed.",
            "success",
        )
    else:
        flash(f"Could not delete assignment '{assignment_id}'.", "error")
    return redirect(url_for("teacher.dashboard"))
