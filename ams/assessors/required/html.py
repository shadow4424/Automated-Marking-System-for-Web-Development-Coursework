"""HTML Required Elements Assessor — checks for required HTML structure and semantics."""
from __future__ import annotations

from collections.abc import Callable
from typing import Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.assessors.html_parser import TagCountingParser
from ams.core.finding_ids import HTML as HID
from ams.core.profiles import RequiredRule


class HTMLRequiredElementsAssessor(BaseRequiredAssessor):
    """Checks required HTML elements based on profile spec using a real HTML parser."""

    _component = "html"
    _finding_ids_class = HID

    def _evaluate_rule_impl(
        self, rule: RequiredRule, content: str
    ) -> Tuple[int, bool]:
        parser = TagCountingParser()
        parser.feed(content)
        return self._evaluate_rule(rule, parser)

    def _evaluate_rule(
        self, rule: RequiredRule, parser: TagCountingParser
    ) -> tuple[int, bool]:
        """Evaluate a single rule against the parsed HTML content."""
        selector = rule.selector.lower()
        bool_matchers: dict[str, Callable[[TagCountingParser], bool]] = {
            "!doctype": lambda p: p.has_doctype,
            "semantic": lambda p: p.has_semantic,
            "heading": lambda p: p.has_heading,
            "list": lambda p: p.has_list,
            "meta_charset": lambda p: p.has_meta_charset,
            "meta_viewport": lambda p: p.has_meta_viewport,
            "html_lang": lambda p: p.has_html_lang,
        }
        if selector in bool_matchers:
            return self._boolean_check(rule, bool_matchers[selector](parser))

        if rule.id == "html.has_doctype":
            return self._boolean_check(rule, parser.has_doctype)
        if rule.id == "html.has_semantic_structure":
            return self._boolean_check(rule, parser.has_semantic)
        if rule.id == "html.has_heading_hierarchy":
            return self._boolean_check(rule, parser.has_heading)
        if rule.id == "html.has_lists":
            return self._boolean_check(rule, parser.has_list)
        if rule.id == "html.has_meta_charset":
            return self._boolean_check(rule, parser.has_meta_charset)
        if rule.id == "html.has_meta_viewport":
            return self._boolean_check(rule, parser.has_meta_viewport)
        if rule.id == "html.has_lang_attribute":
            return self._boolean_check(rule, parser.has_html_lang)

        if selector == "img_alt" or rule.id == "html.has_alt_attributes":
            if parser.img_count == 0:
                return 1, True
            count = parser.img_with_alt
            passed = parser.img_with_alt == parser.img_count
            return count, passed

        if selector == "label" or rule.id == "html.has_labels":
            return self._count_check(rule, parser.label_count)
        if selector == "img" or rule.id == "html.has_image":
            return self._count_check(rule, parser.img_count)
        if selector == "link_stylesheet" or rule.id == "html.links_stylesheet":
            return self._count_check(rule, parser.link_stylesheet_count)
        if selector == "link_script" or rule.id == "html.links_script_or_js":
            return self._count_check(rule, parser.script_count)

        count = parser.counts.get(selector, 0)
        return self._count_check(rule, count)

    @staticmethod
    def _boolean_check(rule: RequiredRule, flag: bool) -> tuple[int, bool]:
        count = 1 if flag else 0
        return count, count >= rule.min_count

    @staticmethod
    def _count_check(rule: RequiredRule, count: int) -> tuple[int, bool]:
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredRule, passed: bool, count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} ({rule.description}) {status}: found {count}, required {rule.min_count}"


__all__ = ["HTMLRequiredElementsAssessor"]
