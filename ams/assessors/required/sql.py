from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec


class SQLRequiredFeaturesAssessor(Assessor):
    """Checks required SQL features based on profile spec."""

    name = "sql_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> list[Finding]:
        findings: list[Finding] = []
        sql_files = sorted(context.discovered_files.get("sql", []))

        if not self.profile_spec.required_sql:
            findings.append(
                Finding(
                    id="SQL.REQ.SKIPPED",
                    category="sql",
                    message="No required SQL rules defined for this profile; skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"rule_ids": []},
                    source=self.name,
                )
            )
            return findings

        # Check if SQL is required for this profile
        is_required = self.profile_spec.is_component_required("sql")
        has_required_rules = self.profile_spec.has_required_rules("sql")

        if not sql_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="SQL.REQ.MISSING_FILES",
                        category="sql",
                        message="No SQL files found; SQL is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_sql],
                            "needles": [r.needle for r in self.profile_spec.required_sql],
                            "discovered_count": 0,
                            "profile": self.profile_spec.name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=self.profile_spec.name,
                        required=True,
                    )
                )
            else:
                # Not required or no rules defined, skip
                findings.append(
                    Finding(
                        id="SQL.REQ.SKIPPED",
                        category="sql",
                        message="No SQL files found; SQL required checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_sql],
                            "needles": [r.needle for r in self.profile_spec.required_sql],
                            "discovered_count": 0,
                            "profile": self.profile_spec.name,
                            "required": is_required,
                            "has_required_rules": has_required_rules,
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=is_required,
                    )
                )
            return findings

        for path in sql_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError as exc:
                findings.append(
                    Finding(
                        id="SQL.REQ.READ_ERROR",
                        category="sql",
                        message="Failed to read SQL file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            content_lower = content.lower()
            for rule in self.profile_spec.required_sql:
                count, passed = self._evaluate_rule(rule, content_lower)
                findings.append(
                    Finding(
                        id="SQL.REQ.PASS" if passed else "SQL.REQ.FAIL",
                        category="sql",
                        message=self._message(rule.id, passed, count, rule.min_count),
                        severity=Severity.INFO if passed else Severity.WARN,
                        evidence={
                            "path": str(path),
                            "rule_id": rule.id,
                            "needle": rule.needle,
                            "min_count": rule.min_count,
                            "count": count,
                            "weight": rule.weight,
                        },
                        source=self.name,
                    )
                )
        return findings

    def _evaluate_rule(self, rule, content_lower: str) -> tuple[int, bool]:
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
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["SQLRequiredFeaturesAssessor"]
