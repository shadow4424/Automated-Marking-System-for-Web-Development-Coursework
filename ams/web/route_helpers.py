from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ams.core.assignment_store import get_assignment
from ams.io.web_storage import find_run_by_id, get_runs_root, load_run_info
from ams.web.routes_common import is_async_job_request, json_error, redirect_with_flash


# -- assignment helpers ----------------------------------------------------

def load_accessible_assignment_or_redirect(
    assignment_id: str,
    *,
    access_checker: Callable[[dict[str, Any]], bool],
    loader: Callable[[str], dict[str, Any] | None] = get_assignment,
    redirect_endpoint: str = "teacher_dashboard.dashboard",
) -> tuple[dict[str, Any] | None, Any | None]:
    """Load an assignment or return the standard redirect failure response."""
    assignment = loader(assignment_id)
    if assignment is None:
        return None, redirect_with_flash(redirect_endpoint, "Assignment not found.", "error")
    if not access_checker(assignment):
        return None, redirect_with_flash(redirect_endpoint, "You do not have access to this assignment.", "error")
    return assignment, None


def load_accessible_assignment_or_json(
    assignment_id: str,
    *,
    access_checker: Callable[[dict[str, Any]], bool],
    loader: Callable[[str], dict[str, Any] | None] = get_assignment,
) -> tuple[dict[str, Any] | None, Any | None]:
    """Load an assignment or return the standard JSON failure response."""
    assignment = loader(assignment_id)
    if assignment is None:
        return None, json_error("Assignment not found.", 404)
    if not access_checker(assignment):
        return None, json_error("You do not have access to this assignment.", 403)
    return assignment, None


# -- run helpers -----------------------------------------------------------

def find_run(run_id: str) -> Path | None:
    """Locate a run directory by *run_id*."""
    from flask import current_app

    return find_run_by_id(get_runs_root(current_app), run_id)


def load_run(run_id: str) -> tuple[Path | None, dict]:
    """Locate a run and load its metadata.  Returns ``(None, {})`` when missing."""
    run_dir = find_run(run_id)
    if run_dir is None:
        return None, {}
    return run_dir, load_run_info(run_dir) or {}


# -- response helpers ------------------------------------------------------

def dual_error(
    json_msg: str,
    json_status: int,
    endpoint: str,
    flash_msg: str | None = None,
    flash_category: str = "error",
    **url_kwargs: Any,
):
    """Return JSON for async requests, flash + redirect for synchronous ones."""
    if is_async_job_request():
        return json_error(json_msg, json_status)
    return redirect_with_flash(endpoint, flash_msg or json_msg, flash_category, **url_kwargs)


__all__ = [
    "dual_error",
    "find_run",
    "load_accessible_assignment_or_json",
    "load_accessible_assignment_or_redirect",
    "load_run",
]
