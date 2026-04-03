"""AMS Assignment Store — CRUD operations for assignments."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ams.core.database import _query_one, _query_all, get_db


def _decode_identifier_list(value: Any) -> list[str]:
    """Return identifier list."""
    if isinstance(value, list):
        raw_items = value
    else:
        try:
            raw_items = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            raw_items = []

    normalized: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def assignment_teacher_ids(assignment: dict[str, Any] | None) -> list[str]:
    """Return teacher ids."""
    if not assignment:
        return []

    teacher_ids: list[str] = []
    owner_id = str(assignment.get("teacherID") or "").strip()
    if owner_id:
        teacher_ids.append(owner_id)

    for teacher_id in _decode_identifier_list(assignment.get("assigned_teachers", "[]")):
        if teacher_id not in teacher_ids:
            teacher_ids.append(teacher_id)

    return teacher_ids


def assignment_allows_teacher(
    assignment: dict[str, Any] | None,
    user_id: str,
    role: str | None = None,
) -> bool:
    """Return allows teacher."""
    if not assignment or not user_id:
        return False
    if role == "admin":
        return True
    return user_id in assignment_teacher_ids(assignment)


def _normalize_assignment_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Normalise the assignment record."""
    assignment = dict(row)
    assignment["assigned_students"] = _decode_identifier_list(assignment.get("assigned_students", "[]"))
    assignment["assigned_teachers"] = [
        teacher_id
        for teacher_id in _decode_identifier_list(assignment.get("assigned_teachers", "[]"))
        if teacher_id != str(assignment.get("teacherID") or "").strip()
    ]
    assignment["teacher_ids"] = assignment_teacher_ids(assignment)
    assignment["marks_released"] = bool(assignment.get("marks_released", 0))
    return assignment

def create_assignment(
    assignment_id: str,
    teacher_id: str,
    title: str = "",
    description: str = "",
    profile: str = "frontend",
    assigned_students: list[str] | None = None,
    assigned_teachers: list[str] | None = None,
    due_date: str = "",
) -> bool:
    """Create a new assignment. Returns True on success."""
    extra_teacher_ids = [
        extra_teacher_id
        for extra_teacher_id in _decode_identifier_list(assigned_teachers or [])
        if extra_teacher_id != str(teacher_id or "").strip()
    ]
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO assignments (assignmentID, teacherID, title, description, profile, assigned_students, assigned_teachers, due_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assignment_id,
                teacher_id,
                title,
                description,
                profile,
                json.dumps(assigned_students or []),
                json.dumps(extra_teacher_ids),
                due_date,
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_assignment(assignment_id: str) -> dict | None:
    """Fetch a single assignment by ID."""
    row = _query_one("SELECT * FROM assignments WHERE assignmentID = ?", (assignment_id,))
    if row is None:
        return None
    return _normalize_assignment_record(row)


def list_assignments(teacher_id: str | None = None) -> list[dict]:
    """List all assignments, optionally filtered by teacher."""
    from datetime import datetime

    rows = _query_all("SELECT * FROM assignments")
    result = [_normalize_assignment_record(row) for row in rows]
    if teacher_id:
        result = [assignment for assignment in result if teacher_id in assignment.get("teacher_ids", [])]

    # Sort: active/upcoming first, past-due last, alphanumeric within each group
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")

    def sort_key(a: dict) -> tuple:
        """Return a sort key."""
        due = a.get("due_date", "")
        is_past_due = 1 if (due and due < now) else 0
        return (is_past_due, a.get("assignmentID", ""))

    result.sort(key=sort_key)
    return result


def list_assignments_for_student(student_id: str) -> list[dict]:
    """Return assignments where *student_id* is in the assigned_students list."""
    all_assignments = list_assignments()
    return [a for a in all_assignments if student_id in a.get("assigned_students", [])]


def update_assignment_students(assignment_id: str, student_ids: list[str]) -> bool:
    """Replace the assigned student list for an assignment."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE assignments SET assigned_students = ? WHERE assignmentID = ?",
            (json.dumps(student_ids), assignment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_assignment_teachers(assignment_id: str, teacher_ids: list[str]) -> bool:
    """Replace the additional teacher list for an assignment."""
    assignment = get_assignment(assignment_id)
    if assignment is None:
        return False

    owner_id = str(assignment.get("teacherID") or "").strip()
    extra_teacher_ids = [
        teacher_id
        for teacher_id in _decode_identifier_list(teacher_ids)
        if teacher_id != owner_id
    ]

    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE assignments SET assigned_teachers = ? WHERE assignmentID = ?",
            (json.dumps(extra_teacher_ids), assignment_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def release_marks(assignment_id: str) -> bool:
    """Set marks_released to True for an assignment."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE assignments SET marks_released = 1 WHERE assignmentID = ?",
            (assignment_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def withhold_marks(assignment_id: str) -> bool:
    """Set marks_released back to False (withheld)."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE assignments SET marks_released = 0 WHERE assignmentID = ?",
            (assignment_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_assignment(assignment_id: str) -> bool:
    """Delete an assignment. Returns True if a row was removed."""
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM assignments WHERE assignmentID = ?", (assignment_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
