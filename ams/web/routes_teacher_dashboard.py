"""Teacher dashboard routes."""
from __future__ import annotations

from flask import Blueprint, render_template

from ams.core.db import list_assignments, list_users
from ams.web.auth import get_current_user, teacher_or_admin_required

teacher_dashboard_bp = Blueprint("teacher_dashboard", __name__, url_prefix="/teacher")


@teacher_dashboard_bp.route("/")
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
