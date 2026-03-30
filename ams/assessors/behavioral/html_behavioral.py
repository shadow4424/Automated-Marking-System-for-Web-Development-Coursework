from __future__ import annotations

from html.parser import HTMLParser
from typing import List

from ams.assessors import Assessor
from ams.core.finding_ids import HTML as HID
from ams.core.models import Finding, Severity, SubmissionContext


class FormChecker(HTMLParser):
    """Simple HTML parser to check for forms and basic structure."""

    def __init__(self) -> None:
        super().__init__()
        self.has_form = False
        self.has_input = False
        self.has_link = False
        self.form_count = 0
        self.input_count = 0
        self.link_count = 0
        self.has_body = False
        self.parse_errors: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.has_form = True
            self.form_count += 1
        elif tag == "input":
            self.has_input = True
            self.input_count += 1
        elif tag == "a":
            self.has_link = True
            self.link_count += 1
        elif tag == "body":
            self.has_body = True

    def error(self, message: str) -> None:
        self.parse_errors.append(message)


class HTMLBehavioralAssessor(Assessor):
    """Behavioural checks: verifies HTML can be parsed and forms exist."""

    name = "html_behavioral"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.files_for("html", relevant_only=True))

        if not html_files:
            findings.append(
                Finding(
                    id=HID.BEHAVIORAL_SKIPPED,
                    category="html",
                    message="No HTML files found; behavioral checks skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"discovered_count": 0},
                    source=self.name,
                )
            )
            return findings

        for path in html_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_READ_ERROR,
                        category="html",
                        message="Failed to read HTML file for behavioral check.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            # Parse HTML to check structure
            parser = FormChecker()
            try:
                parser.feed(content)
            except Exception as exc:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_PARSE_ERROR,
                        category="html",
                        message="HTML parsing failed during behavioral check.",
                        severity=Severity.WARN,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            # Check whether page loads (has body tag)
            if parser.has_body:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_PAGE_LOADS,
                        category="html",
                        message="HTML page structure valid (body tag found).",
                        severity=Severity.INFO,
                        evidence={"path": str(path), "has_body": True},
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_NO_BODY,
                        category="html",
                        message="HTML page missing body tag.",
                        severity=Severity.WARN,
                        evidence={"path": str(path), "has_body": False},
                        source=self.name,
                    )
                )

            # Check whether form exists
            if parser.has_form:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_FORM_EXISTS,
                        category="html",
                        message="Form element found in HTML.",
                        severity=Severity.INFO,
                        evidence={
                            "path": str(path),
                            "form_count": parser.form_count,
                            "input_count": parser.input_count,
                        },
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=HID.BEHAVIORAL_NO_FORM,
                        category="html",
                        message="No form element found in HTML.",
                        severity=Severity.WARN,
                        evidence={"path": str(path), "form_count": 0},
                        source=self.name,
                    )
                )

        return findings


__all__ = ["HTMLBehavioralAssessor"]


