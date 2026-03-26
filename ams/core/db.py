"""AMS Database Layer — SQLite-backed user and assignment management.

Provides:
- Schema initialisation for ``Users`` and ``Assignments`` tables.
- Auto-provisioning of the root admin account (``admin123`` / ``Pass123``).
- Thread-safe connection management via context manager.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

# Default database path — stored in project root (not inside package)
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "ams_users.db"

# Root admin defaults
_ROOT_ADMIN_ID = "admin123"
_ROOT_ADMIN_PASSWORD = "Pass123"
_ROOT_ADMIN_EMAIL = "admin@ams.local"

# Preview/demo student for admin view-as mode (not a real student)
PREVIEW_STUDENT_ID = "_preview_student_"
_PREVIEW_STUDENT_EMAIL = "preview@ams.local"

# ---------------------------------------------------------------------------
#  Schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    userID        TEXT PRIMARY KEY,
    firstName     TEXT NOT NULL DEFAULT '',
    lastName      TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'student'
        CHECK(role IN ('admin', 'teacher', 'student'))
);

CREATE TABLE IF NOT EXISTS assignments (
    assignmentID         TEXT PRIMARY KEY,
    teacherID            TEXT NOT NULL,
    title                TEXT NOT NULL DEFAULT '',
    description          TEXT NOT NULL DEFAULT '',
    profile              TEXT NOT NULL DEFAULT 'frontend',
    marks_released       INTEGER NOT NULL DEFAULT 0,
    assigned_students    TEXT NOT NULL DEFAULT '[]',
    assigned_teachers    TEXT NOT NULL DEFAULT '[]',
    due_date             TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (teacherID) REFERENCES users(userID)
);

CREATE TABLE IF NOT EXISTS submission_attempts (
    id                    TEXT PRIMARY KEY,
    assignment_id         TEXT NOT NULL,
    student_id            TEXT NOT NULL,
    attempt_number        INTEGER NOT NULL,
    source_type           TEXT NOT NULL DEFAULT '',
    source_actor_user_id  TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at          TEXT NOT NULL DEFAULT '',
    original_filename     TEXT NOT NULL DEFAULT '',
    source_ref            TEXT NOT NULL DEFAULT '',
    ingestion_status      TEXT NOT NULL DEFAULT 'pending',
    pipeline_status       TEXT NOT NULL DEFAULT 'pending',
    validity_status       TEXT NOT NULL DEFAULT 'pending',
    run_id                TEXT NOT NULL DEFAULT '',
    run_dir               TEXT NOT NULL DEFAULT '',
    report_path           TEXT NOT NULL DEFAULT '',
    batch_run_id          TEXT NOT NULL DEFAULT '',
    batch_submission_id   TEXT NOT NULL DEFAULT '',
    overall_score         REAL,
    confidence            TEXT NOT NULL DEFAULT '',
    manual_review_required INTEGER NOT NULL DEFAULT 0,
    error_message         TEXT NOT NULL DEFAULT '',
    is_active             INTEGER NOT NULL DEFAULT 0,
    selection_reason      TEXT NOT NULL DEFAULT '',
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (assignment_id, student_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS student_assignment_summary (
    assignment_id      TEXT NOT NULL,
    student_id         TEXT NOT NULL,
    latest_attempt_id  TEXT NOT NULL DEFAULT '',
    active_attempt_id  TEXT NOT NULL DEFAULT '',
    selection_reason   TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (assignment_id, student_id)
);
"""


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


