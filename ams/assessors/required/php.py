from __future__ import annotations

from collections.abc import Callable
from typing import Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import PHP as PID
from ams.core.profiles import RequiredPHPRule


class PHPRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required PHP features based on profile spec."""

    _component = "php"
    _finding_ids_class = PID

    _REQUEST_PATTERNS = ["$_get", "$_post", "$_request"]
    _VALIDATION_FUNCS = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
    _SANITISE_FUNCS = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
    _DB_PATTERNS = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
    _PREPARED_PATTERNS = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
    _SESSION_PATTERNS = ["session_start", "$_session", "session_destroy"]
    _ERROR_PATTERNS = ["try", "catch", "error_reporting", "set_error_handler", "exception"]

    def _evaluate_rule_impl(
        self, rule: RequiredPHPRule, content: str
    ) -> Tuple[int, bool]:
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)

    def _evaluate_rule(
        self, rule: RequiredPHPRule, content_lower: str
    ) -> tuple[int, bool]:
        """Evaluate a single PHP rule and return count and pass status."""
        needle = rule.needle.lower()

        direct_matchers: dict[str, Callable[[RequiredPHPRule, str], tuple[int, bool]]] = {
            "request_superglobal": self._match_request_superglobal,
            "validation": self._match_validation,
            "sanitisation": self._match_sanitisation,
            "output": self._match_output,
            "database": self._match_database,
            "prepared_statements": self._match_prepared,
            "sessions": self._match_sessions,
            "loops": self._match_loops,
            "error_handling": self._match_error_handling,
            "response_path_complete": self._match_response_path_complete,
        }
        rule_id_matchers: dict[str, Callable[[RequiredPHPRule, str], tuple[int, bool]]] = {
            "php.uses_request": self._match_request_superglobal,
            "php.has_validation": self._match_validation,
            "php.has_sanitisation": self._match_sanitisation,
            "php.outputs": self._match_output,
            "php.uses_database": self._match_database,
            "php.uses_prepared_statements": self._match_prepared,
            "php.uses_sessions": self._match_sessions,
            "php.has_loops": self._match_loops,
            "php.has_error_handling": self._match_error_handling,
            "php.response_path_complete": self._match_response_path_complete,
        }
        matcher = direct_matchers.get(needle) or rule_id_matchers.get(rule.id)
        if matcher:
            return matcher(rule, content_lower)

        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _match_request_superglobal(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(content_lower.count(p) for p in self._REQUEST_PATTERNS)
        return count, count >= rule.min_count

    def _match_validation(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for f in self._VALIDATION_FUNCS if f in content_lower)
        return count, count >= rule.min_count

    def _match_sanitisation(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for f in self._SANITISE_FUNCS if f in content_lower)
        return count, count >= rule.min_count

    def _match_output(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = content_lower.count("echo") + content_lower.count("print")
        return count, count >= rule.min_count

    def _match_database(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for p in self._DB_PATTERNS if p in content_lower)
        return count, count >= rule.min_count

    def _match_prepared(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for p in self._PREPARED_PATTERNS if p in content_lower)
        return count, count >= rule.min_count

    def _match_sessions(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for p in self._SESSION_PATTERNS if p in content_lower)
        return count, count >= rule.min_count

    @staticmethod
    def _match_loops(rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        has_for = "for " in content_lower or "for(" in content_lower
        has_while = "while " in content_lower or "while(" in content_lower
        has_foreach = "foreach" in content_lower
        count = sum([has_for, has_while, has_foreach])
        return count, count >= rule.min_count

    def _match_error_handling(self, rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for p in self._ERROR_PATTERNS if p in content_lower)
        return count, count >= rule.min_count

    @staticmethod
    def _match_response_path_complete(rule: RequiredPHPRule, content_lower: str) -> tuple[int, bool]:
        has_input = "$_post" in content_lower or "$_get" in content_lower or "$_request" in content_lower
        has_processing = "isset(" in content_lower or "if " in content_lower or "if(" in content_lower
        has_output = "echo" in content_lower or "print" in content_lower or "json_encode(" in content_lower
        count = sum([has_input, has_processing, has_output])
        return count, count >= rule.min_count

__all__ = ["PHPRequiredFeaturesAssessor"]
