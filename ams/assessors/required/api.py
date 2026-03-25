from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import API as AID
from ams.core.profiles import ProfileSpec, RequiredAPIRule


class APIRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required API features based on profile spec.

    Inherits common behaviour from BaseRequiredAssessor:
    - File reading and error handling
    - Snippet extraction
    - Finding creation
    - Unified run() pipeline

    Implements API-specific:
    - Rule evaluation scanning PHP and JS files for API patterns
    - Message building
    - Finding ID mapping
    """

    name = "api_required"

    def __init__(self, profile: str | ProfileSpec = "api_backed_web") -> None:
        super().__init__(profile)

    @property
    def component_name(self) -> str:
        return "api"

    @property
    def required_rules(self) -> List[RequiredAPIRule]:
        return list(self.profile_spec.required_api)

    def _get_finding_id_pass(self) -> str:
        return AID.REQ_PASS

    def _get_finding_id_fail(self) -> str:
        return AID.REQ_FAIL

    def _get_finding_id_skipped(self) -> str:
        return AID.REQ_SKIPPED

    def _get_finding_id_missing_files(self) -> str:
        return AID.REQ_MISSING_FILES

    def _evaluate_rule_impl(
        self, rule: RequiredAPIRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single API rule against content.

        Args:
            rule: The API rule to evaluate
            content: Raw file content as string

        Returns:
            Tuple of (count, passed)
        """
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)

    def _evaluate_rule(
        self, rule: RequiredAPIRule, content_lower: str
    ) -> Tuple[int, bool]:
        """Evaluate a single API rule and return count and pass status."""
        needle = rule.needle.lower()

        # === JSON ENCODING ===
        if needle == "json_encode":
            count = content_lower.count("json_encode(")
            return count, count >= rule.min_count

        # === JSON CONTENT TYPE HEADER ===
        if needle == "application/json":
            count = content_lower.count("application/json")
            return count, count >= rule.min_count

        # === METHOD ROUTING ===
        if needle in ("request_method", "method_routing"):
            count = content_lower.count("request_method")
            return count, count >= rule.min_count

        # === JSON INPUT PARSING ===
        if needle in ("json_decode", "php_input"):
            count = content_lower.count("json_decode(") + content_lower.count("php://input")
            return count, count >= rule.min_count

        # === FETCH API (JS client) ===
        if needle == "fetch":
            count = content_lower.count("fetch(") + content_lower.count("fetch (")
            return count, count >= rule.min_count

        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredAPIRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["APIRequiredFeaturesAssessor"]
