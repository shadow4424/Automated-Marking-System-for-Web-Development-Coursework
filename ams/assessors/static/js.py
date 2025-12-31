from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class JSStaticAssessor(Assessor):
    """Deterministic JavaScript static checks using simple heuristics."""

    name = "js_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        js_files = sorted(context.discovered_files.get("js", []))
        
        # Determine if JS is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("js")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not js_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="JS.MISSING_FILES",
                        category="js",
                        message="No JavaScript files found; JS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".js"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=profile_name,
                        required=True,
                    )
                )
            else:
                # Not required for profile, skip
                findings.append(
                    Finding(
                        id="JS.SKIPPED",
                        category="js",
                        message="No JavaScript files found; JS is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".js"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": False,
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=profile_name,
                        required=False,
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


__all__ = ["JSStaticAssessor"]
