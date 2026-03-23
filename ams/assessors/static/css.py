from __future__ import annotations

import re
from typing import List

from ams.assessors import Assessor
from ams.core.finding_ids import CSS as CID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class CSSStaticAssessor(Assessor):
    """Deterministic CSS static checks."""

    name = "css_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        css_files = sorted(context.files_for("css", relevant_only=True))
        
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
                        id=CID.MISSING_FILES,
                        category="css",
                        message="No CSS files found; CSS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".css"],
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
                        id=CID.SKIPPED,
                        category="css",
                        message="No CSS files found; CSS is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".css"],
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

        for path in css_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=CID.READ_ERROR,
                        category="css",
                        message="Failed to read CSS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
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
                        id=CID.BRACES_BALANCED,
                        category="css",
                        message="CSS braces appear balanced.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            elif open_braces == 0 and close_braces == 0:
                findings.append(
                    Finding(
                        id=CID.NO_RULES,
                        category="css",
                        message="No CSS rules detected.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=CID.BRACES_UNBALANCED,
                        category="css",
                        message="CSS braces appear unbalanced.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )

            findings.append(
                Finding(
                    id=CID.EVIDENCE,
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
                    source=self.name,
                )
            )

            # Code Quality Checks
            # 1. Evaluate CSS selector specificity - flag overly specific selectors
            lines = content.splitlines()
            overly_specific_selectors = []
            max_specificity_score = 0
            
            for line in lines:
                # Extract selector part (before {)
                if "{" in line:
                    selector_part = line.split("{", 1)[0].strip()
                    if selector_part and not selector_part.startswith("@"):
                        # Calculate specificity: count IDs, classes, elements
                        # Simplified: count #, ., and element names
                        id_count = selector_part.count("#")
                        class_count = selector_part.count(".")
                        # Count element names (simplified - count words that aren't # or .)
                        element_count = len(re.findall(r'\b[a-z]+\b', selector_part.lower()))
                        
                        # Weighted specificity: IDs=100, classes=10, elements=1
                        specificity = id_count * 100 + class_count * 10 + element_count
                        max_specificity_score = max(max_specificity_score, specificity)
                        
                        # Flag selectors with specificity > 120 (e.g., #id .class .class element)
                        if specificity > 120:
                            overly_specific_selectors.append({
                                "selector": selector_part[:50],  # Truncate for display
                                "specificity": specificity,
                            })
            
            if overly_specific_selectors:
                findings.append(
                    Finding(
                        id=CID.QUALITY_OVERLY_SPECIFIC,
                        category="css",
                        message=f"Found {len(overly_specific_selectors)} overly specific selector(s). High specificity makes CSS harder to maintain and override.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "overly_specific_count": len(overly_specific_selectors),
                            "max_specificity": max_specificity_score,
                            "threshold": 120,
                            "examples": overly_specific_selectors[:5],  # Limit examples
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

        return findings


__all__ = ["CSSStaticAssessor"]
