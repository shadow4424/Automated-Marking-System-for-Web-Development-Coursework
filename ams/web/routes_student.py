"""Student blueprint — restricted dashboard showing only this student's submissions."""
from __future__ import annotations

import json
from datetime import datetime
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
                        if aid:
                            submitted_aids.add(aid)
                        break

    return my_runs, submitted_aids


def _split_assignments(
    assignments: list[dict], submitted_aids: set[str],
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
        due = a.get("due_date", "")
        if due and due < now:
            completed.append(a)
        else:
            todo.append(a)

    return todo, completed


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@student_bp.route("/")
@login_required
def dashboard():
    student_id = _resolve_student_id()

    assignments: list[dict] = []
    my_runs: list[dict] = []
    submitted_aids: set[str] = set()
    if student_id:
        assignments = list_assignments_for_student(student_id)
        my_runs, submitted_aids = _gather_student_runs(student_id)

    todo, completed = _split_assignments(assignments, submitted_aids)

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
    submitted_aids: set[str] = set()
    if student_id:
        assignments = list_assignments_for_student(student_id)
        my_runs, submitted_aids = _gather_student_runs(student_id)

    todo, completed = _split_assignments(assignments, submitted_aids)

    return render_template(
        "student_coursework.html",
        assignments=assignments,
        todo=todo,
        completed=completed,
        my_runs=my_runs,
        student_id=student_id,
    )