# ---------------------------------------------------------------------------
#  Connection helpers
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return _DEFAULT_DB_PATH


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with row-factory enabled."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
#  Initialisation
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables if they don't exist and ensure system accounts are present."""
    conn = get_db()
    try:
        conn.executescript(_SCHEMA_SQL)

        # Migrate: add due_date column if missing (existing DBs)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(assignments)").fetchall()]
        if "due_date" not in cols:
            conn.execute("ALTER TABLE assignments ADD COLUMN due_date TEXT NOT NULL DEFAULT ''")
        if "assigned_teachers" not in cols:
            conn.execute("ALTER TABLE assignments ADD COLUMN assigned_teachers TEXT NOT NULL DEFAULT '[]'")

        for column_name, column_sql in (
            ("source_type", "TEXT NOT NULL DEFAULT ''"),
            ("source_actor_user_id", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("submitted_at", "TEXT NOT NULL DEFAULT ''"),
            ("original_filename", "TEXT NOT NULL DEFAULT ''"),
            ("source_ref", "TEXT NOT NULL DEFAULT ''"),
            ("ingestion_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("pipeline_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("validity_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("run_id", "TEXT NOT NULL DEFAULT ''"),
            ("run_dir", "TEXT NOT NULL DEFAULT ''"),
            ("report_path", "TEXT NOT NULL DEFAULT ''"),
            ("batch_run_id", "TEXT NOT NULL DEFAULT ''"),
            ("batch_submission_id", "TEXT NOT NULL DEFAULT ''"),
            ("overall_score", "REAL"),
            ("confidence", "TEXT NOT NULL DEFAULT ''"),
            ("manual_review_required", "INTEGER NOT NULL DEFAULT 0"),
            ("error_message", "TEXT NOT NULL DEFAULT ''"),
            ("is_active", "INTEGER NOT NULL DEFAULT 0"),
            ("selection_reason", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, "submission_attempts", column_name, column_sql)

        for column_name, column_sql in (
            ("latest_attempt_id", "TEXT NOT NULL DEFAULT ''"),
            ("active_attempt_id", "TEXT NOT NULL DEFAULT ''"),
            ("selection_reason", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, "student_assignment_summary", column_name, column_sql)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_attempts_identity "
            "ON submission_attempts(assignment_id, student_id, attempt_number DESC)"
        )
        conn.execute("DROP INDEX IF EXISTS idx_submission_attempts_run_ref")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_attempts_run_ref_lookup "
            "ON submission_attempts(run_id, batch_submission_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submission_attempts_active "
            "ON submission_attempts(assignment_id, is_active)"
        )

        # Provision root admin when missing
        row = conn.execute(
            "SELECT userID FROM users WHERE userID = ?", (_ROOT_ADMIN_ID,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (userID, firstName, lastName, email, password_hash, role) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _ROOT_ADMIN_ID,
                    "System",
                    "Admin",
                    _ROOT_ADMIN_EMAIL,
                    generate_password_hash(_ROOT_ADMIN_PASSWORD),
                    "admin",
                ),
            )
            logger.info("Root admin account provisioned (%s).", _ROOT_ADMIN_ID)

        # Provision preview student for admin view-as mode
        row = conn.execute(
            "SELECT userID FROM users WHERE userID = ?", (PREVIEW_STUDENT_ID,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (userID, firstName, lastName, email, password_hash, role) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    PREVIEW_STUDENT_ID,
                    "Preview",
                    "Student",
                    _PREVIEW_STUDENT_EMAIL,
                    generate_password_hash(""),  # No login allowed
                    "student",
                ),
            )
            logger.info("Preview student account provisioned (%s).", PREVIEW_STUDENT_ID)

        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  User CRUD
# ---------------------------------------------------------------------------

def authenticate_user(user_id: str, password: str) -> dict | None:
    """Verify credentials. Returns user dict on success, ``None`` on failure."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE userID = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        if not check_password_hash(row["password_hash"], password):
            return None
        return dict(row)
    finally:
        conn.close()


def get_user(user_id: str) -> dict | None:
    """Fetch a single user by ID."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE userID = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_preview_student() -> dict | None:
    """Return the dedicated preview student account for admin view-as mode."""
    return get_user(PREVIEW_STUDENT_ID)


def list_users(role: str | None = None) -> list[dict]:
    """Return all users, optionally filtered by role.

    Excludes system accounts (preview student) from listings.
    """
    conn = get_db()
    try:
        if role:
            rows = conn.execute(
                "SELECT * FROM users WHERE role = ? AND userID != ? ORDER BY userID",
                (role, PREVIEW_STUDENT_ID),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE userID != ? ORDER BY role, userID",
                (PREVIEW_STUDENT_ID,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(
    user_id: str,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    role: str = "student",
) -> bool:
    """Insert a new user. Returns ``True`` on success, ``False`` if the ID already exists."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (userID, firstName, lastName, email, password_hash, role) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, first_name, last_name, email, generate_password_hash(password), role),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_user(user_id: str) -> bool:
    """Delete a user. Returns ``True`` if a row was removed."""
    if user_id == _ROOT_ADMIN_ID:
        return False  # protect root admin
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM users WHERE userID = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    """Return the first user with the given email, or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_user_email(user_id: str, email: str) -> None:
    """Update email for *user_id*. Only the email column is touched (whitelist)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET email = ? WHERE userID = ?",
            (email, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_password(user_id: str, password: str) -> None:
    """Hash *password* and store it for *user_id*. Only password_hash is touched (whitelist)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE userID = ?",
            (generate_password_hash(password), user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  Assignment CRUD
# ---------------------------------------------------------------------------

def _decode_identifier_list(value: Any) -> list[str]:
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
    if not assignment or not user_id:
        return False
    if role == "admin":
        return True
    return user_id in assignment_teacher_ids(assignment)


def _normalize_assignment_record(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
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
    """Create a new assignment. Returns ``True`` on success."""
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
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM assignments WHERE assignmentID = ?", (assignment_id,)
        ).fetchone()
        if row is None:
            return None
        return _normalize_assignment_record(row)
    finally:
        conn.close()


def list_assignments(teacher_id: str | None = None) -> list[dict]:
    """List all assignments, optionally filtered by teacher.

    Sorting: active/upcoming assignments first (alphanumeric by ID),
    then past-due assignments at the bottom (also alphanumeric by ID).
    """
    from datetime import datetime

    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM assignments").fetchall()
        result = [_normalize_assignment_record(row) for row in rows]
        if teacher_id:
            result = [assignment for assignment in result if teacher_id in assignment.get("teacher_ids", [])]

        # Sort: active/upcoming first, past-due last, alphanumeric within each group
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")

        def sort_key(a: dict) -> tuple:
            due = a.get("due_date", "")
            is_past_due = 1 if (due and due < now) else 0
            return (is_past_due, a.get("assignmentID", ""))

        result.sort(key=sort_key)
        return result
    finally:
        conn.close()


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
    """Delete an assignment. Returns ``True`` if a row was removed."""
    conn = get_db()
    try:
        cur = conn.execute(
            "DELETE FROM assignments WHERE assignmentID = ?", (assignment_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
