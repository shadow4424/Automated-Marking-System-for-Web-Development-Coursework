"""Tests for the Account Settings feature (/account/settings)."""
from __future__ import annotations

from pathlib import Path

from werkzeug.security import check_password_hash

from ams.core.db import create_user, get_user, init_db
from ams.webui import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _use_temp_db(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "ams_users.db"
    monkeypatch.setattr("ams.core.db._DEFAULT_DB_PATH", db_path)
    init_db()


def _make_client(
    tmp_path: Path,
    monkeypatch,
    user_id: str = "testuser",
    password: str = "Pass1234!",
    role: str = "student",
    email: str = "test@example.com",
    setup_db: bool = True,
):
    if setup_db:
        _use_temp_db(monkeypatch, tmp_path)
    create_user(user_id, "Test", "User", email, password, role)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["user_role"] = role
        sess["2fa_verified"] = True
    return client


# ---------------------------------------------------------------------------
# GET /account/settings
# ---------------------------------------------------------------------------

def test_settings_page_loads(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.get("/account/settings")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "testuser" in body
    assert "Test" in body
    assert "User" in body
    assert "test@example.com" in body


def test_settings_page_unauthenticated(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    response = client.get("/account/settings")
    assert response.status_code == 302
    assert "login" in response.headers["Location"]


def test_readonly_fields_in_template(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.get("/account/settings")
    body = response.get_data(as_text=True)
    # The identity panel uses info-item blocks (not editable inputs)
    assert "info-item" in body
    # userID, firstName, lastName appear on the page
    assert "testuser" in body
    assert "Test" in body
    assert "User" in body
    # No input field with name="userID" (i.e. not a form field)
    assert 'name="userID"' not in body
    assert 'name="firstName"' not in body
    assert 'name="lastName"' not in body


# ---------------------------------------------------------------------------
# POST /account/settings/email
# ---------------------------------------------------------------------------

def test_update_email_success(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/email",
        data={"email": "new@example.com", "current_password": "Pass1234!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Email updated successfully" in response.get_data(as_text=True)
    user = get_user("testuser")
    assert user["email"] == "new@example.com"


def test_update_email_invalid_format(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/email",
        data={"email": "not-an-email", "current_password": "Pass1234!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "valid email" in response.get_data(as_text=True)
    # Email unchanged
    assert get_user("testuser")["email"] == "test@example.com"


def test_update_email_wrong_password(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/email",
        data={"email": "new@example.com", "current_password": "WrongPass"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "incorrect" in response.get_data(as_text=True).lower()
    assert get_user("testuser")["email"] == "test@example.com"


def test_update_email_duplicate(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path)
    create_user("user1", "User", "One", "user1@example.com", "Pass1234!", "student")
    create_user("user2", "User", "Two", "user2@example.com", "Pass1234!", "student")
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user1"
        sess["user_role"] = "student"
        sess["2fa_verified"] = True
    response = client.post(
        "/account/settings/email",
        data={"email": "user2@example.com", "current_password": "Pass1234!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "already in use" in response.get_data(as_text=True)
    assert get_user("user1")["email"] == "user1@example.com"


def test_update_email_missing_fields(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/email",
        data={"email": "", "current_password": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "required" in response.get_data(as_text=True).lower()


def test_update_email_same_email_allowed(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/email",
        data={"email": "test@example.com", "current_password": "Pass1234!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Email updated successfully" in response.get_data(as_text=True)


def test_settings_page_unauthenticated_post_email(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    response = client.post(
        "/account/settings/email",
        data={"email": "x@x.com", "current_password": "pass"},
    )
    assert response.status_code == 302
    assert "login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# POST /account/settings/password
# ---------------------------------------------------------------------------

def test_update_password_success(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/password",
        data={
            "current_password": "Pass1234!",
            "new_password": "NewSecure456!",
            "confirm_password": "NewSecure456!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Password updated successfully" in response.get_data(as_text=True)
    # Old password should no longer work
    user = get_user("testuser")
    assert not check_password_hash(user["password_hash"], "Pass1234!")
    assert check_password_hash(user["password_hash"], "NewSecure456!")


def test_update_password_wrong_current(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/password",
        data={
            "current_password": "WrongPass",
            "new_password": "NewSecure456!",
            "confirm_password": "NewSecure456!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "incorrect" in response.get_data(as_text=True).lower()
    # Password unchanged
    user = get_user("testuser")
    assert check_password_hash(user["password_hash"], "Pass1234!")


def test_update_password_mismatch(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/password",
        data={
            "current_password": "Pass1234!",
            "new_password": "NewSecure456!",
            "confirm_password": "DifferentPass789!",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "do not match" in response.get_data(as_text=True).lower()


def test_update_password_too_short(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/password",
        data={
            "current_password": "Pass1234!",
            "new_password": "short",
            "confirm_password": "short",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "at least" in response.get_data(as_text=True).lower()


def test_update_password_missing_fields(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/account/settings/password",
        data={"current_password": "", "new_password": "", "confirm_password": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "required" in response.get_data(as_text=True).lower()


def test_settings_page_unauthenticated_post_password(tmp_path, monkeypatch):
    _use_temp_db(monkeypatch, tmp_path)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    response = client.post(
        "/account/settings/password",
        data={"current_password": "x", "new_password": "y", "confirm_password": "y"},
    )
    assert response.status_code == 302
    assert "login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# Protected fields — backend enforcement
# ---------------------------------------------------------------------------

def test_protected_fields_unchanged_after_email_update(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    client.post(
        "/account/settings/email",
        data={"email": "new@example.com", "current_password": "Pass1234!"},
    )
    user = get_user("testuser")
    assert user["userID"] == "testuser"
    assert user["firstName"] == "Test"
    assert user["lastName"] == "User"


def test_protected_fields_unchanged_after_password_update(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    client.post(
        "/account/settings/password",
        data={
            "current_password": "Pass1234!",
            "new_password": "NewSecure456!",
            "confirm_password": "NewSecure456!",
        },
    )
    user = get_user("testuser")
    assert user["userID"] == "testuser"
    assert user["firstName"] == "Test"
    assert user["lastName"] == "User"


def test_another_users_account_not_affected(tmp_path, monkeypatch):
    """Session user cannot change another user's data — the route always uses session user_id."""
    _use_temp_db(monkeypatch, tmp_path)
    create_user("victim", "Vic", "Tim", "victim@example.com", "VictimPass1!", "student")
    create_user("attacker", "At", "Tacker", "attacker@example.com", "AttackPass1!", "student")

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "attacker"
        sess["user_role"] = "student"
        sess["2fa_verified"] = True

    # Attacker updates their own email — victim's email must be untouched
    client.post(
        "/account/settings/email",
        data={"email": "hacked@example.com", "current_password": "AttackPass1!"},
    )
    assert get_user("victim")["email"] == "victim@example.com"
    assert get_user("attacker")["email"] == "hacked@example.com"


def test_admin_can_use_account_settings(tmp_path, monkeypatch):
    """Admin users should also be able to update their own email via account settings."""
    client = _make_client(tmp_path, monkeypatch, user_id="admin123", role="admin",
                          email="admin@ams.local", setup_db=False)
    # setup_db=False because _make_client already calls _use_temp_db via the DB monkeypatch
    # We need the temp db here
    _use_temp_db(monkeypatch, tmp_path)
    create_user("admin123", "System", "Admin", "admin@ams.local", "Pass1234!", "admin")
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin123"
        sess["user_role"] = "admin"
        sess["2fa_verified"] = True

    response = client.get("/account/settings")
    assert response.status_code == 200
    assert "admin123" in response.get_data(as_text=True)
