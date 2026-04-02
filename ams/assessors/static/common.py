from __future__ import annotations

from pathlib import Path

from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


def resolve_component_requirement(context: SubmissionContext, component_name: str) -> tuple[str, bool]:
    """Return the profile name and whether a component is required."""
    profile_name = str(context.metadata.get("profile") or "")
    if not profile_name:
        return "", False
    try:
        profile_spec = get_profile_spec(profile_name)
    except ValueError:
        return profile_name, False
    return profile_name, profile_spec.is_component_required(component_name)


def missing_component_finding(
    *,
    finding_id: str,
    category: str,
    message: str,
    expected_extensions: list[str],
    profile_name: str,
    source: str,
) -> Finding:
    """Build the common missing-files finding for a required component."""
    return Finding(
        id=finding_id,
        category=category,
        message=message,
        severity=Severity.FAIL,
        evidence={
            "expected_extensions": expected_extensions,
            "discovered_count": 0,
            "profile": profile_name,
            "required": True,
        },
        source=source,
        finding_category=FindingCategory.MISSING,
        profile=profile_name or None,
        required=True,
    )


def skipped_component_finding(
    *,
    finding_id: str,
    category: str,
    message: str,
    expected_extensions: list[str],
    profile_name: str,
    source: str,
) -> Finding:
    """Build the common skipped finding for an optional component."""
    return Finding(
        id=finding_id,
        category=category,
        message=message,
        severity=Severity.SKIPPED,
        evidence={
            "expected_extensions": expected_extensions,
            "discovered_count": 0,
            "profile": profile_name,
            "required": False,
        },
        source=source,
        finding_category=FindingCategory.OTHER,
        profile=profile_name or None,
        required=False,
    )


def read_component_text(
    path: Path,
    *,
    finding_id: str,
    category: str,
    source: str,
    message: str,
) -> tuple[str | None, Finding | None]:
    """Read component source text or return a read-error finding."""
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, Finding(
            id=finding_id,
            category=category,
            message=message,
            severity=Severity.FAIL,
            evidence={"path": str(path), "error": str(exc)},
            source=source,
        )


__all__ = [
    "missing_component_finding",
    "read_component_text",
    "resolve_component_requirement",
    "skipped_component_finding",
]
