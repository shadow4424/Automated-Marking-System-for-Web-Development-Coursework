from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional

from .assessors import Assessor
from .models import Finding, Severity, SubmissionContext
from .profiles import ProfileSpec, RequiredHTMLRule, get_profile_spec


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

        if not html_files:
            findings.append(
                Finding(
                    id="HTML.REQ.SKIPPED",
                    category="html",
                    message="No HTML files found; required element checks skipped.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "expected_selectors": [rule.selector for rule in self.profile_spec.required_html],
                        "rule_ids": [rule.id for rule in self.profile_spec.required_html],
                    },
                    source=self.name,
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
        except OSError as exc:
            # Treat read error as empty content; surfaced via failing counts.
            return ""

    def _build_message(self, rule: RequiredHTMLRule, passed: bool, count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} ({rule.description}) {status}: found {count}, required {rule.min_count}"
