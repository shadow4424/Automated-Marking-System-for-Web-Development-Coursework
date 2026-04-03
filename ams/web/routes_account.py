"""Account Settings — self-service email and password updates for authenticated users."""
from __future__ import annotations

import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from ams.core.user_store import (
    get_user,
    get_user_by_email,
    update_user_email,
    update_user_password,
)
from ams.web.auth import login_required

account_bp = Blueprint("account", __name__, url_prefix="/account")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LENGTH = 8


@account_bp.route("/settings", methods=["GET"])
@login_required
# Show the account settings page.
def settings():
    user = get_user(session["user_id"])
    return render_template("account_settings.html", user=user)


@account_bp.route("/settings/email", methods=["POST"])
@login_required
# Update the current user's email address.
def update_email():
    user = get_user(session["user_id"])
    new_email = request.form.get("email", "").strip()
    current_password = request.form.get("current_password", "").strip()

    if not new_email or not current_password:
        flash("All fields are required.", "error")
        return redirect(url_for("account.settings"))

    if not _EMAIL_RE.match(new_email):
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("account.settings"))

    if not check_password_hash(user["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("account.settings"))

    # Uniqueness check — allow keeping the same address
    if new_email != user["email"]:
        existing = get_user_by_email(new_email)
        if existing is not None:
            flash("That email address is already in use.", "error")
            return redirect(url_for("account.settings"))

    update_user_email(user["userID"], new_email)
    flash("Email updated successfully.", "success")
    return redirect(url_for("account.settings"))


@account_bp.route("/settings/password", methods=["POST"])
@login_required
# Update the current user's password.
def update_password():
    user = get_user(session["user_id"])
    current_password = request.form.get("current_password", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if not all([current_password, new_password, confirm_password]):
        flash("All password fields are required.", "error")
        return redirect(url_for("account.settings"))

    if not check_password_hash(user["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("account.settings"))

    if new_password != confirm_password:
        flash("New passwords do not match.", "error")
        return redirect(url_for("account.settings"))

    if len(new_password) < _MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters.", "error")
        return redirect(url_for("account.settings"))

    update_user_password(user["userID"], new_password)
    flash("Password updated successfully.", "success")
    return redirect(url_for("account.settings"))
