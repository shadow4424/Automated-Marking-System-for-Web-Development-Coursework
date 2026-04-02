from __future__ import annotations

from html.parser import HTMLParser
from typing import List

from ams.assessors import Assessor
from ams.core.finding_ids import HTML as HID
from ams.core.models import Finding, Severity, SubmissionContext

# Class to check HTML structure and presence of forms, inputs, and links.
class FormChecker(HTMLParser):
    """Simple HTML parser to check for forms and basic structure."""

    # pylint: disable=too-many-instance-attributes
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
    
    # Override to track tags of interest
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

    # Override to track parsing errors
    def error(self, message: str) -> None:
        self.parse_errors.append(message)

# Class to perform behavioral checks on HTML files.
class HTMLBehavioralAssessor(Assessor):
    """Behavioural checks: verifies HTML can be parsed and forms exist."""

    name = "html_behavioral"

    # Function to run the assessor on the given submission context, returning findings.
    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.files_for("html", relevant_only=True))

        # If no HTML files are found, skip behavioral checks and log a finding.
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

        # Iterate over each HTML file and perform checks on its content and structure.
        for path in html_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                # If the file cannot be read, log a finding and skip to the next file.
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
                    # If parsing fails, log a finding with the error details and skip to the next file.
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
            # If no body tag is found, log a warning finding indicating the page may not load properly.
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
            # If no form is found, log a warning finding indicating that no form element was found in the HTML.
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
