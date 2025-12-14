from __future__ import annotations

from typing import List

from .assessors import Assessor
from .models import Finding, Severity, SubmissionContext


class HTMLStaticAssessor(Assessor):
    """Deterministic HTML static checks."""

    name = "html_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.discovered_files.get("html", []))

        if not html_files:
            findings.append(
                Finding(
                    id="HTML.MISSING",
                    category="html",
                    message="No HTML files found; HTML checks skipped.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "expected_extensions": [".html"],
                        "discovered_count": 0,
                    },
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
                        id="HTML.READ_ERROR",
                        category="html",
                        message="Failed to read HTML file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            lowered = content.lower()
            has_doctype = "<!doctype html" in lowered
            has_html_tag = "<html" in lowered and "</html" in lowered
            has_head = "<head" in lowered
            has_body = "<body" in lowered

            structure_evidence = {
                "path": str(path),
                "has_doctype": has_doctype,
                "has_html_tag": has_html_tag,
                "has_head": has_head,
                "has_body": has_body,
            }

            if (has_doctype or has_html_tag) and has_head and has_body:
                findings.append(
                    Finding(
                        id="HTML.PARSE_OK",
                        category="html",
                        message="HTML structure appears valid.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id="HTML.PARSE_SUSPECT",
                        category="html",
                        message="HTML structure incomplete or missing expected elements.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )

            forms = lowered.count("<form")
            inputs = lowered.count("<input")
            links = lowered.count("<a ")

            findings.append(
                Finding(
                    id="HTML.ELEMENT_EVIDENCE",
                    category="html",
                    message="HTML element evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "forms": forms,
                        "inputs": inputs,
                        "links": links,
                    },
                    source=self.name,
                )
            )

        return findings
