"""AMS User Store — CRUD operations for user accounts."""
from __future__ import annotations

import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

from ams.core.database import (
    PREVIEW_STUDENT_ID,
    _query_one,
    _query_all,
    get_db,
    _ROOT_ADMIN_ID,
)


def authenticate_user(user_id: str, password: str) -> dict | None:
    """Verify credentials. Returns user dict on success, None on failure."""
    row = _query_one("SELECT * FROM users WHERE userID = ?", (user_id,))
    if row is None:
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    return dict(row)


def get_user(user_id: str) -> dict | None:
    """Fetch a single user by ID."""
    row = _query_one("SELECT * FROM users WHERE userID = ?", (user_id,))
    return dict(row) if row else None


def get_preview_student() -> dict | None:
    """Return dedicated preview student account for admin view-as mode."""
    return get_user(PREVIEW_STUDENT_ID)


def list_users(role: str | None = None) -> list[dict]:
    """Return all users, optionally filtered by role. Excludes system accounts (preview student) from listings."""
    if role:
        rows = _query_all(
            "SELECT * FROM users WHERE role = ? AND userID != ? ORDER BY userID",
            (role, PREVIEW_STUDENT_ID),
        )
    else:
        rows = _query_all(
            "SELECT * FROM users WHERE userID != ? ORDER BY role, userID",
            (PREVIEW_STUDENT_ID,),
        )
    return [dict(row) for row in rows]


def create_user(
    user_id: str,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
    role: str = "student",
) -> bool:
    """Insert a new user. Returns True on success, False if the ID already exists."""
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
    """Delete a user. Returns True if a row was removed."""
    if user_id == _ROOT_ADMIN_ID:
        return False  # Protect root admin
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM users WHERE userID = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict | None:
    """Return first user with the given email, or None."""
    row = _query_one("SELECT * FROM users WHERE email = ?", (email,))
    return dict(row) if row else None


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
