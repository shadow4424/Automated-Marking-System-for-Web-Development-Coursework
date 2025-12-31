from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class CSSStaticAssessor(Assessor):
    """Deterministic CSS static checks."""

    source = "css_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        css_files = sorted(context.discovered_files.get("css", []))
        
        # Determine if CSS is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("css")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not css_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="CSS.MISSING_FILES",
                        category="css",
                        message="No CSS files found; CSS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".css"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": True,
                        },
                        source=self.source,
                        finding_category=FindingCategory.MISSING,
                        profile=profile_name,
                        required=True,
                    )
                )
            else:
                # Not required for profile, skip
                findings.append(
                    Finding(
                        id="CSS.SKIPPED",
                        category="css",
                        message="No CSS files found; CSS is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".css"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": False,
                        },
                        source=self.source,
                        finding_category=FindingCategory.OTHER,
                        profile=profile_name,
                        required=False,
                    )
                )
            return findings

        for path in css_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id="CSS.READ_ERROR",
                        category="css",
                        message="Failed to read CSS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.source,
                    )
                )
                continue

            open_braces = content.count("{")
            close_braces = content.count("}")
            balanced = open_braces == close_braces and open_braces > 0

            has_selector_hint = False
            for line in content.splitlines():
                if "{" in line:
                    before = line.split("{", 1)[0].strip()
                    if before and not before.startswith("@"):
                        has_selector_hint = True
                        break

            lowered = content.lower()
            has_at_rule = "@media" in lowered or "@keyframes" in lowered

            structure_evidence = {
                "path": str(path),
                "open_braces": open_braces,
                "close_braces": close_braces,
                "balanced": balanced,
                "has_selector_hint": has_selector_hint,
                "has_at_rule": has_at_rule,
            }

            if balanced:
                findings.append(
                    Finding(
                        id="CSS.BRACES_BALANCED",
                        category="css",
                        message="CSS braces appear balanced.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.source,
                    )
                )
            elif open_braces == 0 and close_braces == 0:
                findings.append(
                    Finding(
                        id="CSS.NO_RULES",
                        category="css",
                        message="No CSS rules detected.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.source,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id="CSS.BRACES_UNBALANCED",
                        category="css",
                        message="CSS braces appear unbalanced.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.source,
                    )
                )

            findings.append(
                Finding(
                    id="CSS.EVIDENCE",
                    category="css",
                    message="CSS evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "selectors_approx": open_braces,
                        "media_queries": lowered.count("@media"),
                        "keyframes": lowered.count("@keyframes"),
                        "important": lowered.count("!important"),
                    },
                    source=self.source,
                )
            )

        return findings


__all__ = ["CSSStaticAssessor"]
