from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, RequiredHTMLRule, get_profile_spec


class _TagCountingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.counts: Dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        lowered = tag.lower()
        self.counts[lowered] = self.counts.get(lowered, 0) + 1

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[override]
        self.handle_starttag(tag, attrs)


class HTMLRequiredElementsAssessor(Assessor):
    """Checks required HTML elements based on profile spec using a real HTML parser."""

    name = "html_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.discovered_files.get("html", []))

        # Check if HTML is required for this profile
        is_required = self.profile_spec.is_component_required("html")
        has_required_rules = self.profile_spec.has_required_rules("html")

        if not html_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="HTML.REQ.MISSING_FILES",
                        category="html",
                        message="No HTML files found; HTML is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_selectors": [rule.selector for rule in self.profile_spec.required_html],
                            "rule_ids": [rule.id for rule in self.profile_spec.required_html],
                            "profile": self.profile_spec.name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=self.profile_spec.name,
                        required=True,
                    )
                )
            else:
                # Not required or no rules defined, skip
                findings.append(
                    Finding(
                        id="HTML.REQ.SKIPPED",
                        category="html",
                        message="No HTML files found; HTML required element checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_selectors": [rule.selector for rule in self.profile_spec.required_html],
                            "rule_ids": [rule.id for rule in self.profile_spec.required_html],
                            "profile": self.profile_spec.name,
                            "required": is_required,
                            "has_required_rules": has_required_rules,
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=is_required,
                    )
                )
            return findings

        for path in html_files:
            content = self._read_file(path)
            parser = _TagCountingParser()
            parser.feed(content)
            for rule in self.profile_spec.required_html:
                count = parser.counts.get(rule.selector.lower(), 0)
                passed = count >= rule.min_count
                finding_id = "HTML.REQ.PASS" if passed else "HTML.REQ.FAIL"
                severity = Severity.INFO if passed else Severity.WARN
                findings.append(
                    Finding(
                        id=finding_id,
                        category="html",
                        message=self._build_message(rule, passed, count),
                        severity=severity,
                        evidence={
                            "path": str(path),
                            "rule_id": rule.id,
                            "selector": rule.selector,
                            "min_count": rule.min_count,
                            "count": count,
                        },
                        source=self.name,
                    )
                )
        return findings

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _build_message(self, rule: RequiredHTMLRule, passed: bool, count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} ({rule.description}) {status}: found {count}, required {rule.min_count}"


__all__ = ["HTMLRequiredElementsAssessor"]
