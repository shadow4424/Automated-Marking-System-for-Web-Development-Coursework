from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class SQLStaticAssessor(Assessor):
    """Deterministic SQL static checks focused on file presence and basic heuristics."""

    name = "sql_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        sql_files = sorted(context.discovered_files.get("sql", []))
        
        # Determine if SQL is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("sql")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not sql_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="SQL.MISSING_FILES",
                        category="sql",
                        message="No SQL files found; SQL is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".sql"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=profile_name,
                        required=True,
                    )
                )
            else:
                # Not required for profile, skip
                findings.append(
                    Finding(
                        id="SQL.SKIPPED",
                        category="sql",
                        message="No SQL files found; SQL is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".sql"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": False,
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=profile_name,
                        required=False,
                    )
                )
            return findings

        for path in sql_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id="SQL.READ_ERROR",
                        category="sql",
                        message="Failed to read SQL file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            lowered = content.lower()
            evidence_finding = Finding(
                id="SQL.EVIDENCE",
                category="sql",
                message="SQL evidence collected.",
                severity=Severity.INFO,
                evidence={
                    "path": str(path),
                    "create_table": lowered.count("create table"),
                    "insert_into": lowered.count("insert into"),
                    "select": lowered.count("select "),
                    "update": lowered.count("update "),
                    "delete": lowered.count("delete "),
                    "join": lowered.count(" join "),
                    "where": lowered.count(" where "),
                    "semicolons": content.count(";"),
                    "non_empty_lines": sum(1 for line in content.splitlines() if line.strip()),
                },
                source=self.name,
            )
            findings.append(evidence_finding)

            semicolons = evidence_finding.evidence["semicolons"]
            non_empty_lines = evidence_finding.evidence["non_empty_lines"]
            structure_evidence = {
                "path": str(path),
                "semicolons": semicolons,
                "non_empty_lines": non_empty_lines,
            }

            if non_empty_lines == 0:
                findings.append(
                    Finding(
                        id="SQL.EMPTY",
                        category="sql",
                        message="SQL file appears empty.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            elif semicolons == 0 and non_empty_lines > 0:
                findings.append(
                    Finding(
                        id="SQL.NO_SEMICOLONS",
                        category="sql",
                        message="No semicolons found; SQL may be incomplete.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id="SQL.STRUCTURE_OK",
                        category="sql",
                        message="SQL structure heuristics look OK.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )

        return findings


__all__ = ["SQLStaticAssessor"]
