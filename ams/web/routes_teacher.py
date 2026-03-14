"""Teacher blueprint — dashboard, assignment creation, student allocation, mark release."""
from __future__ import annotations

import json

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
    assignment_runs = [
        r for r in all_runs
        if r.get("assignment_id") == assignment_id
    ]

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
    return redirect(url_for("teacher.dashboard"))


@teacher_bp.route("/assignment/<assignment_id>/withhold", methods=["POST"])
@teacher_or_admin_required
def withhold(assignment_id: str):
    withhold_marks(assignment_id)
    flash(f"Marks withheld for '{assignment_id}'.", "info")
    return redirect(url_for("teacher.dashboard"))


@teacher_bp.route("/assignment/<assignment_id>/delete", methods=["POST"])
@teacher_or_admin_required
def delete_assignment_route(assignment_id: str):
    if delete_assignment(assignment_id):
        flash(f"Assignment '{assignment_id}' deleted.", "success")
    else:
        flash(f"Could not delete assignment '{assignment_id}'.", "error")
    return redirect(url_for("teacher.dashboard"))
