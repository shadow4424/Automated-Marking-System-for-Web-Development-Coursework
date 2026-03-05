from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import CSS as CID
from ams.core.profiles import ProfileSpec, RequiredCSSRule


class CSSRequiredRulesAssessor(BaseRequiredAssessor):
    """Checks required CSS rules based on profile spec.
    
    Inherits common behaviour from BaseRequiredAssessor:
    - File reading and error handling
    - Snippet extraction
    - Finding creation
    - Unified run() pipeline
    
    Implements CSS-specific:
    - Rule evaluation with CSS-specific logic
    - Message building
    - Finding ID mapping
    """

    name = "css_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        super().__init__(profile)
    
    @property
    def component_name(self) -> str:
        return "css"
    
    @property
    def required_rules(self) -> List[RequiredCSSRule]:
        return list(self.profile_spec.required_css)
    
    def _get_finding_id_pass(self) -> str:
        return CID.REQ_PASS
    
    def _get_finding_id_fail(self) -> str:
        return CID.REQ_FAIL
    
    def _get_finding_id_skipped(self) -> str:
        return CID.REQ_SKIPPED
    
    def _get_finding_id_missing_files(self) -> str:
        return CID.REQ_MISSING_FILES
    
    def _evaluate_rule_impl(
        self, rule: RequiredCSSRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single CSS rule against content.
        
        Args:
            rule: The CSS rule to evaluate
            content: Raw CSS content as string
            
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        brace_count = content.count("{")
        content_lower = content.lower()
        return self._evaluate_rule(rule, content, content_lower, brace_count)

    def _evaluate_rule(
        self, rule: RequiredCSSRule, content: str, content_lower: str, brace_count: int
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
        # For simple needles like ".", "#", "@media", "colour:"
        count = content.count(rule.needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredCSSRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["CSSRequiredRulesAssessor"]
