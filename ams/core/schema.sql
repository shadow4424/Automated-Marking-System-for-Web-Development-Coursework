-- AMS Database Schema (SQLite)
-- Single source of truth for table definitions and indexes.
-- Loaded at runtime by ams/core/db.py via init_db().
-- Seed data (root admin, preview student) is inserted by init_db() using
-- werkzeug-hashed passwords and therefore lives in Python, not here.

-- ------------------------------------------------------------
-- Tables
-- ------------------------------------------------------------

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
    assignmentID      TEXT PRIMARY KEY,
    teacherID         TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    profile           TEXT NOT NULL DEFAULT 'frontend',
    marks_released    INTEGER NOT NULL DEFAULT 0,
    assigned_students TEXT NOT NULL DEFAULT '[]',
    assigned_teachers TEXT NOT NULL DEFAULT '[]',
    due_date          TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (teacherID) REFERENCES users(userID)
);

CREATE TABLE IF NOT EXISTS submission_attempts (
    id                     TEXT PRIMARY KEY,
    assignment_id          TEXT NOT NULL,
    student_id             TEXT NOT NULL,
    attempt_number         INTEGER NOT NULL,
    source_type            TEXT NOT NULL DEFAULT '',
    source_actor_user_id   TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at           TEXT NOT NULL DEFAULT '',
    original_filename      TEXT NOT NULL DEFAULT '',
    source_ref             TEXT NOT NULL DEFAULT '',
    ingestion_status       TEXT NOT NULL DEFAULT 'pending',
    pipeline_status        TEXT NOT NULL DEFAULT 'pending',
    validity_status        TEXT NOT NULL DEFAULT 'pending',
    run_id                 TEXT NOT NULL DEFAULT '',
    run_dir                TEXT NOT NULL DEFAULT '',
    report_path            TEXT NOT NULL DEFAULT '',
    batch_run_id           TEXT NOT NULL DEFAULT '',
    batch_submission_id    TEXT NOT NULL DEFAULT '',
    overall_score          REAL,
    confidence             TEXT NOT NULL DEFAULT '',
    manual_review_required INTEGER NOT NULL DEFAULT 0,
    error_message          TEXT NOT NULL DEFAULT '',
    is_active              INTEGER NOT NULL DEFAULT 0,
    selection_reason       TEXT NOT NULL DEFAULT '',
    updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (assignment_id, student_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS student_assignment_summary (
    assignment_id     TEXT NOT NULL,
    student_id        TEXT NOT NULL,
    latest_attempt_id TEXT NOT NULL DEFAULT '',
    active_attempt_id TEXT NOT NULL DEFAULT '',
    selection_reason  TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (assignment_id, student_id)
);

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_submission_attempts_identity
    ON submission_attempts(assignment_id, student_id, attempt_number DESC);

-- Superseded index — drop if present from older databases.
DROP INDEX IF EXISTS idx_submission_attempts_run_ref;

CREATE INDEX IF NOT EXISTS idx_submission_attempts_run_ref_lookup
    ON submission_attempts(run_id, batch_submission_id);

CREATE INDEX IF NOT EXISTS idx_submission_attempts_active
    ON submission_attempts(assignment_id, is_active);
