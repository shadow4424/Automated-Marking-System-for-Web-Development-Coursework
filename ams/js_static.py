from __future__ import annotations

from typing import List

from .assessors import Assessor
from .models import Finding, Severity, SubmissionContext


class JSStaticAssessor(Assessor):
    """Deterministic JavaScript static checks using simple heuristics."""

    name = "js_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        js_files = sorted(context.discovered_files.get("js", []))

        if not js_files:
            findings.append(
                Finding(
                    id="JS.MISSING",
                    category="js",
                    message="No JavaScript files found; JS checks skipped.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "expected_extensions": [".js"],
                        "discovered_count": 0,
                    },
                    source=self.name,
                )
            )
            return findings

        for path in js_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id="JS.READ_ERROR",
                        category="js",
                        message="Failed to read JS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            open_braces = content.count("{")
            close_braces = content.count("}")
            open_parens = content.count("(")
            close_parens = content.count(")")
            braces_balanced = open_braces == close_braces
            parens_balanced = open_parens == close_parens
            has_semicolons = ";" in content

            syntax_evidence = {
                "path": str(path),
                "open_braces": open_braces,
                "close_braces": close_braces,
                "open_parens": open_parens,
                "close_parens": close_parens,
                "braces_balanced": braces_balanced,
                "parens_balanced": parens_balanced,
                "has_semicolons": has_semicolons,
            }

            if braces_balanced and parens_balanced and (open_braces + open_parens) > 0:
                findings.append(
                    Finding(
                        id="JS.SYNTAX_OK",
                        category="js",
                        message="JS syntax heuristics look OK.",
                        severity=Severity.INFO,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            elif (open_braces + close_braces + open_parens + close_parens) == 0:
                findings.append(
                    Finding(
                        id="JS.NO_CODE",
                        category="js",
                        message="JS file appears to contain no code-like tokens.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id="JS.SYNTAX_SUSPECT",
                        category="js",
                        message="JS syntax heuristics look suspect.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )

            lowered = content.lower()
            dom_calls = lowered.count("document.")
            query_calls = lowered.count("queryselector") + lowered.count("getelementby")
            event_listeners = lowered.count("addeventlistener")
            fetch_calls = lowered.count("fetch(")
            xhr_calls = lowered.count("xmlhttprequest")
            loops = lowered.count("for (") + lowered.count("while (")
            functions = lowered.count("function ") + lowered.count("=>")

            findings.append(
                Finding(
                    id="JS.EVIDENCE",
                    category="js",
                    message="JS evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "dom_calls": dom_calls,
                        "query_calls": query_calls,
                        "event_listeners": event_listeners,
                        "fetch_calls": fetch_calls,
                        "xhr_calls": xhr_calls,
                        "loops": loops,
                        "functions": functions,
                    },
                    source=self.name,
                )
            )

        return findings
