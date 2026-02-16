from __future__ import annotations

import re
from typing import List

from ams.assessors.base import Assessor
from ams.core.finding_ids import SQL as SID
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
                        id=SID.MISSING_FILES,
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
                        id=SID.SKIPPED,
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
                        id=SID.READ_ERROR,
                        category="sql",
                        message="Failed to read SQL file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            lowered = content.lower()
            # For SQL evidence, show the first 500 chars as a snippet
            evidence_snippet = content[:500] + ("..." if len(content) > 500 else "")
            
            evidence_finding = Finding(
                id=SID.EVIDENCE,
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
                    "snippet": evidence_snippet,
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
                "snippet": evidence_snippet,
            }

            if non_empty_lines == 0:
                findings.append(
                    Finding(
                        id=SID.EMPTY,
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
                        id=SID.NO_SEMICOLONS,
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
                        id=SID.STRUCTURE_OK,
                        category="sql",
                        message="SQL structure heuristics look OK.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )

            # Security Checks
            # 1. Detect dynamic SQL without sanitisation (look for string concatenation in queries)
            # This is a simplified check - look for SELECT/INSERT/UPDATE with + or . (concatenation)
            dynamic_sql_patterns = [
                r'select\s+.*[+\'"`]',  # SELECT with concatenation
                r'insert\s+.*[+\'"`]',  # INSERT with concatenation
                r'update\s+.*[+\'"`]',  # UPDATE with concatenation
            ]
            dynamic_sql_found = False
            dynamic_snippet = ""
            for pattern in dynamic_sql_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    dynamic_sql_found = True
                    dynamic_snippet = self._extract_snippet(content, match.group(0))
                    break
            
            if dynamic_sql_found:
                findings.append(
                    Finding(
                        id=SID.SECURITY_DYNAMIC_SQL,
                        category="sql",
                        message="Dynamic SQL with string concatenation detected. Use parameterized queries/prepared statements to prevent SQL injection.",
                        severity=Severity.FAIL,
                        evidence={
                            "path": str(path),
                            "dynamic_sql_detected": True,
                            "snippet": dynamic_snippet,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 2. Flag use of SELECT *
            select_star_count = lowered.count("select *")
            if select_star_count > 0:
                star_snippet = self._extract_snippet(content, "select *")
                findings.append(
                    Finding(
                        id=SID.QUALITY_SELECT_STAR,
                        category="sql",
                        message=f"Found {select_star_count} use(s) of SELECT *. Specify columns explicitly for better performance and maintainability.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "select_star_count": select_star_count,
                            "snippet": star_snippet,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 3. Require LIMIT for user-controlled queries
            # Check for SELECT statements that might be user-controlled (have WHERE with variables)
            # This is a heuristic - look for SELECT without LIMIT
            select_statements = re.findall(r'select\s+.*?from\s+.*?(?:where\s+.*?)?(?:order\s+by\s+.*?)?(?:limit\s+.*?)?;', content, re.IGNORECASE | re.DOTALL)
            selects_without_limit = []
            limit_snippets = []
            for i, stmt in enumerate(select_statements):
                if "limit" not in stmt.lower() and "where" in stmt.lower():
                    # Might be user-controlled if it has WHERE
                    selects_without_limit.append(i + 1)
                    limit_snippets.append(stmt.strip())
            
            if selects_without_limit:
                findings.append(
                    Finding(
                        id=SID.SECURITY_MISSING_LIMIT,
                        category="sql",
                        message=f"Found {len(selects_without_limit)} SELECT statement(s) with WHERE clause but no LIMIT. Add LIMIT to prevent excessive data retrieval.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "selects_without_limit": len(selects_without_limit),
                            "snippet": limit_snippets[0] if limit_snippets else "",
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

        return findings

    def _extract_snippet(self, content: str, needle: str, context_lines: int = 2) -> str:
        """Extract a snippet of code surrounding the needle."""
        try:
            lines = content.splitlines()
            lower_needle = needle.lower()
            if not lower_needle:
                 # If needle is regex match, use it directly to find line
                 pass 

            # Simple line-based search
            for i, line in enumerate(lines):
                # We do loose matching because needle might be a multiline string from regex
                if needle in line or needle.lower() in line.lower() or (len(needle) > 20 and line.strip() in needle):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    
                    snippet = []
                    for j in range(start, end):
                        prefix = "> " if j == i else "  "
                        snippet.append(f"{j+1:3d} | {lines[j]}")
                    return "\n".join(snippet)
            
            # Fallback for multiline match that wasn't found line-by-line
            if needle in content:
                # Find index
                idx = content.find(needle)
                # Find line number...
                # This is sufficient for now
                return needle
                
            return ""
        except Exception:
            return ""


__all__ = ["SQLStaticAssessor"]
