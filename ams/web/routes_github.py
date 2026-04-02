from __future__ import annotations

import logging
import secrets as _secrets

import requests as _requests
from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from ams.core.config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_OAUTH_CALLBACK
from ams.web.auth import teacher_or_admin_required

logger = logging.getLogger(__name__)
github_bp = Blueprint("github", __name__)


@github_bp.route("/threats")
@teacher_or_admin_required
# Show the current threat review page.
def threats():
    from ams.sandbox.forensics import list_retained_containers

    containers = list_retained_containers()
    return render_template("threats.html", containers=containers)


@github_bp.route("/threats/<container_name>/inspect")
@teacher_or_admin_required
# Show details for one threat container.
def threat_inspect(container_name: str):
    from ams.sandbox.forensics import inspect_container

    info = inspect_container(container_name)
    if info is None:
        flash("Container not found or not inspectable.", "error")
        return redirect(url_for("github.threats"))
    return render_template("threats.html", containers=[], inspected=info)


@github_bp.route("/threats/<container_name>/cleanup", methods=["POST"])
@teacher_or_admin_required
# Clean up a threat container.
def threat_cleanup(container_name: str):
    from ams.sandbox.forensics import cleanup_container

    ok = cleanup_container(container_name)
    if ok:
        flash(f"Container {container_name} removed.", "success")
    else:
        flash(f"Failed to remove container {container_name}.", "error")
    return redirect(url_for("github.threats"))


@github_bp.route("/api/github/login")
# Start the GitHub OAuth flow.
def github_login():
    if not GITHUB_CLIENT_ID:
        flash("GitHub integration is not configured (missing Client ID).")
        return redirect(url_for("marking.mark"))

    state = _secrets.token_urlsafe(32)
    session["github_oauth_state"] = state
    params = (
        f"client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_OAUTH_CALLBACK}"
        f"&scope=repo"
        f"&state={state}"
    )
    return redirect(f"https://github.com/login/oauth/authorize?{params}")


@github_bp.route("/api/github/callback")
# Handle the GitHub OAuth callback.
def github_callback():
    code = request.args.get("code", "")
    state = request.args.get("state", "")

    expected_state = session.pop("github_oauth_state", None)
    if not code or not state or state != expected_state:
        flash("GitHub authorization failed (invalid state). Please try again.")
        return redirect(url_for("marking.mark"))

    try:
        token_resp = _requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_OAUTH_CALLBACK,
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
    except _requests.RequestException as exc:
        logger.warning("GitHub OAuth token exchange failed: %s", exc)
        flash("Failed to connect to GitHub. Please try again.")
        return redirect(url_for("marking.mark"))

    access_token = token_data.get("access_token")
    if not access_token:
        error_desc = token_data.get("error_description", "Unknown error")
        flash(f"GitHub authorization failed: {error_desc}")
        return redirect(url_for("marking.mark"))

    try:
        user_resp = _requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_resp.raise_for_status()
        user_info = user_resp.json()
    except _requests.RequestException:
        user_info = {}

    session["github_token"] = access_token
    session["github_user"] = user_info.get("login", "")
    session["github_avatar"] = user_info.get("avatar_url", "")
    flash(f"Connected to GitHub as {user_info.get('login', 'unknown')}.", "success")
    return redirect(url_for("marking.mark"))


@github_bp.route("/api/github/disconnect", methods=["POST"])
# Disconnect the linked GitHub account.
def github_disconnect():
    session.pop("github_token", None)
    session.pop("github_user", None)
    session.pop("github_avatar", None)
    return jsonify({"status": "disconnected"})


@github_bp.route("/api/github/repos")
# List GitHub repositories for the linked account.
def github_repos():
    token = session.get("github_token")
    if not token:
        return jsonify({"error": "GitHub account not linked"}), 401

    try:
        resp = _requests.get(
            "https://api.github.com/user/repos",
            headers={"Authorization": f"Bearer {token}"},
            params={"sort": "updated", "per_page": 100},
            timeout=15,
        )
        resp.raise_for_status()
        repos = resp.json()
    except _requests.RequestException as exc:
        return jsonify({"error": f"Failed to fetch repositories: {exc}"}), 502

    return jsonify(
        [
            {
                "full_name": repo["full_name"],
                "name": repo["name"],
                "private": repo["private"],
                "updated_at": repo.get("updated_at", ""),
                "description": repo.get("description") or "",
                "default_branch": repo.get("default_branch", "main"),
            }
            for repo in repos
        ]
    )


@github_bp.route("/api/github/repos/<owner>/<repo>/branches")
# List branches for a selected GitHub repository.
def github_branches(owner: str, repo: str):
    token = session.get("github_token")
    if not token:
        return jsonify({"error": "GitHub account not linked"}), 401

    full_name = f"{owner}/{repo}"
    default_branch = "main"
    try:
        repo_resp = _requests.get(
            f"https://api.github.com/repos/{full_name}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        repo_resp.raise_for_status()
        default_branch = repo_resp.json().get("default_branch", "main")
    except _requests.RequestException:
        pass

    try:
        resp = _requests.get(
            f"https://api.github.com/repos/{full_name}/branches",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
            timeout=15,
        )
        resp.raise_for_status()
        branches = resp.json()
    except _requests.RequestException as exc:
        return jsonify({"error": f"Failed to fetch branches: {exc}"}), 502

    return jsonify(
        [
            {
                "name": branch["name"],
                "is_default": branch["name"] == default_branch,
            }
            for branch in branches
        ]
    )
