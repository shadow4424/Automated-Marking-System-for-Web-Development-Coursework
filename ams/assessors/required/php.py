from __future__ import annotations

from typing import Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import PHP as PID
from ams.core.profiles import RequiredPHPRule


class PHPRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required PHP features based on profile spec."""

    _component = "php"
    _finding_ids_class = PID

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

        # REQUEST SUPERGLOBALS.
        if needle == "request_superglobal" or rule.id == "php.uses_request":
            patterns = ["$_get", "$_post", "$_request"]
            count = sum(content_lower.count(p) for p in patterns)
            return count, count >= rule.min_count

        # VALIDATION.
        if needle == "validation" or rule.id == "php.has_validation":
            validation_funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
            count = sum(1 for f in validation_funcs if f in content_lower)
            return count, count >= rule.min_count

        # SANITISATION.
        if needle == "sanitisation" or rule.id == "php.has_sanitisation":
            sanitise_funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
            count = sum(1 for f in sanitise_funcs if f in content_lower)
            return count, count >= rule.min_count

        # OUTPUT.
        if needle == "output" or rule.id == "php.outputs":
            count = content_lower.count("echo") + content_lower.count("print")
            return count, count >= rule.min_count

        # DATABASE.
        if needle == "database" or rule.id == "php.uses_database":
            db_patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
            count = sum(1 for p in db_patterns if p in content_lower)
            return count, count >= rule.min_count

        # PREPARED STATEMENTS.
        if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
            prep_patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
            count = sum(1 for p in prep_patterns if p in content_lower)
            return count, count >= rule.min_count

        # SESSIONS.
        if needle == "sessions" or rule.id == "php.uses_sessions":
            session_patterns = ["session_start", "$_session", "session_destroy"]
            count = sum(1 for p in session_patterns if p in content_lower)
            return count, count >= rule.min_count

        # LOOPS.
        if needle == "loops" or rule.id == "php.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = "foreach" in content_lower
            count = sum([has_for, has_while, has_foreach])
            return count, count >= rule.min_count

        # ERROR HANDLING.
        if needle == "error_handling" or rule.id == "php.has_error_handling":
            error_patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
            count = sum(1 for p in error_patterns if p in content_lower)
            return count, count >= rule.min_count

        # RESPONSE PATH COMPLETE.
        if needle == "response_path_complete" or rule.id == "php.response_path_complete":
            # Check all three stages of a complete request-response path
            has_input = "$_post" in content_lower or "$_get" in content_lower or "$_request" in content_lower
            has_processing = "isset(" in content_lower or "if " in content_lower or "if(" in content_lower
            has_output = "echo" in content_lower or "print" in content_lower or "json_encode(" in content_lower
            count = sum([has_input, has_processing, has_output])
            return count, count >= rule.min_count  # Min_count=2 → needs input + at least one other

        # STANDARD NEEDLE COUNTING.
        count = content_lower.count(needle)
        return count, count >= rule.min_count

__all__ = ["PHPRequiredFeaturesAssessor"]
