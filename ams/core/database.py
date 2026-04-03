"""AMS Database Layer — SQLite schema, connection helpers, and initialisation."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from werkzeug.security import generate_password_hash

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


# Schema

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
    """Return columns."""
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_sql: str,
) -> None:
    """Ensure the column."""
    if column_name in _table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


# Connection helpers


def _db_path() -> Path:
    """Return path."""
    return _DEFAULT_DB_PATH


def _query_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    """Execute a query and return a single row, or None."""
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _query_all(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Execute a query and return all matching rows."""
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection with row-factory enabled."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Initialisation


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
