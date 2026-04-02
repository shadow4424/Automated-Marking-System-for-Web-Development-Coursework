from __future__ import annotations

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

        # JSON ENCODING.
        if needle == "json_encode":
            count = content_lower.count("json_encode(")
            return count, count >= rule.min_count

        # JSON CONTENT TYPE HEADER.
        if needle == "application/json":
            count = content_lower.count("application/json")
            return count, count >= rule.min_count

        # METHOD ROUTING.
        if needle in ("request_method", "method_routing"):
            count = content_lower.count("request_method")
            return count, count >= rule.min_count

        # JSON INPUT PARSING.
        if needle in ("json_decode", "php_input"):
            count = content_lower.count("json_decode(") + content_lower.count("php://input")
            return count, count >= rule.min_count

        # FETCH API (JS client).
        if needle == "fetch":
            count = content_lower.count("fetch(") + content_lower.count("fetch (")
            return count, count >= rule.min_count

        # ACCEPTS METHOD.
        if needle == "accepts_method" or rule.id == "api.accepts_method":
            has_request_method = "request_method" in content_lower
            has_in_array = "in_array" in content_lower and ("'get'" in content_lower or "'post'" in content_lower)
            count = 1 if (has_request_method or has_in_array) else 0
            return count, count >= rule.min_count

        # VALID JSON SHAPE.
        if needle == "valid_json_shape" or rule.id == "api.valid_json_shape":
            # Json_encode with an array/object literal (not a bare variable)
            has_json_encode = "json_encode(" in content_lower
            # Check for associative array arg: json_encode(['key' =>...]) or json_encode(array(...))
            has_array_arg = (
                ("json_encode([" in content_lower) or
                ("json_encode(array(" in content_lower) or
                ("json_encode(['" in content_lower) or
                ('json_encode(["' in content_lower)
            )
            count = 1 if (has_json_encode and has_array_arg) else 0
            return count, count >= rule.min_count

        # HTTP STATUS CODES.
        if needle == "http_status_codes" or rule.id == "api.http_status_codes":
            has_response_code = "http_response_code(" in content_lower
            has_header_http = "header(\"http/" in content_lower or "header('http/" in content_lower
            count = 1 if (has_response_code or has_header_http) else 0
            return count, count >= rule.min_count

        # ERROR RESPONSE PATH.
        if needle == "error_response_path" or rule.id == "api.error_response_path":
            # JSON error response: json_encode inside conditional/catch with an error key
            has_json_encode = "json_encode(" in content_lower
            has_error_key = ("'error'" in content_lower or '"error"' in content_lower or
                             "'message'" in content_lower or '"message"' in content_lower)
            has_condition = "if " in content_lower or "if(" in content_lower or "catch" in content_lower
            count = 1 if (has_json_encode and has_error_key and has_condition) else 0
            return count, count >= rule.min_count

        # STANDARD NEEDLE COUNTING.
        count = content_lower.count(needle)
        return count, count >= rule.min_count

__all__ = ["APIRequiredFeaturesAssessor"]
