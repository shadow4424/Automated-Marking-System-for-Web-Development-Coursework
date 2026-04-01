"""Shared teacher route helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from flask import Blueprint, current_app, url_for

from ams.core.database import assignment_allows_teacher, get_user
from ams.io.web_storage import get_runs_root, list_runs
from ams.web.auth import get_current_user

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")


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
                    submission.get("submission_id") or submission.get("student_id") or submission.get("student_name")
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
                or any(bool(submission.get("threat_flagged") or submission.get("threat_count")) for submission in matching_submissions)
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
            if run_row.get("llm_error_flagged") and str(run_row.get("status") or "").strip().lower() in {
                "",
                "ok",
                "completed",
                "complete",
                "success",
                "succeeded",
            }:
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
        return url_for("batch.batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
    return url_for("runs.run_detail", run_id=row.get("id"))


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
        history_attempts = [row for row in rows if _assignment_submission_row_key(row) != primary_key]

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

    groups.sort(key=lambda group: str(group["student_id"] or "").lower())
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
                    url_for("batch.batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
                    if row.get("mode") == "batch" and row.get("_batch_submission_id")
                    else url_for("runs.run_detail", run_id=row.get("id"))
                ),
                "threat_count": int(submission.get("threat_count") or 0) if isinstance(submission, Mapping) else 0,
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
                    url_for("batch.batch_submission_view", run_id=row.get("id"), submission_id=row.get("_batch_submission_id"))
                    if row.get("mode") == "batch" and row.get("_batch_submission_id")
                    else url_for("runs.run_detail", run_id=row.get("id"))
                ),
                "llm_error_message": first_message or "LLM-assisted marking failed and requires review.",
                "llm_error_messages": messages,
            }
        )
    return llm_rows


def _matches(
    row: dict,
    *,
    severity: str = "",
    score_band: str = "",
    grade: str = "",
    confidence: str = "",
    reason: str = "",
    flag: str = "",
    student: str = "",
    rule: str = "",
    signal_students: set[str] | None = None,
    signal_rules: set[str] | None = None,
) -> bool:
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
    signal_students = signal_students or set()
    signal_rules = signal_rules or set()
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

    filtered = [
        row
        for row in rows
        if _matches(
            row,
            severity=severity,
            score_band=score_band,
            grade=grade,
            confidence=confidence,
            reason=reason,
            flag=flag,
            student=student,
            rule=rule,
            signal_students=signal_students,
            signal_rules=signal_rules,
        )
    ]
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
