from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
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
                    id="PHP.REQ.SKIPPED",
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

        if not php_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="PHP.REQ.MISSING_FILES",
                        category="php",
                        message="No PHP files found; PHP is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_php],
                            "needles": [r.needle for r in self.profile_spec.required_php],
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
                        id="PHP.REQ.SKIPPED",
                        category="php",
                        message="No PHP files found; PHP required checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_php],
                            "needles": [r.needle for r in self.profile_spec.required_php],
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

        for path in php_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError as exc:
                findings.append(
                    Finding(
                        id="PHP.REQ.READ_ERROR",
                        category="php",
                        message="Failed to read PHP file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            content_lower = content.lower()
            for rule in self.profile_spec.required_php:
                count, passed = self._evaluate_rule(rule, content_lower)
                findings.append(
                    Finding(
                        id="PHP.REQ.PASS" if passed else "PHP.REQ.FAIL",
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
                        },
                        source=self.name,
                    )
                )
        return findings

    def _evaluate_rule(self, rule, content_lower: str) -> tuple[int, bool]:
        """Evaluate a single PHP rule against the file content.
        
        Returns:
            A tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the rule requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === REQUEST SUPERGLOBALS ===
        if needle == "request_superglobal" or rule.id == "php.uses_request":
            count = (
                content_lower.count("$_get")
                + content_lower.count("$_post")
                + content_lower.count("$_request")
            )
            return count, count >= rule.min_count
        
        # === VALIDATION ===
        if needle == "validation" or rule.id == "php.has_validation":
            validation_funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
            count = sum(1 for f in validation_funcs if f in content_lower)
            return count, count >= rule.min_count
        
        # === SANITISATION ===
        if needle == "sanitisation" or rule.id == "php.has_sanitisation":
            sanitise_funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
            count = sum(1 for f in sanitise_funcs if f in content_lower)
            return count, count >= rule.min_count
        
        # === OUTPUT ===
        if needle == "output" or rule.id == "php.outputs":
            count = content_lower.count("echo") + content_lower.count("print")
            return count, count >= rule.min_count
        
        # === DATABASE ===
        if needle == "database" or rule.id == "php.uses_database":
            db_patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
            count = sum(1 for p in db_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === PREPARED STATEMENTS ===
        if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
            prep_patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
            count = sum(1 for p in prep_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === SESSIONS ===
        if needle == "sessions" or rule.id == "php.uses_sessions":
            session_patterns = ["session_start", "$_session", "session_destroy"]
            count = sum(1 for p in session_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "php.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = "foreach" in content_lower
            count = sum([has_for, has_while, has_foreach])
            return count, count >= rule.min_count
        
        # === ERROR HANDLING ===
        if needle == "error_handling" or rule.id == "php.has_error_handling":
            error_patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
            count = sum(1 for p in error_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["PHPRequiredFeaturesAssessor"]
