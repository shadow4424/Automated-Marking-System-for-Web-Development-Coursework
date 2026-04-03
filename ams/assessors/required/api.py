from __future__ import annotations

from collections.abc import Callable
from typing import Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import API as AID
from ams.core.profiles import RequiredAPIRule


class APIRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required API features based on profile spec."""

    _component = "api"
    _finding_ids_class = AID
    _default_profile = "api_backed_web"

    def _evaluate_rule_impl(
        self, rule: RequiredAPIRule, content: str
    ) -> Tuple[int, bool]:
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)

    def _evaluate_rule(
        self, rule: RequiredAPIRule, content_lower: str
    ) -> Tuple[int, bool]:
        """Evaluate a single API rule and return count and pass status."""
        needle = rule.needle.lower()
        direct_matchers: dict[str, Callable[[RequiredAPIRule, str], tuple[int, bool]]] = {
            "json_encode": self._match_json_encode,
            "application/json": self._match_content_type_json,
            "request_method": self._match_request_method,
            "method_routing": self._match_request_method,
            "json_decode": self._match_json_input,
            "php_input": self._match_json_input,
            "fetch": self._match_fetch,
        }
        matcher = direct_matchers.get(needle)
        if matcher:
            return matcher(rule, content_lower)

        if needle == "accepts_method" or rule.id == "api.accepts_method":
            return self._match_accepts_method(rule, content_lower)
        if needle == "valid_json_shape" or rule.id == "api.valid_json_shape":
            return self._match_valid_json_shape(rule, content_lower)
        if needle == "http_status_codes" or rule.id == "api.http_status_codes":
            return self._match_http_status_codes(rule, content_lower)
        if needle == "error_response_path" or rule.id == "api.error_response_path":
            return self._match_error_response_path(rule, content_lower)

        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _match_json_encode(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("json_encode(")
        return count, count >= rule.min_count

    def _match_content_type_json(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("application/json")
        return count, count >= rule.min_count

    def _match_request_method(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("request_method")
        return count, count >= rule.min_count

    def _match_json_input(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("json_decode(") + content_lower.count("php://input")
        return count, count >= rule.min_count

    def _match_fetch(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("fetch(") + content_lower.count("fetch (")
        return count, count >= rule.min_count

    def _match_accepts_method(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        has_request_method = "request_method" in content_lower
        has_in_array = "in_array" in content_lower and ("'get'" in content_lower or "'post'" in content_lower)
        count = 1 if (has_request_method or has_in_array) else 0
        return count, count >= rule.min_count

    def _match_valid_json_shape(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        has_json_encode = "json_encode(" in content_lower
        has_array_arg = (
            ("json_encode([" in content_lower)
            or ("json_encode(array(" in content_lower)
            or ("json_encode(['" in content_lower)
            or ('json_encode(["' in content_lower)
        )
        count = 1 if (has_json_encode and has_array_arg) else 0
        return count, count >= rule.min_count

    def _match_http_status_codes(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        has_response_code = "http_response_code(" in content_lower
        has_header_http = "header(\"http/" in content_lower or "header('http/" in content_lower
        count = 1 if (has_response_code or has_header_http) else 0
        return count, count >= rule.min_count

    def _match_error_response_path(self, rule: RequiredAPIRule, content_lower: str) -> tuple[int, bool]:
        has_json_encode = "json_encode(" in content_lower
        has_error_key = (
            "'error'" in content_lower
            or '"error"' in content_lower
            or "'message'" in content_lower
            or '"message"' in content_lower
        )
        has_condition = "if " in content_lower or "if(" in content_lower or "catch" in content_lower
        count = 1 if (has_json_encode and has_error_key and has_condition) else 0
        return count, count >= rule.min_count

__all__ = ["APIRequiredFeaturesAssessor"]
