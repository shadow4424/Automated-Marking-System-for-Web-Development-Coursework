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
            for rule in self.profile_spec.required_sql:
                count = content.count(rule.needle.lower())
                passed = count >= rule.min_count
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
                        },
                        source=self.name,
                    )
                )
        return findings

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["SQLRequiredFeaturesAssessor"]
