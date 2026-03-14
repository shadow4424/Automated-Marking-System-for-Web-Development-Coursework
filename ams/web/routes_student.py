"""Student blueprint — restricted dashboard showing only this student's submissions."""
from __future__ import annotations

import json
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    render_template,
    session,
)

from ams.core.db import get_assignment, list_assignments_for_student
from ams.io.web_storage import get_runs_root, list_runs
from ams.web.auth import get_current_user, login_required

student_bp = Blueprint("student", __name__, url_prefix="/student")


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _resolve_student_id() -> str | None:
    """Return the current student's user ID, or None if admin view-as."""
    user = get_current_user()
    if user["role"] == "student" or session.get("view_as_role") == "student":
        return user["userID"] if user["role"] == "student" else None
    return None


def _gather_student_runs(student_id: str) -> list[dict]:
    """Collect all runs belonging to *student_id* with ``_marks_released`` flags."""
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root)
    my_runs: list[dict] = []

    for run in all_runs:
        if run.get("mode") == "mark":
            if run.get("student_id") == student_id:
                aid = run.get("assignment_id", "")
                assignment = get_assignment(aid) if aid else None
                run["_marks_released"] = assignment["marks_released"] if assignment else False
                my_runs.append(run)
        elif run.get("mode") == "batch":
            batch_summary = run.get("batch_summary", [])
            if isinstance(batch_summary, list):
                for rec in batch_summary:
                    if rec.get("student_id") == student_id:
                        aid = run.get("assignment_id", "")
                        assignment = get_assignment(aid) if aid else None
                        student_run = dict(run)
                        student_run["_submission_record"] = rec
                        student_run["_marks_released"] = assignment["marks_released"] if assignment else False
                        my_runs.append(student_run)
                        break

    return my_runs


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@student_bp.route("/")
@login_required
def dashboard():
    student_id = _resolve_student_id()

    assignments: list[dict] = []
    my_runs: list[dict] = []
    if student_id:
        assignments = list_assignments_for_student(student_id)
        my_runs = _gather_student_runs(student_id)

    todo = [a for a in assignments if not a["marks_released"]]
    completed = [a for a in assignments if a["marks_released"]]

    return render_template(
        "student_dashboard.html",
        assignments=assignments,
        todo=todo,
        completed=completed,
        my_runs=my_runs,
        recent_runs=my_runs[:3],
        student_id=student_id,
    )


@student_bp.route("/coursework")
@login_required
def coursework():
    student_id = _resolve_student_id()

    assignments: list[dict] = []
    my_runs: list[dict] = []
    if student_id:
        assignments = list_assignments_for_student(student_id)
        my_runs = _gather_student_runs(student_id)

    todo = [a for a in assignments if not a["marks_released"]]
    completed = [a for a in assignments if a["marks_released"]]

    return render_template(
        "student_coursework.html",
        assignments=assignments,
        todo=todo,
        completed=completed,
        my_runs=my_runs,
        student_id=student_id,
    )
