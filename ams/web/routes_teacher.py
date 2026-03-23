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
from ams.web.auth import get_current_user, teacher_or_admin_required

logger = logging.getLogger(__name__)

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
    if submission.get("invalid") is True:
        return False
    status = str(submission.get("status") or "").strip().lower()
    if status.startswith("invalid"):
        return False
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
    all_runs = list_runs(runs_root)
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
            normalized.append(
                {
                    "insight_type": str(insight.get("insight_type") or f"insight_{index}"),
                    "priority": str(insight.get("priority") or "medium"),
                    "text": str(insight.get("text") or "").strip(),
                    "supporting_metric_keys": [
                        str(key)
                        for key in list(insight.get("supporting_metric_keys", []) or [])
                        if str(key).strip()
                    ],
                }
            )
            continue
        text = str(insight or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "insight_type": f"insight_{index}",
                "priority": "medium",
                "text": text,
                "supporting_metric_keys": [],
            }
        )
    return normalized


def _numeric_tokens(value: object) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", str(value or "")))


def _validate_enhanced_teaching_insights(
    baseline: Sequence[Mapping[str, object]],
    candidate: object,
    context: Mapping[str, object],
) -> list[dict] | None:
    if not isinstance(candidate, list) or len(candidate) != len(baseline):
        return None

    allowed_numbers = _numeric_tokens(json.dumps(context, sort_keys=True))
    for item in baseline:
        allowed_numbers.update(_numeric_tokens(item.get("text", "")))

    validated: list[dict] = []
    for original, generated in zip(baseline, candidate):
        if isinstance(generated, Mapping):
            text = str(generated.get("text") or "").strip()
        else:
            text = str(generated or "").strip()
        if not text or len(text) > 280:
            return None
        if not _numeric_tokens(text).issubset(allowed_numbers):
            return None
        validated.append(
            {
                "insight_type": str(original.get("insight_type") or ""),
                "priority": str(original.get("priority") or "medium"),
                "text": text,
                "supporting_metric_keys": list(original.get("supporting_metric_keys") or []),
            }
        )
    return validated


def _llm_summary_enabled() -> bool:
    if current_app.testing and "AMS_ENABLE_ANALYTICS_LLM_SUMMARY" not in current_app.config:
        return False
    value = current_app.config.get("AMS_ENABLE_ANALYTICS_LLM_SUMMARY", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return value is not False


def _maybe_enhance_teaching_insights(analytics: Mapping[str, Any]) -> tuple[list[dict], str]:
    baseline = _normalize_teaching_insights(analytics.get("teaching_insights"))
    context = dict(analytics.get("teaching_insight_context", {}) or {})
    if not baseline:
        return [], "deterministic"
    if not _llm_summary_enabled():
        return baseline, "deterministic"

    try:
        provider = get_llm_provider()
        prompt_payload = {
            "context": context,
            "insights": baseline,
        }
        system_prompt = (
            "You are rewriting a teacher-facing analytics summary. "
            "Rewrite the text fields only so they read naturally on a moderation dashboard. "
            "Do not add or remove insights. Keep the same insight order. "
            "Do not change any counts, percentages, rankings, labels, or facts. "
            "Do not invent advice. Only improve phrasing and readability. "
            'Return strict JSON with the shape {"insights":[{"text":"..."}]} and keep each text under 220 characters.'
        )
        response = provider.complete(
            json.dumps(prompt_payload, indent=2, sort_keys=True),
            system_prompt=system_prompt,
            temperature=0.1,
            max_tokens=500,
            json_mode=True,
        )
        if not response.success:
            raise ValueError(response.error or "LLM summary enhancement failed")
        payload = json.loads(clean_json_response(response.content))
        validated = _validate_enhanced_teaching_insights(baseline, payload.get("insights"), context)
        if validated is None:
            raise ValueError("LLM summary enhancement did not match the required schema")
        return validated, "llm"
    except Exception as exc:
        logger.info("Falling back to deterministic teaching insights: %s", exc)
        return baseline, "deterministic"


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

    insights, source = _maybe_enhance_teaching_insights(analytics)
    return jsonify(
        {
            "assignment_id": assignment_id,
            "source": source,
            "insights": insights,
        }
    )


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
