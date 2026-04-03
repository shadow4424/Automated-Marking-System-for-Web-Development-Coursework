from __future__ import annotations

from collections.abc import Callable
from typing import Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import SQL as SID
from ams.core.profiles import RequiredSQLRule


class SQLRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required SQL features based on profile spec."""

    _component = "sql"
    _finding_ids_class = SID

    _FOREIGN_KEY_PATTERNS = ["foreign key", "references "]
    _CONSTRAINT_PATTERNS = ["not null", "unique", "check ", "default "]
    _DATA_TYPES = ["int", "varchar", "text", "date", "datetime", "boolean", "decimal", "float", "char(", "timestamp"]
    _AGGREGATE_PATTERNS = ["count(", "sum(", "avg(", "min(", "max(", "group by"]

    def _evaluate_rule_impl(
        self, rule: RequiredSQLRule, content: str
    ) -> Tuple[int, bool]:
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)

    def _evaluate_rule(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        """Evaluate a single SQL rule against the file content."""
        needle = rule.needle.lower()

        direct_matchers: dict[str, Callable[[RequiredSQLRule, str], tuple[int, bool]]] = {
            "foreign_key": self._match_foreign_key,
            "constraints": self._match_constraints,
            "data_types": self._match_data_types,
            "aggregate": self._match_aggregate,
            "parses_cleanly": self._match_parses_cleanly,
        }
        rule_id_matchers: dict[str, Callable[[RequiredSQLRule, str], tuple[int, bool]]] = {
            "sql.has_foreign_key": self._match_foreign_key,
            "sql.has_constraints": self._match_constraints,
            "sql.has_data_types": self._match_data_types,
            "sql.has_aggregate": self._match_aggregate,
            "sql.parses_cleanly": self._match_parses_cleanly,
        }

        matcher = direct_matchers.get(needle) or rule_id_matchers.get(rule.id)
        if matcher:
            return matcher(rule, content_lower)

        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _match_foreign_key(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for pattern in self._FOREIGN_KEY_PATTERNS if pattern in content_lower)
        return count, count >= rule.min_count

    def _match_constraints(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for pattern in self._CONSTRAINT_PATTERNS if pattern in content_lower)
        return count, count >= rule.min_count

    def _match_data_types(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for data_type in self._DATA_TYPES if data_type in content_lower)
        return count, count >= rule.min_count

    def _match_aggregate(self, rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        count = sum(1 for pattern in self._AGGREGATE_PATTERNS if pattern in content_lower)
        return count, count >= rule.min_count

    @staticmethod
    def _match_parses_cleanly(rule: RequiredSQLRule, content_lower: str) -> tuple[int, bool]:
        has_semicolons = ";" in content_lower
        has_statements = "create table" in content_lower or "select " in content_lower or "insert " in content_lower
        open_parens = content_lower.count("(")
        close_parens = content_lower.count(")")
        parens_balanced = abs(open_parens - close_parens) <= 2
        if not has_semicolons or not has_statements:
            return 0, False
        count = 1 if parens_balanced else 0
        return count, count >= rule.min_count

__all__ = ["SQLRequiredFeaturesAssessor"]
