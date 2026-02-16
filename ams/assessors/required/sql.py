from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.finding_ids import SQL as SID
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
                    id=SID.REQ_SKIPPED,
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

        # If SQL is not required for this profile, generate per-rule SKIPPED findings
        if not is_required:
            for rule in self.profile_spec.required_sql:
                findings.append(
                    Finding(
                        id=SID.REQ_SKIPPED,
                        category="sql",
                        message=f"Rule '{rule.id}' skipped: SQL not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_id": rule.id,
                            "description": rule.description,
                            "needle": rule.needle,
                            "weight": rule.weight,
                            "skip_reason": "component_not_required",
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=False,
                    )
                )
            return findings

        if not sql_files:
            if is_required and has_required_rules:
                # Generate per-rule FAIL findings for each required rule when files missing
                for rule in self.profile_spec.required_sql:
                    findings.append(
                        Finding(
                            id=SID.REQ_MISSING_FILES,
                            category="sql",
                            message=f"Rule '{rule.id}' not evaluated: No SQL files found.",
                            severity=Severity.FAIL,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "min_count": rule.min_count,
                                "weight": rule.weight,
                                "skip_reason": "no_sql_files_found",
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
                # Generate per-rule SKIPPED findings for each rule when not required
                for rule in self.profile_spec.required_sql:
                    findings.append(
                        Finding(
                            id=SID.REQ_SKIPPED,
                            category="sql",
                            message=f"Rule '{rule.id}' skipped: {rule.description}",
                            severity=Severity.SKIPPED,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "weight": rule.weight,
                                "skip_reason": "component_not_required" if not is_required else "no_sql_files_found",
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
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=SID.REQ_READ_ERROR,
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
                snippet = self._extract_snippet(rule, content, content_lower)
                findings.append(
                    Finding(
                        id=SID.REQ_PASS if passed else SID.REQ_FAIL,
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
                            "snippet": snippet,
                            "content": content[:500],
                        },
                        source=self.name,
                    )
                )
        return findings

    def _extract_snippet(self, rule, content: str, content_lower: str) -> str:
        """Extract a code snippet around the needle match (±5 lines).
        
        Args:
            rule: The rule being evaluated
            content: The original (case-preserving) file content
            content_lower: The lowercased content for pattern matching
            
        Returns:
            A string snippet with context lines around the match
        """
        needle = rule.needle.lower()
        lines = content.split('\n')
        
        # Try to find the needle in the content
        match_line_idx = -1
        for idx, line in enumerate(lines):
            if needle in line.lower():
                match_line_idx = idx
                break
        
        if match_line_idx == -1:
            # No match found, return first 10 lines as context
            return '\n'.join(lines[:10])
        
        # Extract ±5 lines around the match
        start = max(0, match_line_idx - 5)
        end = min(len(lines), match_line_idx + 6)
        snippet_lines = lines[start:end]
        return '\n'.join(snippet_lines)

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
