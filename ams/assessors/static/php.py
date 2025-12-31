from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
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
                        id="PHP.MISSING_FILES",
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
                        id="PHP.SKIPPED",
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
                        id="PHP.READ_ERROR",
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
                    id="PHP.TAG_OK" if has_open_tag else "PHP.TAG_MISSING",
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
                        id="PHP.SYNTAX_OK",
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
                        id="PHP.NO_CODE",
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
                        id="PHP.SYNTAX_SUSPECT",
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
                    id="PHP.EVIDENCE",
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

        return findings


__all__ = ["PHPStaticAssessor"]
