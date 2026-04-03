from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, List

from ams.assessors import Assessor
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


# ---------------------------------------------------------------------------
# Base class — eliminates ~30 lines of boilerplate per concrete assessor.
# ---------------------------------------------------------------------------

class BaseStaticAssessor(Assessor):
    """DRY base for component-level static assessors.

    Subclasses set class attributes and implement ``_analyse_loaded_files``.

    Class attributes:
        _component          – e.g. ``"html"``
        _finding_ids_class  – namespace with ``MISSING_FILES``, ``SKIPPED``,
                              ``READ_ERROR`` constants
        _extensions         – e.g. ``[".html"]``
    """

    _component: str
    _finding_ids_class: Any
    _extensions: list[str]

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if "_component" in cls.__dict__ and "name" not in cls.__dict__:
            cls.name = f"{cls._component}_static"

    # -- overridable helpers -----------------------------------------------

    def _discover_files(self, context: SubmissionContext) -> list[Path]:
        """Return sorted list of files for this component."""
        return sorted(context.files_for(self._component, relevant_only=True))

    def _missing_message(self) -> str:
        upper = self._component.upper()
        return f"No {upper} files found; {upper} is required for this profile."

    def _skipped_message(self) -> str:
        upper = self._component.upper()
        return f"No {upper} files found; {upper} is not required for this profile."

    # -- concrete run() ----------------------------------------------------

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        files = self._discover_files(context)
        profile_name, is_required = resolve_component_requirement(
            context, self._component,
        )

        if not files:
            findings.append(
                missing_component_finding(
                    finding_id=self._finding_ids_class.MISSING_FILES,
                    category=self._component,
                    message=self._missing_message(),
                    source=self.name,
                    profile_name=profile_name,
                    expected_extensions=self._extensions,
                )
                if is_required
                else skipped_component_finding(
                    finding_id=self._finding_ids_class.SKIPPED,
                    category=self._component,
                    message=self._skipped_message(),
                    source=self.name,
                    profile_name=profile_name,
                    expected_extensions=self._extensions,
                )
            )
            return findings

        loaded: list[tuple[Path, str]] = []
        for path in files:
            content, read_error = read_component_text(
                path,
                finding_id=self._finding_ids_class.READ_ERROR,
                category=self._component,
                source=self.name,
                message=f"Failed to read {self._component.upper()} file.",
            )
            if read_error is not None:
                findings.append(read_error)
                continue
            loaded.append((path, content))

        findings.extend(self._analyse_loaded_files(loaded))
        return findings

    @abstractmethod
    def _analyse_loaded_files(
        self, loaded_files: list[tuple[Path, str]],
    ) -> List[Finding]:
        """Analyse all successfully-loaded files and return findings."""
        ...


__all__ = [
    "BaseStaticAssessor",
    "missing_component_finding",
    "read_component_text",
    "resolve_component_requirement",
    "skipped_component_finding",
]
