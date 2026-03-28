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

        # === RESET ===
        if needle == "universal_reset" or rule.id == "css.has_universal_reset":
            # Accept * selector blocks, box-sizing reset strategies, or reset-library styles
            has_star_block = "* {" in content_lower or "*{" in content_lower
            has_box_sizing = "box-sizing" in content_lower
            has_margin_reset = ("margin: 0" in content_lower or "margin:0" in content_lower)
            count = 1 if (has_star_block or has_box_sizing or has_margin_reset) else 0
            return count, count >= rule.min_count

        # === PARSE CLEANLY ===
        if needle == "parses_cleanly" or rule.id == "css.parses_cleanly":
            open_count = content.count("{")
            close_count = content.count("}")
            imbalance = abs(open_count - close_count)
            if open_count == 0:
                # No rules at all — this is a fail
                return 0, False
            if imbalance == 0:
                return 1, True
            # Minor imbalance (1-2): partial — still returns count=1 but the LLM/scorer
            # handles partial via partial_allowed/partial_range on the rule definition.
            if imbalance <= 2:
                return 1, False  # count present but not balanced → FAIL, partial may be applied
            return 0, False

        # === CSS LAB VISUAL CHECKS ===
        if needle == "body_card_layout" or rule.id == "css.body_card_layout":
            # Count how many card-layout traits are present (5 total)
            traits = [
                "max-width" in content_lower,
                "margin: auto" in content_lower or "margin:auto" in content_lower or "0 auto" in content_lower,
                "padding" in content_lower,
                "box-shadow" in content_lower,
                "border-radius" in content_lower,
            ]
            count = sum(traits)
            # Return count of traits; rule min_count=1 so presence of any trait counts
            # but partial credit is applied for 2-3 traits by the LLM/partial system
            return count, count >= 4  # Full pass = 4+ traits

        if needle == "h1_styled" or rule.id == "css.h1_styled":
            # Check h1 is styled with colour, size or alignment
            has_h1_block = "h1" in content_lower
            has_color = "color" in content_lower
            has_size = "font-size" in content_lower or "font-weight" in content_lower
            count_traits = sum([has_h1_block and has_color, has_h1_block and has_size])
            count = 1 if has_h1_block and (has_color or has_size) else 0
            return count, count >= rule.min_count

        if needle == "table_profile_layout" or rule.id == "css.table_profile_layout":
            has_table = "table" in content_lower
            has_width = "max-width" in content_lower or ("width" in content_lower and "table" in content_lower)
            has_spacing = "border-spacing" in content_lower or "border-collapse" in content_lower
            count = 1 if (has_table and (has_width or has_spacing)) else 0
            return count, count >= rule.min_count

        if needle == "image_rounding_shadow" or rule.id == "css.image_rounding_shadow":
            has_radius = "border-radius" in content_lower
            has_shadow = "box-shadow" in content_lower
            count = sum([has_radius, has_shadow])
            return count, count >= rule.min_count  # min_count=0 so always passes

        if needle == "h2_section_style" or rule.id == "css.h2_section_style":
            has_h2 = "h2" in content_lower
            has_color = "color" in content_lower
            has_size = "font-size" in content_lower
            count = 1 if has_h2 and (has_color or has_size) else 0
            return count, count >= rule.min_count

        if needle == "list_readability_style" or rule.id == "css.list_readability_style":
            has_list_target = "ul" in content_lower or "li" in content_lower or "ol" in content_lower
            has_list_style = "list-style" in content_lower
            has_spacing = "padding" in content_lower or "margin" in content_lower
            count = 1 if has_list_target and (has_list_style or has_spacing) else 0
            return count, count >= rule.min_count

        if needle == "link_hover_style" or rule.id == "css.link_hover_style":
            # Check for a:hover or :hover rule
            has_hover = "a:hover" in content_lower or ":hover" in content_lower
            count = 1 if has_hover else 0
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
