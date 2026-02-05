from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec


class CSSRequiredRulesAssessor(Assessor):
    """Checks required CSS rules based on profile spec."""

    name = "css_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        css_files = sorted(context.discovered_files.get("css", []))

        if not self.profile_spec.required_css:
            findings.append(
                Finding(
                    id="CSS.REQ.SKIPPED",
                    category="css",
                    message="No required CSS rules defined for this profile; skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"rule_ids": []},
                    source=self.name,
                )
            )
            return findings

        # Check if CSS is required for this profile
        is_required = self.profile_spec.is_component_required("css")
        has_required_rules = self.profile_spec.has_required_rules("css")

        if not css_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="CSS.REQ.MISSING_FILES",
                        category="css",
                        message="No CSS files found; CSS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_css],
                            "expected_needles": [r.needle for r in self.profile_spec.required_css],
                            "discovered_count": 0,
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
                        id="CSS.REQ.SKIPPED",
                        category="css",
                        message="No CSS files found; CSS required checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_css],
                            "expected_needles": [r.needle for r in self.profile_spec.required_css],
                            "discovered_count": 0,
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

        for path in css_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id="CSS.REQ.READ_ERROR",
                        category="css",
                        message="Failed to read CSS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            brace_count = content.count("{")
            content_lower = content.lower()
            for rule in self.profile_spec.required_css:
                count, passed = self._evaluate_rule(rule, content, content_lower, brace_count)
                findings.append(
                    Finding(
                        id="CSS.REQ.PASS" if passed else "CSS.REQ.FAIL",
                        category="css",
                        message=self._message(rule.id, passed, count, rule.min_count),
                        severity=Severity.INFO if passed else Severity.WARN,
                        evidence={
                            "path": str(path),
                            "rule_id": rule.id,
                            "needle": rule.needle,
                            "min_count": rule.min_count,
                            "count": count,
                            "weight": rule.weight,
                        },
                        source=self.name,
                    )
                )
        return findings

    def _evaluate_rule(
        self, rule, content: str, content_lower: str, brace_count: int
    ) -> tuple[int, bool]:
        """Evaluate a single CSS rule against the file content.
        
        Returns:
            A tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the rule requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === STRUCTURE ===
        if needle == "{":
            # Count rule blocks
            return brace_count, brace_count >= rule.min_count
        
        if needle == "multiple_rules" or rule.id == "css.has_multiple_rules":
            # Check for multiple rule blocks
            return brace_count, brace_count >= rule.min_count
        
        # === SELECTORS ===
        if needle == "element_selector" or rule.id == "css.has_element_selector":
            # Check for common element selectors (before {)
            element_selectors = ["body", "html", "h1", "h2", "h3", "p", "a", "div", "span", "ul", "li", "table", "form", "input", "button", "header", "footer", "nav", "main", "section", "article"]
            count = sum(1 for sel in element_selectors if sel in content_lower)
            return count, count >= rule.min_count
        
        # === LAYOUT ===
        if needle == "layout" or rule.id == "css.has_layout":
            # Check for layout properties
            layout_props = ["margin", "padding", "display", "position", "width", "height", "top", "left", "right", "bottom"]
            count = sum(1 for prop in layout_props if prop in content_lower)
            return count, count >= rule.min_count
        
        if needle == "flexbox" or rule.id == "css.has_flexbox":
            # Check for flexbox usage
            has_flex = "display:" in content_lower and "flex" in content_lower
            has_flex = has_flex or "display: flex" in content_lower or "display:flex" in content_lower
            count = 1 if has_flex else 0
            return count, count >= rule.min_count
        
        if needle == "grid" or rule.id == "css.has_grid":
            # Check for CSS Grid usage
            has_grid = "display:" in content_lower and "grid" in content_lower
            has_grid = has_grid or "display: grid" in content_lower or "display:grid" in content_lower
            count = 1 if has_grid else 0
            return count, count >= rule.min_count
        
        # === STYLING ===
        if needle == "typography" or rule.id == "css.has_typography":
            # Check for typography properties
            typo_props = ["font-family", "font-size", "line-height", "font-weight", "letter-spacing", "text-align"]
            count = sum(1 for prop in typo_props if prop in content_lower)
            return count, count >= rule.min_count
        
        # === MAINTAINABILITY ===
        if needle == "custom_properties" or rule.id == "css.has_custom_properties":
            # Check for CSS custom properties (--variable)
            count = content.count("--")
            return count, count >= rule.min_count
        
        if needle == "comments" or rule.id == "css.has_comments":
            # Check for CSS comments
            count = content.count("/*")
            return count, count >= rule.min_count
        
        # === STANDARD NEEDLE COUNTING ===
        # For simple needles like ".", "#", "@media", "color:"
        count = content.count(rule.needle)
        return count, count >= rule.min_count

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["CSSRequiredRulesAssessor"]
