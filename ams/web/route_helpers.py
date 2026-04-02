from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ams.core.db import get_assignment
from ams.web.routes_common import json_error, redirect_with_flash


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


__all__ = [
    "load_accessible_assignment_or_json",
    "load_accessible_assignment_or_redirect",
]
