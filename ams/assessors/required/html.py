"""HTML Required Elements Assessor — checks for required HTML structure and semantics."""
from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.assessors.html_parser import TagCountingParser
from ams.core.finding_ids import HTML as HID
from ams.core.profiles import ProfileSpec, RequiredRule


class HTMLRequiredElementsAssessor(BaseRequiredAssessor):
    """Checks required HTML elements based on profile spec using a real HTML parser."""

    name = "html_required"

    # Initialise the HTML required assessor.
    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        super().__init__(profile)

    # Return the component name.
    @property
    def component_name(self) -> str:
        return "html"

    # Return the required rules for this component.
    @property
    def required_rules(self) -> List[RequiredRule]:
        return list(self.profile_spec.required_html)

    # Return the pass finding id.
    def _get_finding_id_pass(self) -> str:
        return HID.REQ_PASS

    # Return the fail finding id.
    def _get_finding_id_fail(self) -> str:
        return HID.REQ_FAIL

    # Return the skipped finding id.
    def _get_finding_id_skipped(self) -> str:
        return HID.REQ_SKIPPED

    # Return the missing-files finding id.
    def _get_finding_id_missing_files(self) -> str:
        return HID.REQ_MISSING_FILES

    def _evaluate_rule_impl(
        self, rule: RequiredRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single HTML rule against content."""
        parser = TagCountingParser()
        parser.feed(content)
        return self._evaluate_rule(rule, parser)

    def _evaluate_rule(
        self, rule: RequiredRule, parser: TagCountingParser
    ) -> tuple[int, bool]:
        """Evaluate a single rule against the parsed HTML content."""
        selector = rule.selector.lower()

        # DOCTYPE.
        if selector == "!doctype" or rule.id == "html.has_doctype":
            count = 1 if parser.has_doctype else 0
            return count, count >= rule.min_count

        # SEMANTIC STRUCTURE.
        if selector == "semantic" or rule.id == "html.has_semantic_structure":
            count = 1 if parser.has_semantic else 0
            return count, count >= rule.min_count

        # HEADING HIERARCHY.
        if selector == "heading" or rule.id == "html.has_heading_hierarchy":
            count = 1 if parser.has_heading else 0
            return count, count >= rule.min_count

        # LIST ELEMENTS.
        if selector == "list" or rule.id == "html.has_lists":
            count = 1 if parser.has_list else 0
            return count, count >= rule.min_count

        # META CHARSET.
        if selector == "meta_charset" or rule.id == "html.has_meta_charset":
            count = 1 if parser.has_meta_charset else 0
            return count, count >= rule.min_count

        # META VIEWPORT.
        if selector == "meta_viewport" or rule.id == "html.has_meta_viewport":
            count = 1 if parser.has_meta_viewport else 0
            return count, count >= rule.min_count

        # HTML LANG ATTRIBUTE.
        if selector == "html_lang" or rule.id == "html.has_lang_attribute":
            count = 1 if parser.has_html_lang else 0
            return count, count >= rule.min_count

        # IMAGE ALT ATTRIBUTES.
        if selector == "img_alt" or rule.id == "html.has_alt_attributes":
            # Pass if all images have alt attributes, or if no images exist
            if parser.img_count == 0:
                return 1, True  # No images means requirement is satisfied
            count = parser.img_with_alt
            passed = parser.img_with_alt == parser.img_count
            return count, passed

        # LABELS.
        if selector == "label" or rule.id == "html.has_labels":
            count = parser.label_count
            return count, count >= rule.min_count

        # IMAGE ELEMENT.
        if selector == "img" or rule.id == "html.has_image":
            count = parser.img_count
            return count, count >= rule.min_count

        # STYLESHEET LINKAGE.
        if selector == "link_stylesheet" or rule.id == "html.links_stylesheet":
            count = parser.link_stylesheet_count
            return count, count >= rule.min_count

        # SCRIPT LINKAGE.
        if selector == "link_script" or rule.id == "html.links_script_or_js":
            count = parser.script_count
            return count, count >= rule.min_count

        # STANDARD TAG COUNTING.
        # For simple selectors like html, head, body, title, form, input, a, table
        count = parser.counts.get(selector, 0)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} ({rule.description}) {status}: found {count}, required {rule.min_count}"


__all__ = ["HTMLRequiredElementsAssessor"]
