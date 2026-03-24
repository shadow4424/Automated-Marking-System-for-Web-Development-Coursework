"""Student blueprint — restricted dashboard showing only this student's submissions."""
from __future__ import annotations

import json
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    session,
    url_for,
)

from ams.analytics.assignment_analytics import generate_student_assignment_analytics
from ams.core.factory import get_llm_provider
from ams.core.db import (
    get_assignment,
    get_preview_student,
    list_assignments,
    list_assignments_for_student,
    PREVIEW_STUDENT_ID,
)
from ams.io.web_storage import get_runs_root, list_runs
from ams.llm.utils import clean_json_response
from ams.web.auth import get_current_user, login_required

student_bp = Blueprint("student", __name__, url_prefix="/student")


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _resolve_student_id() -> tuple[str | None, dict | None]:
    """Return (student_id, preview_info) for the student dashboard.

    - For real students: returns (their_id, None)
    - For admin viewing as student: returns (PREVIEW_STUDENT_ID, preview_student_dict)
      Uses a dedicated dummy account - no real student data is accessed.
    """
    user = get_current_user()
    if user["role"] == "student":
        return user["userID"], None
    if session.get("view_as_role") == "student":
        preview = get_preview_student()
        return PREVIEW_STUDENT_ID, preview
    return None, None


def _gather_student_runs(student_id: str) -> tuple[list[dict], set[str]]:
    """Collect runs for *student_id*.

    Returns ``(runs_list, submitted_assignment_ids)`` where
    *submitted_assignment_ids* is the set of assignment IDs for which
    the student has at least one submission.
    """
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root)
    my_runs: list[dict] = []
    submitted_aids: set[str] = set()

    for run in all_runs:
        if run.get("mode") == "mark":
            if run.get("student_id") == student_id:
                aid = run.get("assignment_id", "")
                assignment = get_assignment(aid) if aid else None
                run["_marks_released"] = assignment["marks_released"] if assignment else False
                my_runs.append(run)
                if aid:
                    submitted_aids.add(aid)
        elif run.get("mode") == "batch":
            for rec in run.get("submissions", []) or []:
                if rec.get("student_id") != student_id:
                    continue
                aid = rec.get("assignment_id") or run.get("assignment_id", "")
                assignment = get_assignment(aid) if aid else None
                student_run = dict(run)
                student_run["_submission_record"] = rec
                student_run["_batch_submission_id"] = (
                    rec.get("submission_id") or rec.get("student_id") or rec.get("student_name")
                )
                overall = rec.get("overall")
                student_run["score"] = float(overall) * 100 if isinstance(overall, (int, float)) else None
                student_run["assignment_id"] = aid
                student_run["_marks_released"] = assignment["marks_released"] if assignment else False
                my_runs.append(student_run)
                if aid:
                    submitted_aids.add(aid)
                break

    return my_runs, submitted_aids


def _latest_runs_by_assignment(runs: list[dict]) -> dict[str, dict]:
    """Return the latest run for each assignment ID."""
    latest: dict[str, dict] = {}

    for run in runs:
        assignment_id = str(run.get("assignment_id") or "").strip()
        if not assignment_id:
            continue
        current_latest = latest.get(assignment_id)
        run_key = (str(run.get("created_at") or ""), str(run.get("id") or ""))
        current_key = (
            str(current_latest.get("created_at") or ""),
            str(current_latest.get("id") or ""),
        ) if current_latest else None
        if current_key is None or run_key > current_key:
            latest[assignment_id] = run

    return latest


