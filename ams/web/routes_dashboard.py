"""Dashboard and app-wide utility routes for the AMS web UI."""
from __future__ import annotations

from typing import Any, Mapping

from flask import Blueprint, current_app, flash, jsonify, redirect, session, url_for

from ams.core.db import assignment_allows_teacher, get_assignment, list_assignments_for_student
from ams.core.job_manager import job_manager
from ams.io.web_storage import get_runs_root, list_runs
from ams.web.auth import get_current_user

dashboard_bp = Blueprint("dashboard", __name__)


def _assignment_submission_locked(assignment: Mapping[str, Any] | None) -> bool:
    """Return ``True`` when an assignment no longer accepts submissions."""
    return bool((assignment or {}).get("marks_released"))


def _submission_lock_message() -> str:
    """Return the standard message shown when submissions are locked."""
    return "Grades have already been released for this assignment, so new submissions are locked."


@dashboard_bp.app_context_processor
def inject_sandbox_status() -> dict[str, object]:
    """Expose sandbox status and retained threat containers to templates."""
    from ams.sandbox.config import get_sandbox_status

    ctx: dict[str, object] = {"sandbox_status": get_sandbox_status()}
    try:
        from ams.sandbox.forensics import list_retained_containers

        ctx["threat_containers"] = list_retained_containers()
    except Exception:
        ctx["threat_containers"] = []
    return ctx


@dashboard_bp.app_context_processor
def inject_released_aids() -> dict[str, list[str]]:
    """Expose released assignment IDs so the job widget can gate view links."""
    if session.get("user_role") == "student" and session.get("user_id"):
        aids = [
            a["assignmentID"]
            for a in list_assignments_for_student(session["user_id"])
            if a.get("marks_released")
        ]
        return {"released_assignment_ids": aids}
    return {"released_assignment_ids": []}


@dashboard_bp.route("/")
def home():
    """Redirect authenticated users to the correct dashboard."""
    if "user_id" in session and session.get("2fa_verified"):
        user = get_current_user()
        if user:
            if user["role"] == "admin":
                return redirect(url_for("admin.dashboard"))
            if user["role"] == "teacher":
                return redirect(url_for("teacher_dashboard.dashboard"))
            return redirect(url_for("student.dashboard"))
    return redirect(url_for("auth.login"))


@dashboard_bp.route("/api/jobs/<job_id>")
def job_status(job_id: str):
    """Return the current state of a background job as JSON."""
    status = job_manager.get_job_status(job_id)
    if status is None:
        return jsonify({"error": "Job not found"}), 404
    result = status.get("result")
    if isinstance(result, dict):
        status["result"] = {
            k: str(v) if hasattr(v, "__fspath__") else v
            for k, v in result.items()
        }
    elif hasattr(result, "__fspath__"):
        status["result"] = str(result)
    return jsonify(status)


def _assignment_has_unresolved_release_blockers(assignment_id: str) -> bool:
    """Return whether flagged submissions still block mark release."""
    for run in list_runs(get_runs_root(current_app)):
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
    """Return whether the current teacher/admin may access this assignment."""
    user = get_current_user()
    assignment = get_assignment(assignment_id)
    if user is None or assignment is None:
        return False
    return assignment_allows_teacher(assignment, user["userID"], user["role"])


def _flash_assignment_review_state(assignment_id: str, resolved_message: str) -> None:
    """Flash the outcome of a review-state change for one assignment."""
    flash(resolved_message, "success")
    if _assignment_has_unresolved_release_blockers(assignment_id):
        flash(
            "Other flagged submissions still need attention before grades can be released.",
            "warning",
        )
    else:
        flash("No flagged submissions remain. Grades can now be released.", "success")
