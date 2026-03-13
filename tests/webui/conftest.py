"""Shared fixtures for webui tests — provides an authenticated test client."""
from __future__ import annotations

from flask.testing import FlaskClient


def authenticate_client(client: FlaskClient, role: str = "admin") -> None:
    """Inject a fully-authenticated session into the Flask test client.

    Sets the session keys that ``login_required`` and ``role_required``
    decorators check, bypassing the actual login/2FA flow.
    """
    with client.session_transaction() as sess:
        if role == "admin":
            sess["user_id"] = "admin123"
        else:
            sess["user_id"] = f"_test_{role}"
        sess["user_role"] = role
        sess["2fa_verified"] = True
