"""Base class for language-specific required rule assessors (Phase 3: DRY).

Subclasses set a handful of class attributes and implement
``_evaluate_rule_impl``; all boilerplate lives here.
"""
from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any, List, Tuple

from ams.assessors import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, RequiredRule, get_profile_spec


class BaseRequiredAssessor(Assessor):
    """Abstract base for all required rule assessors.

    Subclasses **must** define the following class attributes:

    * ``_component``          – e.g. ``"html"``, ``"css"``
    * ``_finding_ids_class``  – the finding-ID namespace class (e.g. ``HTML``)
    * ``_default_profile``    – default profile name (default ``"frontend"``)

    The ``name`` attribute is auto-derived as ``"{_component}_required"``
    unless the subclass overrides it.
    """

    _component: str
    _finding_ids_class: Any          # e.g. finding_ids.HTML
    _default_profile: str = "frontend"

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        # Auto-derive ``name`` when the subclass declares ``_component``.
        if "_component" in cls.__dict__ and "name" not in cls.__dict__:
            cls.name = f"{cls._component}_required"

    def __init__(self, profile: str | ProfileSpec | None = None) -> None:
        if profile is None:
            profile = self._default_profile
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    # -- derived properties (no override needed) -------------------------

    @property
    def component_name(self) -> str:
        return self._component

    @property
    def required_rules(self) -> List[RequiredRule]:
        return list(getattr(self.profile_spec, f"required_{self._component}"))

    def _get_finding_id_pass(self) -> str:
        return self._finding_ids_class.REQ_PASS

    def _get_finding_id_fail(self) -> str:
        return self._finding_ids_class.REQ_FAIL

    def _get_finding_id_skipped(self) -> str:
        return self._finding_ids_class.REQ_SKIPPED

    def _get_finding_id_missing_files(self) -> str:
        return self._finding_ids_class.REQ_MISSING_FILES

    def _build_message(self, rule: RequiredRule, passed: bool, count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"

    # -- abstract (subclass must implement) ------------------------------

    @abstractmethod
    def _evaluate_rule_impl(self, rule: RequiredRule, content: str) -> Tuple[int, bool]:
        """Evaluate single rule. Return (occurrence_count, passed: bool)."""
        pass

    def run(self, context: SubmissionContext) -> List[Finding]:
        """Unified pipeline for all required assessors."""
        findings: List[Finding] = []

        # Check whether component is required and has rules
        is_required = self.profile_spec.is_component_required(self.component_name)
        has_rules = self.profile_spec.has_required_rules(self.component_name)

        # If no rules defined, return SKIPPED finding
        if not has_rules:
            findings.append(
                Finding(
                    id=self._get_finding_id_skipped(),
                    category=self.component_name,
                    message=f"No {self.component_name.upper()} checks defined for this profile.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "profile": self.profile_spec.name,
                        "skip_reason": "no_rules_defined",
                    },
                    source=self.name,
                    finding_category=FindingCategory.OTHER,
                    profile=self.profile_spec.name,
                    required=is_required,
                )
            )
            return findings

        # If component not required, return per-rule SKIPPED findings
        if not is_required:
            for rule in self.required_rules:
                findings.append(
                    Finding(
                        id=self._get_finding_id_skipped(),
                        category=self.component_name,
                        message=f"Rule '{rule.id}' skipped: {self.component_name.upper()} not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_id": rule.id,
                            "description": getattr(rule, "description", ""),
                            "selector": getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                            "weight": getattr(rule, "weight", 0),
                            "skip_reason": "component_not_required",
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=False,
                    )
                )
            return findings

        # Get files for this component
        files = sorted(context.discovered_files.get(self.component_name, []))

        # No files found → generate per-rule FAIL findings (required component missing files)
        if not files:
            for rule in self.required_rules:
                findings.append(
                    Finding(
                        id=self._get_finding_id_missing_files(),
                        category=self.component_name,
                        message=f"Rule '{rule.id}' not evaluated: No {self.component_name.upper()} files found in submission.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_id": rule.id,
                            "description": getattr(rule, "description", ""),
                            "selector": getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                            "weight": getattr(rule, "weight", 0),
                            "skip_reason": "no_files_found",
                            "profile": self.profile_spec.name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=self.profile_spec.name,
                        required=True,
                    )
                )
            return findings

        # Evaluate each file and rule
        for path in files:
            content = self._read_file_safe(path)
            for rule in self.required_rules:
                count, passed = self._evaluate_rule_impl(rule, content)
                snippet = self._extract_snippet(
                    content,
                    getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                    rule.id,
                )

                finding = self._create_finding(
                    rule=rule,
                    path=path,
                    passed=passed,
                    count=count,
                    snippet=snippet,
                    content=content,
                )
                findings.append(finding)

        return findings


    # Consolidated Helpers (eliminates ~250 lines of duplication)


    def _read_file_safe(self, path: Path) -> str:
        """Read file with error handling."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _extract_snippet(
        self,
        content: str,
        needle: str,
        rule_id: str,
        context_lines: int = 2,
    ) -> str:
        """Extract relevant code snippet around needle/selector."""
        if not content or not content.strip():
            return "(file is empty)"

        lines = content.splitlines()
        needle_lower = needle.lower()

        # Reduce CSS and HTML selectors to a tag-like search term first.
        # For example, "form" and "input[type=text]" both search for the opening tag.
        tag_name = needle_lower.split("[")[0].split(".")[0].split("#")[0].strip()
        search_term = f"<{tag_name}" if tag_name and self._is_html_like() else needle_lower

        # Find first matching line
        for i, line in enumerate(lines):
            if search_term in line.lower():
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet_lines = [f"{j + 1:>4} | {lines[j]}" for j in range(start, end)]
                return "\n".join(snippet_lines)

        # No direct match: show both start and end context so downstream
        # Feedback/partial-credit logic can still see late-file attempts.
        if len(lines) <= 20:
            preview_lines = [f"{j + 1:>4} | {lines[j]}" for j in range(len(lines))]
            return "\n".join(preview_lines)

        head_count = 10
        tail_count = 10
        head = [f"{j + 1:>4} | {lines[j]}" for j in range(head_count)]
        tail_start = len(lines) - tail_count
        tail = [f"{j + 1:>4} | {lines[j]}" for j in range(tail_start, len(lines))]
        return "\n".join(head + ["  ... | ..."] + tail)

    def _is_html_like(self) -> bool:
        """Return True if this assessor deals with HTML-like content."""
        return self.component_name in ("html", "php")

    def _create_finding(
        self,
        rule: RequiredRule,
        path: Path,
        passed: bool,
        count: int,
        snippet: str,
        content: str,
    ) -> Finding:
        """Unified Finding creation (eliminates ~20 lines per assessor)."""
        finding_id = self._get_finding_id_pass() if passed else self._get_finding_id_fail()
        severity = Severity.INFO if passed else Severity.WARN

        # Extract rule selector/needle for evidence (generic approach)
        selector = getattr(rule, "selector", None) or getattr(rule, "needle", "unknown")

        return Finding(
            id=finding_id,
            category=self.component_name,
            message=self._build_message(rule, passed, count),
            severity=severity,
            evidence={
                "path": str(path),
                "rule_id": rule.id,
                "selector": selector,
                "min_count": getattr(rule, "min_count", 0),
                "count": count,
                "weight": getattr(rule, "weight", 0),
                "snippet": snippet,
                "content": content[:500],
            },
            source=self.name,
            finding_category=FindingCategory.STRUCTURE if passed else FindingCategory.MISSING,
            profile=self.profile_spec.name,
            required=True,
        )


__all__ = ["BaseRequiredAssessor"]
