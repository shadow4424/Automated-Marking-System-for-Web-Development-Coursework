from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import PHP as PID
from ams.core.profiles import ProfileSpec, RequiredPHPRule


class PHPRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required PHP features based on profile spec.
    
    Inherits common behavior from BaseRequiredAssessor:
    - File reading and error handling
    - Snippet extraction
    - Finding creation
    - Unified run() pipeline
    
    Implements PHP-specific:
    - Rule evaluation with superglobal, validation, security pattern matching
    - Message building
    - Finding ID mapping
    """

    name = "php_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        super().__init__(profile)
    
    @property
    def component_name(self) -> str:
        return "php"
    
    @property
    def required_rules(self) -> List[RequiredPHPRule]:
        return list(self.profile_spec.required_php)
    
    def _get_finding_id_pass(self) -> str:
        return PID.REQ_PASS
    
    def _get_finding_id_fail(self) -> str:
        return PID.REQ_FAIL
    
    def _get_finding_id_skipped(self) -> str:
        return PID.REQ_SKIPPED
    
    def _get_finding_id_missing_files(self) -> str:
        return PID.REQ_MISSING_FILES
    
    def _evaluate_rule_impl(
        self, rule: RequiredPHPRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single PHP rule against content.
        
        Args:
            rule: The PHP rule to evaluate
            content: Raw PHP content as string
            
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)
    
    def _evaluate_rule(
        self, rule: RequiredPHPRule, content_lower: str
    ) -> tuple[int, bool]:
        """Evaluate a single PHP rule and return count and pass status.
        
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === REQUEST SUPERGLOBALS ===
        if needle == "request_superglobal" or rule.id == "php.uses_request":
            patterns = ["$_get", "$_post", "$_request"]
            count = sum(content_lower.count(p) for p in patterns)
            return count, count >= rule.min_count
        
        # === VALIDATION ===
        if needle == "validation" or rule.id == "php.has_validation":
            validation_funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
            count = sum(1 for f in validation_funcs if f in content_lower)
            return count, count >= rule.min_count
        
        # === SANITISATION ===
        if needle == "sanitisation" or rule.id == "php.has_sanitisation":
            sanitise_funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
            count = sum(1 for f in sanitise_funcs if f in content_lower)
            return count, count >= rule.min_count
        
        # === OUTPUT ===
        if needle == "output" or rule.id == "php.outputs":
            count = content_lower.count("echo") + content_lower.count("print")
            return count, count >= rule.min_count
        
        # === DATABASE ===
        if needle == "database" or rule.id == "php.uses_database":
            db_patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
            count = sum(1 for p in db_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === PREPARED STATEMENTS ===
        if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
            prep_patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
            count = sum(1 for p in prep_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === SESSIONS ===
        if needle == "sessions" or rule.id == "php.uses_sessions":
            session_patterns = ["session_start", "$_session", "session_destroy"]
            count = sum(1 for p in session_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "php.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = "foreach" in content_lower
            count = sum([has_for, has_while, has_foreach])
            return count, count >= rule.min_count
        
        # === ERROR HANDLING ===
        if needle == "error_handling" or rule.id == "php.has_error_handling":
            error_patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
            count = sum(1 for p in error_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredPHPRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["PHPRequiredFeaturesAssessor"]



__all__ = ["PHPRequiredFeaturesAssessor"]
