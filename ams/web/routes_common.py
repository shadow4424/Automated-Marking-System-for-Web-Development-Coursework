from __future__ import annotations

from collections.abc import Callable

from flask import flash, jsonify, redirect, request, url_for


def redirect_with_flash(endpoint: str, message: str, category: str = "info", **url_kwargs):
    """Flash a message, then redirect to a Flask endpoint."""
    flash(message, category)
    return redirect(url_for(endpoint, **url_kwargs))


def json_ok(data: dict | None = None, message: str = "OK"):
    """Return a standard JSON success payload."""
    payload = {"status": "ok", "message": message}
    if data:
        payload.update(data)
    return jsonify(payload)


def json_error(message: str, status: int = 400):
    """Return a standard JSON error payload."""
    return jsonify({"status": "error", "message": message}), status


def is_async_job_request() -> bool:
    """Return whether the current request asked for an async response."""
    return request.headers.get("X-AMS-Async") == "1"


def build_rerun_job_response(
    *,
    job_id: str,
    run_id: str,
    label: str,
    assignment_id: str,
    view_url: str,
    refresh_url: str,
):
    """Return a standard rerun response for async and sync callers."""
    payload = {
        "job_id": job_id,
        "status": "accepted",
        "run_id": run_id,
        "assignment_id": assignment_id,
        "label": label,
        "view_url": view_url,
        "refresh_url": refresh_url,
    }
    if is_async_job_request():
        return jsonify(payload), 202
    flash(f"{label} queued. The submission will update when background processing finishes.", "success")
    return redirect(refresh_url)


def submit_rerun_job(
    job_manager,
    rerun_job: Callable[[], dict],
    *,
    run_id: str,
    label: str,
    assignment_id: str,
    view_url: str,
    refresh_url: str,
):
    """Submit a rerun job and return the standard response payload."""
    job_id = job_manager.submit_job("submission_rerun", rerun_job)
    return build_rerun_job_response(
        job_id=job_id,
        run_id=run_id,
        label=label,
        assignment_id=assignment_id,
        view_url=view_url,
        refresh_url=refresh_url,
    )
