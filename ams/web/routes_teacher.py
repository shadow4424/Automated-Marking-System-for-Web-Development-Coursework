"""Teacher blueprint — dashboard, assignment creation, student allocation, mark release."""
from __future__ import annotations

import json
import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ams.core.db import (
    create_assignment,
    delete_assignment,
    get_assignment,
    get_user,
    list_assignments,
    list_users,
    release_marks,
    update_assignment_students,
    withhold_marks,
)
from ams.io.web_storage import get_runs_root, list_runs
from ams.web.auth import get_current_user, teacher_or_admin_required
from ams.analytics import generate_assignment_analytics

logger = logging.getLogger(__name__)

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")


@teacher_bp.route("/")
@teacher_or_admin_required
def dashboard():
    user = get_current_user()
    # Admin in view-as mode sees all; teachers see their own
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
        return render_template("teacher_create_assignment.html", students=students)

    user = get_current_user()
    assignment_id = request.form.get("assignment_id", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    profile = request.form.get("profile", "frontend").strip()
    due_date = request.form.get("due_date", "").strip()
    selected_students = request.form.getlist("students")

    if not assignment_id or not title:
        flash("Assignment ID and Title are required.", "error")
        return redirect(url_for("teacher.dashboard"))

    teacher_id = user["userID"]
    ok = create_assignment(
        assignment_id=assignment_id,
        teacher_id=teacher_id,
        title=title,
        description=description,
        profile=profile,
        assigned_students=selected_students,
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
    selected = request.form.getlist("students")
    update_assignment_students(assignment_id, selected)
    flash(f"Student list updated for '{assignment_id}'.", "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>")
@teacher_or_admin_required
def assignment_detail(assignment_id: str):
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))

    # Resolve student names
    student_details = []
    for sid in assignment.get("assigned_students", []):
        u = get_user(sid)
        if u:
            student_details.append(u)
        else:
            student_details.append({"userID": sid, "firstName": sid, "lastName": "", "email": ""})

    # All students (for the edit form)
    all_students = list_users(role="student")

    # Gather runs that belong to this assignment
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root)
    assignment_runs_raw = [
        r for r in all_runs
        if r.get("assignment_id") == assignment_id
    ]

    # Expand batch runs into individual per-student rows
    assignment_runs = []
    for r in assignment_runs_raw:
        if r.get("mode") == "batch" and r.get("submissions"):
            # Create one row per individual submission in this batch
            for sub in r.get("submissions", []):
                sub_row = dict(r)  # shallow copy of batch run metadata
                sub_row["student_id"] = sub.get("student_id") or sub.get("student_name") or "Unknown"
                sub_row["_batch_submission_id"] = sub.get("submission_id") or sub.get("student_id") or sub.get("student_name")
                # Use individual score if available, otherwise keep batch average
                assignment_runs.append(sub_row)
        else:
            assignment_runs.append(r)

    return render_template(
        "assignment_detail.html",
        assignment=assignment,
        student_details=student_details,
        all_students=all_students,
        runs=assignment_runs,
    )


@teacher_bp.route("/assignment/<assignment_id>/release", methods=["POST"])
@teacher_or_admin_required
def release(assignment_id: str):
    release_marks(assignment_id)
    flash(f"Marks released for '{assignment_id}'.", "success")
    return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))


@teacher_bp.route("/assignment/<assignment_id>/withhold", methods=["POST"])
@teacher_or_admin_required
def withhold(assignment_id: str):
    withhold_marks(assignment_id)
    flash(f"Marks withheld for '{assignment_id}'.", "info")
    return redirect(url_for("teacher.dashboard"))


@teacher_bp.route("/assignment/<assignment_id>/analytics")
@teacher_or_admin_required
def view_analytics(assignment_id: str):
    """Auto-generate and display analytics for an assignment."""
    assignment = get_assignment(assignment_id)
    if assignment is None:
        flash("Assignment not found.", "error")
        return redirect(url_for("teacher.dashboard"))

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
        _save_analytics(assignment_id, analytics)
    except Exception as exc:
        logger.warning("Analytics generation failed for %s: %s", assignment_id, exc)
        flash(f"Analytics generation failed: {exc}", "error")
        return redirect(url_for("teacher.assignment_detail", assignment_id=assignment_id))

    # Count total submissions for this assignment
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root)
    total_submissions = len([r for r in all_runs if r.get("assignment_id") == assignment_id])

    return render_template(
        "assignment_analytics.html",
        assignment=assignment,
        analytics=analytics,
        total_submissions=total_submissions,
    )


@teacher_bp.route("/assignment/<assignment_id>/delete", methods=["POST"])
@teacher_or_admin_required
def delete_assignment_route(assignment_id: str):
    if delete_assignment(assignment_id):
        # Clean up analytics file if present
        try:
            path = _analytics_path(assignment_id)
            if path.exists():
                path.unlink()
        except Exception:
            pass
        flash(f"Assignment '{assignment_id}' deleted.", "success")
    else:
        flash(f"Could not delete assignment '{assignment_id}'.", "error")
    return redirect(url_for("teacher.dashboard"))


# ── Helpers ─────────────────────────────────────────────────────────

from pathlib import Path

_ANALYTICS_DIR = Path(__file__).resolve().parent.parent / "ams_analytics"


def _analytics_path(assignment_id: str) -> Path:
    """Return the filesystem path for an assignment's analytics JSON."""
    _ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in assignment_id)
    return _ANALYTICS_DIR / f"{safe_id}_analytics.json"


def _save_analytics(assignment_id: str, analytics: dict) -> None:
    """Persist analytics dict to disk."""
    path = _analytics_path(assignment_id)
    path.write_text(json.dumps(analytics, indent=2), encoding="utf-8")