def _split_assignments(
    assignments: list[dict],
    submitted_aids: set[str],
    latest_runs_by_assignment: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split assignments into *todo* and *completed* lists.

    - **todo**: due date is in the future (or unset)
    - **completed**: due date has passed
    Each assignment gets a ``_uploaded`` boolean flag.
    """
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    todo: list[dict] = []
    completed: list[dict] = []

    for a in assignments:
        a["_uploaded"] = a["assignmentID"] in submitted_aids
        a["_latest_submission_run"] = (
            latest_runs_by_assignment.get(a["assignmentID"])
            if latest_runs_by_assignment
            else None
        )
        due = a.get("due_date", "")
        if due and due < now:
            completed.append(a)
        else:
            todo.append(a)

    return todo, completed


def _student_can_access_assignment(assignment: dict | None, student_id: str | None) -> bool:
    if assignment is None or not student_id:
        return False
    assigned_students = {
        str(current_student).strip()
        for current_student in list(assignment.get("assigned_students", []) or [])
        if str(current_student).strip()
    }
    return str(student_id).strip() in assigned_students


def _student_llm_feedback_enabled() -> bool:
    if current_app.testing and "AMS_ENABLE_ANALYTICS_LLM_SUMMARY" not in current_app.config:
        return False
    value = current_app.config.get("AMS_ENABLE_ANALYTICS_LLM_SUMMARY", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return value is not False


def _deterministic_student_feedback(analytics: dict) -> dict:
    feedback: list[dict[str, object]] = []
    seen_titles: set[str] = set()

    def _append_item(feedback_type: str, title: str, text: str) -> None:
        clean_title = str(title or "").strip()
        clean_text = str(text or "").strip()
        if not clean_title or not clean_text or clean_title in seen_titles:
            return
        feedback.append(
            {
                "type": str(feedback_type or "context"),
                "title": clean_title,
                "text": clean_text,
                "evidence_keys": [],
            }
        )
        seen_titles.add(clean_title)

    for item in list(analytics.get("personal_insights", []) or []):
        _append_item("context", item.get("title", ""), item.get("text", ""))
        if len(feedback) >= 5:
            break

    for item in list(analytics.get("strengths", []) or []):
        if len(feedback) >= 5:
            break
        _append_item("strength", item.get("title", ""), item.get("detail", ""))

    for item in list(analytics.get("improvements", []) or []):
        if len(feedback) >= 5:
            break
        _append_item("action", item.get("title", ""), item.get("detail", ""))

    for item in list(analytics.get("needs_attention", []) or []):
        if len(feedback) >= 5:
            break
        _append_item("confidence", item.get("title", ""), item.get("text", ""))

    return {
        "summary_mode": "deterministic",
        "headline": str((analytics.get("student") or {}).get("summary_line") or ""),
        "feedback": feedback[:5],
    }


def _student_feedback_validation_failure(category: str, message: str) -> dict[str, str]:
    return {"category": str(category or "schema_error"), "message": str(message or "Validation failed.")}


def _validate_student_feedback(candidate: object, context: dict) -> tuple[dict | None, dict | None]:
    allowed_types = {"strength", "weakness", "context", "action", "confidence"}
    banned_markers = (
        "other students",
        "another student",
        "teacher should",
        "markers should",
        "review queue",
        "student id",
        "run id",
        "submission id",
    )

    if not isinstance(candidate, dict):
        return None, _student_feedback_validation_failure("schema_error", "Feedback must be returned as a JSON object.")
    if str(candidate.get("summary_mode") or "").strip() != "llm_student_feedback":
        return None, _student_feedback_validation_failure("schema_error", "Feedback must include summary_mode='llm_student_feedback'.")

    headline = str(candidate.get("headline") or "").strip()
    if not headline or len(headline) > 240:
        return None, _student_feedback_validation_failure("schema_error", "Feedback headline is missing or too long.")
    if any(marker in headline.lower() for marker in banned_markers):
        return None, _student_feedback_validation_failure("unsupported_claim", "Feedback headline contains unsupported audience language.")

    raw_feedback = candidate.get("feedback")
    if not isinstance(raw_feedback, list) or len(raw_feedback) < 3 or len(raw_feedback) > 5:
        return None, _student_feedback_validation_failure("schema_error", "Feedback must contain 3 to 5 items.")

    allowed_evidence_keys = {str(key).strip() for key in context.keys() if str(key).strip()}
    validated: list[dict] = []
    for item in raw_feedback:
        if not isinstance(item, dict):
            return None, _student_feedback_validation_failure("schema_error", "Each feedback item must be a JSON object.")
        feedback_type = str(item.get("type") or "").strip().lower()
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        evidence_keys = [
            str(key).strip()
            for key in list(item.get("evidence_keys", []) or [])
            if str(key).strip()
        ]
        if feedback_type not in allowed_types:
            return None, _student_feedback_validation_failure("schema_error", "Feedback item type is unsupported.")
        if not title or len(title) > 90:
            return None, _student_feedback_validation_failure("schema_error", "Feedback item title is missing or too long.")
        if not text or len(text) < 30 or len(text) > 320:
            return None, _student_feedback_validation_failure("schema_error", "Feedback item text is missing or too long.")
        if any(marker in text.lower() for marker in banned_markers):
            return None, _student_feedback_validation_failure("unsupported_claim", "Feedback item contains teacher-only or peer-specific wording.")
        if not evidence_keys:
            return None, _student_feedback_validation_failure("schema_error", "Each feedback item must cite at least one evidence key.")
        if any(key not in allowed_evidence_keys for key in evidence_keys):
            return None, _student_feedback_validation_failure("unsupported_claim", "Feedback item references unsupported evidence keys.")
        validated.append(
            {
                "type": feedback_type,
                "title": title,
                "text": text,
                "evidence_keys": evidence_keys,
            }
        )

    return {
        "summary_mode": "llm_student_feedback",
        "headline": headline,
        "feedback": validated,
    }, None


def _student_feedback_payload(analytics: dict) -> dict:
    context = dict(analytics.get("feedback_context", {}) or {})
    deterministic = _deterministic_student_feedback(analytics)
    if not _student_llm_feedback_enabled():
        return {**deterministic, "source": "deterministic"}

    try:
        provider = get_llm_provider()
        prompt_payload = {
            "student_assignment_analytics": context,
            "valid_evidence_keys": sorted(str(key) for key in context.keys()),
        }
        system_prompt = (
            "You are generating student-facing personalised assignment feedback for an automated marking system.\n"
            "Use only the structured analytics provided for this one student and anonymous cohort aggregates.\n"
            "Do not mention or imply any other student's identity, result, submission, rank position, or record.\n"
            "Do not mention teacher/admin workflows, moderation queues, run IDs, or technical diagnostics unless they are already translated into student-safe wording.\n"
            "Write in supportive but honest academic language.\n"
            "Be specific, practical, and grounded. No generic encouragement and no speculation.\n"
            "Return JSON only in this exact structure:\n"
            "{\n"
            '  "summary_mode": "llm_student_feedback",\n'
            '  "headline": "<one-sentence student-facing interpretation>",\n'
            '  "feedback": [\n'
            "    {\n"
            '      "type": "strength|weakness|context|action|confidence",\n'
            '      "title": "<short student-facing label>",\n'
            '      "text": "<1-2 sentence grounded feedback>",\n'
            '      "evidence_keys": ["<safe_context_key>"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Provide 3 to 5 feedback items."
        )
        response = provider.complete(
            json.dumps(prompt_payload, indent=2, sort_keys=True),
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=900,
            json_mode=True,
        )
        if not getattr(response, "success", False):
            return {
                **deterministic,
                "source": "deterministic",
                "validation_status": "unavailable",
                "fallback_reason_code": "generation_unavailable",
                "fallback_reason": "LLM feedback was unavailable, so deterministic feedback is shown.",
            }
        try:
            payload = json.loads(clean_json_response(getattr(response, "content", "")))
        except json.JSONDecodeError:
            return {
                **deterministic,
                "source": "deterministic",
                "validation_status": "rejected",
                "fallback_reason_code": "invalid_json",
                "fallback_reason": "LLM feedback was rejected because the response format was invalid.",
            }
        validated, reason = _validate_student_feedback(payload, context)
        if validated is None:
            return {
                **deterministic,
                "source": "deterministic",
                "validation_status": "rejected",
                "fallback_reason_code": str((reason or {}).get("category") or "schema_error"),
                "fallback_reason": "LLM feedback was rejected during validation, so deterministic feedback is shown.",
            }
        return {
            **validated,
            "source": "llm",
            "model": str(getattr(provider, "model_name", "") or ""),
        }
    except Exception:
        return {
            **deterministic,
            "source": "deterministic",
            "validation_status": "unavailable",
            "fallback_reason_code": "generation_unavailable",
            "fallback_reason": "LLM feedback was unavailable, so deterministic feedback is shown.",
        }


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@student_bp.route("/")
@login_required
def dashboard():
    student_id, preview_student = _resolve_student_id()

    assignments: list[dict] = []
    my_runs: list[dict] = []
    submitted_aids: set[str] = set()
    latest_runs_by_assignment: dict[str, dict] = {}

    is_preview = preview_student is not None

    if student_id and not is_preview:
        # Real student - load their actual data
        assignments = list_assignments_for_student(student_id)
        my_runs, submitted_aids = _gather_student_runs(student_id)
        latest_runs_by_assignment = _latest_runs_by_assignment(my_runs)
    elif is_preview:
        # Admin preview mode - show all assignments for UI demo, no runs
        assignments = list_assignments()

    todo, completed = _split_assignments(assignments, submitted_aids, latest_runs_by_assignment)

    return render_template(
        "student_dashboard.html",
        assignments=assignments,
        todo=todo,
        completed=completed,
        my_runs=my_runs,
        recent_runs=my_runs[:3],
        student_id=student_id,
        preview_student=preview_student,
    )


@student_bp.route("/coursework")
@login_required
def coursework():
    student_id, preview_student = _resolve_student_id()

    assignments: list[dict] = []
    my_runs: list[dict] = []
    submitted_aids: set[str] = set()
    latest_runs_by_assignment: dict[str, dict] = {}

    is_preview = preview_student is not None

    if student_id and not is_preview:
        # Real student - load their actual data
        assignments = list_assignments_for_student(student_id)
        my_runs, submitted_aids = _gather_student_runs(student_id)
        latest_runs_by_assignment = _latest_runs_by_assignment(my_runs)
    elif is_preview:
        # Admin preview mode - show all assignments for UI demo, no runs
        assignments = list_assignments()

    todo, completed = _split_assignments(assignments, submitted_aids, latest_runs_by_assignment)

    return render_template(
        "student_coursework.html",
        assignments=assignments,
        todo=todo,
        completed=completed,
        my_runs=my_runs,
        student_id=student_id,
        preview_student=preview_student,
    )


@student_bp.route("/assignment/<assignment_id>/analytics")
@login_required
def assignment_analytics(assignment_id: str):
    student_id, preview_student = _resolve_student_id()
    if preview_student is not None:
        flash("Student analytics are unavailable in preview mode.", "info")
        return redirect(url_for("student.coursework"))

    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("student.coursework"))
    if not _student_can_access_assignment(assignment, student_id):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("student.coursework"))
    if not bool(assignment.get("marks_released")):
        flash("Analytics become available once marks are released for this assignment.", "info")
        return redirect(url_for("student.coursework"))

    try:
        analytics = generate_student_assignment_analytics(assignment_id, str(student_id or ""), app=current_app)
    except Exception as exc:
        flash(str(exc), "error")
        return redirect(url_for("student.coursework"))

    return render_template(
        "student_assignment_analytics.html",
        assignment=assignment,
        analytics=analytics,
        deterministic_feedback=_deterministic_student_feedback(analytics),
        llm_feedback_enabled=_student_llm_feedback_enabled(),
    )


@student_bp.route("/assignment/<assignment_id>/analytics/personal-feedback.json")
@login_required
def personalised_feedback_json(assignment_id: str):
    student_id, preview_student = _resolve_student_id()
    if preview_student is not None:
        return jsonify({"error": "Student analytics are unavailable in preview mode."}), 403

    assignment = get_assignment(assignment_id)
    if assignment is None:
        return jsonify({"error": "Assignment not found."}), 404
    if not _student_can_access_assignment(assignment, student_id):
        return jsonify({"error": "You do not have access to this assignment."}), 403
    if not bool(assignment.get("marks_released")):
        return jsonify({"error": "Analytics become available once marks are released for this assignment."}), 403

    try:
        analytics = generate_student_assignment_analytics(assignment_id, str(student_id or ""), app=current_app)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    payload = _student_feedback_payload(analytics)
    payload["assignment_id"] = assignment_id
    return jsonify(payload)
