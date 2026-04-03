"""Admin blueprint — dashboard, account creation, user management, view toggle."""
from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ams.core.user_store import create_user, delete_user, list_users
from ams.core.assignment_store import list_assignments
from ams.web.auth import admin_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@admin_required
# Show the admin dashboard.
def dashboard():
    users = list_users()
    teachers = [u for u in users if u["role"] == "teacher"]
    students = [u for u in users if u["role"] == "student"]
    admins = [u for u in users if u["role"] == "admin"]
    assignments = list_assignments()
    return render_template(
        "admin/dashboard.html",
        users=users,
        teachers=teachers,
        students=students,
        admins=admins,
        assignments=assignments,
    )


@admin_bp.route("/create-account")
@admin_required
# Show the teacher account creation page.
def create_account_page():
    return render_template("admin/create_account.html")


@admin_bp.route("/users")
@admin_required
# Show the user management page.
def users_page():
    users = list_users()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/assignments")
@admin_required
# Show the assignment management page.
def assignments_page():
    assignments = list_assignments()
    teaching_team_ids = set()
    for assignment in assignments:
        for teacher_id in assignment.get("teacher_ids") or [assignment.get("teacherID")]:
            if teacher_id:
                teaching_team_ids.add(teacher_id)
    teacher_count = len(teaching_team_ids)
    return render_template("admin/assignments.html", assignments=assignments, teacher_count=teacher_count)


@admin_bp.route("/create-teacher", methods=["POST"])
@admin_required
# Create a teacher account.
def create_teacher():
    user_id = request.form.get("user_id", "").strip()
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not all([user_id, first_name, last_name, email, password]):
        flash("All fields are required.", "error")
        return redirect(url_for("admin.create_account_page"))

    ok = create_user(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
        role="teacher",
    )
    if ok:
        flash(f"Teacher account '{user_id}' created successfully.", "success")
    else:
        flash(f"User ID '{user_id}' already exists.", "error")
    return redirect(url_for("admin.create_account_page"))


@admin_bp.route("/create-student", methods=["POST"])
@admin_required
# Create a student account.
def create_student():
    user_id = request.form.get("user_id", "").strip()
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not all([user_id, first_name, last_name, email, password]):
        flash("All fields are required.", "error")
        return redirect(url_for("admin.create_account_page"))

    ok = create_user(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=password,
        role="student",
    )
    if ok:
        flash(f"Student account '{user_id}' created successfully.", "success")
    else:
        flash(f"User ID '{user_id}' already exists.", "error")
    return redirect(url_for("admin.create_account_page"))


@admin_bp.route("/delete-user/<user_id>", methods=["POST"])
@admin_required
# Remove a user account.
def remove_user(user_id: str):
    if delete_user(user_id):
        flash(f"User '{user_id}' deleted.", "success")
    else:
        flash(f"Cannot delete user '{user_id}'.", "error")
    return redirect(url_for("admin.users_page"))


@admin_bp.route("/view-as/<role>")
@admin_required
# Switch into a temporary view-as session.
def view_as(role: str):
    """Let the admin toggle their view to see the teacher or student dashboard."""
    if role not in ("admin", "teacher", "student"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin.dashboard"))

    if role == "admin":
        session.pop("view_as_role", None)
        return redirect(url_for("admin.dashboard"))

    session["view_as_role"] = role
    if role == "teacher":
        return redirect(url_for("teacher_dashboard.dashboard"))
    return redirect(url_for("student.dashboard"))
