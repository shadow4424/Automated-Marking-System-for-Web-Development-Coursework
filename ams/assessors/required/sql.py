from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import SQL as SID
from ams.core.profiles import ProfileSpec, RequiredSQLRule


class SQLRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required SQL features based on profile spec.
    
    Inherits common behaviour from BaseRequiredAssessor:
    - File reading and error handling
    - Snippet extraction
    - Finding creation
    - Unified run() pipeline
    
    Implements SQL-specific:
    - Rule evaluation with constraint, key, and aggregate pattern matching
    - Message building
    - Finding ID mapping
    """

    name = "sql_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        super().__init__(profile)
    
    @property
    def component_name(self) -> str:
        return "sql"
    
    @property
    def required_rules(self) -> List[RequiredSQLRule]:
        return list(self.profile_spec.required_sql)
    
    def _get_finding_id_pass(self) -> str:
        return SID.REQ_PASS
    
    def _get_finding_id_fail(self) -> str:
        return SID.REQ_FAIL
    
    def _get_finding_id_skipped(self) -> str:
        return SID.REQ_SKIPPED
    
    def _get_finding_id_missing_files(self) -> str:
        return SID.REQ_MISSING_FILES
    
    def _evaluate_rule_impl(
        self, rule: RequiredSQLRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single SQL rule against content.
        
        Args:
            rule: The SQL rule to evaluate
            content: Raw SQL content as string
            
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)
    
    def _evaluate_rule(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        """Evaluate a single SQL rule against the file content.
        
        Returns:
            A tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the rule requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === FOREIGN KEY ===
        if needle == "foreign_key" or rule.id == "sql.has_foreign_key":
            fk_patterns = ["foreign key", "references "]
            count = sum(1 for p in fk_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === CONSTRAINTS ===
        if needle == "constraints" or rule.id == "sql.has_constraints":
            constraint_patterns = ["not null", "unique", "check ", "default "]
            count = sum(1 for p in constraint_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === DATA TYPES ===
        if needle == "data_types" or rule.id == "sql.has_data_types":
            data_types = ["int", "varchar", "text", "date", "datetime", "boolean", 
                         "decimal", "float", "char(", "timestamp"]
            count = sum(1 for t in data_types if t in content_lower)
            return count, count >= rule.min_count
        
        # === AGGREGATE ===
        if needle == "aggregate" or rule.id == "sql.has_aggregate":
            agg_patterns = ["count(", "sum(", "avg(", "min(", "max(", "group by"]
            count = sum(1 for p in agg_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === PARSES CLEANLY ===
        if needle == "parses_cleanly" or rule.id == "sql.parses_cleanly":
            # A valid SQL file should have semicolons and CREATE TABLE or SELECT statements
            has_semicolons = ";" in content_lower
            has_statements = "create table" in content_lower or "select " in content_lower or "insert " in content_lower
            # Check for balanced parentheses as a proxy for structural integrity
            open_parens = content_lower.count("(")
            close_parens = content_lower.count(")")
            parens_balanced = abs(open_parens - close_parens) <= 2
            if not has_semicolons or not has_statements:
                return 0, False
            count = 1 if parens_balanced else 0
            return count, count >= rule.min_count

        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredSQLRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["SQLRequiredFeaturesAssessor"]
