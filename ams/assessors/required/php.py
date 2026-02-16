from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.finding_ids import PHP as PID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec


class PHPRequiredFeaturesAssessor(Assessor):
    """Checks required PHP features based on profile spec."""

    name = "php_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        php_files = sorted(context.discovered_files.get("php", []))

        if not self.profile_spec.required_php:
            findings.append(
                Finding(
                    id=PID.REQ_SKIPPED,
                    category="php",
                    message="No required PHP rules defined for this profile; skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"rule_ids": []},
                    source=self.name,
                )
            )
            return findings

        # Check if PHP is required for this profile
        is_required = self.profile_spec.is_component_required("php")
        has_required_rules = self.profile_spec.has_required_rules("php")

        # If PHP is not required for this profile, generate per-rule SKIPPED findings
        if not is_required:
            for rule in self.profile_spec.required_php:
                findings.append(
                    Finding(
                        id=PID.REQ_SKIPPED,
                        category="php",
                        message=f"Rule '{rule.id}' skipped: PHP not required for this profile.",
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

        if not php_files:
            if is_required and has_required_rules:
                # Generate per-rule FAIL findings for each required rule when files missing
                for rule in self.profile_spec.required_php:
                    findings.append(
                        Finding(
                            id=PID.REQ_MISSING_FILES,
                            category="php",
                            message=f"Rule '{rule.id}' not evaluated: No PHP files found.",
                            severity=Severity.FAIL,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "min_count": rule.min_count,
                                "weight": rule.weight,
                                "skip_reason": "no_php_files_found",
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
                for rule in self.profile_spec.required_php:
                    findings.append(
                        Finding(
                            id=PID.REQ_SKIPPED,
                            category="php",
                            message=f"Rule '{rule.id}' skipped: {rule.description}",
                            severity=Severity.SKIPPED,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "weight": rule.weight,
                                "skip_reason": "component_not_required" if not is_required else "no_php_files_found",
                            },
                            source=self.name,
                            finding_category=FindingCategory.OTHER,
                            profile=self.profile_spec.name,
                            required=is_required,
                        )
                    )
            return findings

        for path in php_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError as exc:
                findings.append(
                    Finding(
                        id=PID.REQ_READ_ERROR,
                        category="php",
                        message="Failed to read PHP file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            content_lower = content.lower()
            content_raw = path.read_text(encoding="utf-8", errors="replace")
            for rule in self.profile_spec.required_php:
                count, passed, snippet = self._evaluate_rule(rule, content_lower, content_raw)
                findings.append(
                    Finding(
                        id=PID.REQ_PASS if passed else PID.REQ_FAIL,
                        category="php",
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
                            "content": content_raw[:500],
                        },
                        source=self.name,
                    )
                )
        return findings

    def _evaluate_rule(self, rule, content_lower: str, content_raw: str) -> tuple[int, bool, str]:
        """Evaluate a single PHP rule and return count, pass status, and snippet.
        
        Returns:
            Tuple of (count, passed, snippet_string)
        """
        needle = rule.needle.lower()
        snippet = ""
        
        # === REQUEST SUPERGLOBALS ===
        if needle == "request_superglobal" or rule.id == "php.uses_request":
            patterns = ["$_get", "$_post", "$_request"]
            count = sum(content_lower.count(p) for p in patterns)
            if count > 0:
                for p in patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === VALIDATION ===
        if needle == "validation" or rule.id == "php.has_validation":
            validation_funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
            count = sum(1 for f in validation_funcs if f in content_lower)
            if count > 0:
                for f in validation_funcs:
                    if f in content_lower:
                        snippet = self._extract_snippet(content_raw, f)
                        break
            return count, count >= rule.min_count, snippet
        
        # === SANITISATION ===
        if needle == "sanitisation" or rule.id == "php.has_sanitisation":
            sanitise_funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
            count = sum(1 for f in sanitise_funcs if f in content_lower)
            if count > 0:
                for f in sanitise_funcs:
                    if f in content_lower:
                        snippet = self._extract_snippet(content_raw, f)
                        break
            return count, count >= rule.min_count, snippet
        
        # === OUTPUT ===
        if needle == "output" or rule.id == "php.outputs":
            count = content_lower.count("echo") + content_lower.count("print")
            if count > 0:
                if "echo" in content_lower: snippet = self._extract_snippet(content_raw, "echo")
                elif "print" in content_lower: snippet = self._extract_snippet(content_raw, "print")
            return count, count >= rule.min_count, snippet
        
        # === DATABASE ===
        if needle == "database" or rule.id == "php.uses_database":
            db_patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
            count = sum(1 for p in db_patterns if p in content_lower)
            if count > 0:
                for p in db_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === PREPARED STATEMENTS ===
        if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
            prep_patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
            count = sum(1 for p in prep_patterns if p in content_lower)
            if count > 0:
                for p in prep_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === SESSIONS ===
        if needle == "sessions" or rule.id == "php.uses_sessions":
            session_patterns = ["session_start", "$_session", "session_destroy"]
            count = sum(1 for p in session_patterns if p in content_lower)
            if count > 0:
                for p in session_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "php.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = "foreach" in content_lower
            count = sum([has_for, has_while, has_foreach])
            if count > 0:
                if has_for: snippet = self._extract_snippet(content_raw, "for")
                elif has_while: snippet = self._extract_snippet(content_raw, "while")
                elif has_foreach: snippet = self._extract_snippet(content_raw, "foreach")
            return count, count >= rule.min_count, snippet
        
        # === ERROR HANDLING ===
        if needle == "error_handling" or rule.id == "php.has_error_handling":
            error_patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
            count = sum(1 for p in error_patterns if p in content_lower)
            if count > 0:
                for p in error_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        if count > 0:
            snippet = self._extract_snippet(content_raw, needle)
        return count, count >= rule.min_count, snippet

    def _extract_snippet(self, content: str, needle: str, context_lines: int = 2) -> str:
        """Extract a snippet of code surrounding the needle."""
        try:
            lines = content.splitlines()
            lower_needle = needle.lower()
            
            for i, line in enumerate(lines):
                if lower_needle in line.lower():
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    
                    snippet = []
                    for j in range(start, end):
                        prefix = "> " if j == i else "  "
                        snippet.append(f"{j+1:3d} | {lines[j]}")
                    return "\n".join(snippet)
            return ""
        except Exception:
            return ""
    
    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["PHPRequiredFeaturesAssessor"]
