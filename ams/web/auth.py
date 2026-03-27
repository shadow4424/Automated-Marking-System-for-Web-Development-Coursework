"""AMS Authentication — login, 2FA, session management, and role decorators.

Blueprint ``auth_bp`` provides:
- ``/login``  — credential entry
- ``/2fa``    — two-factor verification
- ``/logout`` — session tear-down

Decorators:
- ``login_required``  — redirect anonymous users to login
- ``role_required(roles)`` — restrict to specified roles
- ``admin_required`` / ``teacher_required`` / ``teacher_or_admin_required``
"""
from __future__ import annotations

import functools
import logging
import random
import string
from datetime import datetime, timezone

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ams.core.db import authenticate_user, get_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# ---------------------------------------------------------------------------
#  2FA helpers
# ---------------------------------------------------------------------------
_2FA_CODE_LENGTH = 6


def _generate_2fa_code() -> str:
    """Return a random numeric code."""
    return "".join(random.choices(string.digits, k=_2FA_CODE_LENGTH))


def _mock_send_email(email: str, code: str) -> None:
    """Print the 2FA code to the console (mock email sender)."""
    print(
        f"\n──────────────────────────────────────────────\n"
        f"  [MOCK EMAIL] To: {email}\n"
        f"  Your AMS 2FA verification code is: {code}\n"
        f"──────────────────────────────────────────────\n",
        flush=True,
    )


# ---------------------------------------------------------------------------
#  Session helpers
# ---------------------------------------------------------------------------

def get_current_user() -> dict | None:
    """Return the full user dict for the logged-in user, or ``None``."""
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_user(uid)


def inject_user_context() -> dict:
    """Context processor — makes ``current_user`` available in every template."""
    user = get_current_user()
    raw_preview_role = session.get("view_as_role")
    effective_role = None
    preview_role = None

    if user:
        if request.blueprint == "admin" and user["role"] == "admin":
            effective_role = "admin"
        else:
            effective_role = raw_preview_role or user["role"]
            if user["role"] == "admin" and raw_preview_role in {"teacher", "student"}:
                preview_role = raw_preview_role

    return {
        "current_user": user,
        "is_authenticated": user is not None,
        "user_role": user["role"] if user else None,
        "effective_role": effective_role,
        "preview_role": preview_role,
        "view_as_role": raw_preview_role,
    }


# ---------------------------------------------------------------------------
#  Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Redirect to login if no active session."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles: str):
    """Restrict access to users whose role is in *roles*."""
    def decorator(f):
        @functools.wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            user = get_current_user()
            if user is None or user["role"] not in roles:
                flash("You do not have permission to access this page.", "error")
                return redirect(url_for("auth.login"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    """Shortcut: admin-only."""
    return role_required("admin")(f)


def teacher_required(f):
    """Shortcut: teacher-only."""
    return role_required("teacher")(f)


def teacher_or_admin_required(f):
    """Shortcut: teacher OR admin."""
    return role_required("teacher", "admin")(f)


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a user and begin the two-factor verification flow."""
    if "user_id" in session and session.get("2fa_verified"):
        return redirect(url_for("dashboard.home"))

    if request.method == "GET":
        return render_template("login.html")

    user_id = request.form.get("user_id", "").strip()
    password = request.form.get("password", "").strip()

    if not user_id or not password:
        flash("Please enter both User ID and Password.", "error")
        return render_template("login.html"), 400

    user = authenticate_user(user_id, password)
    if user is None:
        flash("Invalid credentials. Please try again.", "error")
        return render_template("login.html"), 401

    # Stage 1 passed — generate 2FA code
    code = _generate_2fa_code()
    session["pending_user_id"] = user["userID"]
    session["pending_2fa_code"] = code
    session["pending_2fa_time"] = datetime.now(timezone.utc).isoformat()

    _mock_send_email(user["email"], code)
    flash(f"A verification code has been sent to {user['email']}.", "info")
    return redirect(url_for("auth.verify_2fa"))


@auth_bp.route("/2fa", methods=["GET", "POST"])
def verify_2fa():
    """Validate the 2FA code and establish the authenticated session."""
    if "pending_user_id" not in session:
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("2fa.html")

    entered_code = request.form.get("code", "").strip()
    expected_code = session.get("pending_2fa_code", "")

    if entered_code != expected_code:
        flash("Incorrect verification code. Please try again.", "error")
        return render_template("2fa.html"), 401

    # 2FA passed — create authenticated session
    user_id = session.pop("pending_user_id")
    session.pop("pending_2fa_code", None)
    session.pop("pending_2fa_time", None)

    user = get_user(user_id)
    session["user_id"] = user_id
    session["user_role"] = user["role"]
    session["2fa_verified"] = True

    flash(f"Welcome, {user['firstName']}!", "success")

    # Redirect based on role
    next_url = request.args.get("next")
    if next_url:
        return redirect(next_url)
    if user["role"] == "admin":
        return redirect(url_for("admin.dashboard"))
    elif user["role"] == "teacher":
        return redirect(url_for("teacher.dashboard"))
    else:
        return redirect(url_for("student.dashboard"))


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Clear the current session and return the user to the login page."""
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
