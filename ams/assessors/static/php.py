from __future__ import annotations

import re
from typing import List

from ams.assessors.base import Assessor
from ams.core.finding_ids import PHP as PID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class PHPStaticAssessor(Assessor):
    """Deterministic PHP static checks using lightweight heuristics."""

    name = "php_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        php_files = sorted(context.discovered_files.get("php", []))
        
        # Determine if PHP is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("php")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not php_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id=PID.MISSING_FILES,
                        category="php",
                        message="No PHP files found; PHP is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".php"],
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
                        id=PID.SKIPPED,
                        category="php",
                        message="No PHP files found; PHP is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".php"],
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

        for path in php_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=PID.READ_ERROR,
                        category="php",
                        message="Failed to read PHP file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            lowered = content.lower()
            has_open_tag = "<?php" in lowered
            has_close_tag = "?>" in lowered

            findings.append(
                Finding(
                    id=PID.TAG_OK if has_open_tag else PID.TAG_MISSING,
                    category="php",
                    message=(
                        "PHP opening tag found." if has_open_tag else "PHP opening tag not found; file may not be valid PHP."
                    ),
                    severity=Severity.INFO if has_open_tag else Severity.WARN,
                    evidence={"path": str(path), "has_open_tag": has_open_tag, "has_close_tag": has_close_tag},
                    source=self.name,
                )
            )

            open_braces = content.count("{")
            close_braces = content.count("}")
            open_parens = content.count("(")
            close_parens = content.count(")")
            parens_balanced = open_parens == close_parens
            braces_balanced = open_braces == close_braces
            has_semicolons = ";" in content

            syntax_evidence = {
                "path": str(path),
                "open_braces": open_braces,
                "close_braces": close_braces,
                "braces_balanced": braces_balanced,
                "parens_balanced": parens_balanced,
                "has_semicolons": has_semicolons,
            }

            stripped_content = content.strip()
            stripped_lower = lowered.strip()
            tag_stripped = stripped_lower.replace("<?php", "").replace("?>", "").strip()
            only_tags = not tag_stripped

            if braces_balanced and parens_balanced and (open_braces + open_parens) > 0:
                findings.append(
                    Finding(
                        id=PID.SYNTAX_OK,
                        category="php",
                        message="PHP syntax heuristics look OK.",
                        severity=Severity.INFO,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            elif len(stripped_content) < 5 or only_tags:
                findings.append(
                    Finding(
                        id=PID.NO_CODE,
                        category="php",
                        message="PHP file appears to contain little or no executable code.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=PID.SYNTAX_SUSPECT,
                        category="php",
                        message="PHP syntax heuristics look suspect.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )

            request_usage = lowered.count("$_get") + lowered.count("$_post") + lowered.count("$_request")
            session_usage = lowered.count("session_start") + lowered.count("$_session")
            db_usage = lowered.count("mysqli") + lowered.count("pdo") + lowered.count("mysql")
            include_usage = lowered.count("include") + lowered.count("require")
            echo_usage = lowered.count("echo") + lowered.count("print")
            header_usage = lowered.count("header(")

            findings.append(
                Finding(
                    id=PID.EVIDENCE,
                    category="php",
                    message="PHP evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "request_usage": request_usage,
                        "session_usage": session_usage,
                        "db_usage": db_usage,
                        "include_usage": include_usage,
                        "echo_usage": echo_usage,
                        "header_usage": header_usage,
                    },
                    source=self.name,
                )
            )

            # Code Quality Checks
            # 1. Enforce separation of concerns (logic vs. markup)
            # Check for excessive mixing of PHP and HTML
            html_tags_in_php = len(re.findall(r'<[a-z]+[^>]*>', content))
            php_tags = content.count("<?php") + content.count("<?=")
            # If lots of HTML tags and PHP tags mixed, might indicate poor separation
            if html_tags_in_php > 20 and php_tags > 5:
                findings.append(
                    Finding(
                        id=PID.QUALITY_MIXED_MARKUP,
                        category="php",
                        message="Code appears to mix PHP logic with HTML markup extensively. Consider separating concerns (MVC pattern).",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "html_tags": html_tags_in_php,
                            "php_tags": php_tags,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 2. Detect SQL queries embedded directly inside echo/print statements
            # Look for echo/print followed by SQL keywords
            lines = content.splitlines()
            sql_in_echo = []
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if re.search(r'\b(echo|print)\s+.*(select|insert|update|delete|create|alter)\s+', stripped, re.IGNORECASE):
                    sql_in_echo.append(i)
            
            if sql_in_echo:
                findings.append(
                    Finding(
                        id=PID.QUALITY_SQL_IN_OUTPUT,
                        category="php",
                        message=f"Found SQL queries embedded in echo/print statements on {len(sql_in_echo)} line(s). Separate database logic from output.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "problematic_lines": sql_in_echo[:10],
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # Security Checks
            # 1. Input sanitisation (GET/POST)
            # Check if $_GET/$_POST are used without sanitization functions
            sanitization_functions = [
                "htmlspecialchars", "htmlentities", "filter_var", "filter_input",
                "mysqli_real_escape_string", "addslashes", "strip_tags", "trim"
            ]
            has_sanitization = any(func in lowered for func in sanitization_functions)
            
            if request_usage > 0 and not has_sanitization:
                findings.append(
                    Finding(
                        id=PID.SECURITY_UNSANITIZED_INPUT,
                        category="php",
                        message="$_GET/$_POST used without apparent input sanitization. Always sanitize user input to prevent XSS and injection attacks.",
                        severity=Severity.FAIL,
                        evidence={
                            "path": str(path),
                            "request_usage": request_usage,
                            "sanitization_found": False,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 2. Output escaping (HTML entities)
            # Check if echo/print output is escaped
            echo_statements = re.findall(r'\b(echo|print)\s+[^;]+', content, re.IGNORECASE)
            escaped_output = any(
                "htmlspecialchars" in stmt or "htmlentities" in stmt or "esc_html" in stmt.lower()
                for stmt in echo_statements
            )
            
            if echo_usage > 3 and not escaped_output:
                findings.append(
                    Finding(
                        id=PID.SECURITY_UNESCAPED_OUTPUT,
                        category="php",
                        message="Output statements (echo/print) may not be properly escaped. Use htmlspecialchars() or htmlentities() to prevent XSS.",
                        severity=Severity.FAIL,
                        evidence={
                            "path": str(path),
                            "echo_usage": echo_usage,
                            "escaped_output": escaped_output,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 3. Use of prepared statements for database operations
            # Check for mysqli_query or mysql_query (old style) vs prepared statements
            old_style_queries = lowered.count("mysql_query") + lowered.count("mysqli_query(")
            prepared_statements = lowered.count("prepare(") + lowered.count("bind_param") + lowered.count("pdo")
            
            if db_usage > 0:
                if old_style_queries > 0 and prepared_statements == 0:
                    findings.append(
                        Finding(
                            id=PID.SECURITY_NO_PREPARED_STATEMENTS,
                            category="php",
                            message="Database queries found but no prepared statements detected. Use prepared statements (mysqli_prepare/PDO) to prevent SQL injection.",
                            severity=Severity.FAIL,
                            evidence={
                                "path": str(path),
                                "old_style_queries": old_style_queries,
                                "prepared_statements": prepared_statements,
                            },
                            source=self.name,
                            finding_category=FindingCategory.STRUCTURE,
                        )
                    )
                elif prepared_statements > 0:
                    findings.append(
                        Finding(
                            id=PID.SECURITY_PREPARED_STATEMENTS_USED,
                            category="php",
                            message="Prepared statements detected. Good security practice.",
                            severity=Severity.INFO,
                            evidence={
                                "path": str(path),
                                "prepared_statements": prepared_statements,
                            },
                            source=self.name,
                            finding_category=FindingCategory.EVIDENCE,
                        )
                    )

            # 4. Proper session handling
            if session_usage > 0:
                session_start_found = "session_start" in lowered
                session_regenerate = "session_regenerate_id" in lowered
                session_destroy = "session_destroy" in lowered
                
                if not session_start_found:
                    findings.append(
                        Finding(
                            id=PID.SECURITY_SESSION_NOT_STARTED,
                            category="php",
                            message="Session variables used but session_start() not found. Always start sessions before using session variables.",
                            severity=Severity.WARN,
                            evidence={
                                "path": str(path),
                                "session_usage": session_usage,
                            },
                            source=self.name,
                            finding_category=FindingCategory.STRUCTURE,
                        )
                    )
                elif not session_regenerate:
                    findings.append(
                        Finding(
                            id=PID.SECURITY_SESSION_NOT_REGENERATED,
                            category="php",
                            message="Sessions used but session_regenerate_id() not found. Regenerate session ID on login to prevent session fixation.",
                            severity=Severity.WARN,
                            evidence={
                                "path": str(path),
                                "session_start_found": session_start_found,
                            },
                            source=self.name,
                            finding_category=FindingCategory.STRUCTURE,
                        )
                    )

        return findings


__all__ = ["PHPStaticAssessor"]
