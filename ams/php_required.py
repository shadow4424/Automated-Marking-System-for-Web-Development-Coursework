from __future__ import annotations

from typing import List

from .assessors import Assessor
from .models import Finding, Severity, SubmissionContext
from .profiles import ProfileSpec, get_profile_spec


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

        if not php_files:
            findings.append(
                Finding(
                    id="PHP.REQ.SKIPPED",
                    category="php",
                    message="No PHP files found; required PHP checks skipped.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "rule_ids": [r.id for r in self.profile_spec.required_php],
                        "needles": [r.needle for r in self.profile_spec.required_php],
                        "discovered_count": 0,
                    },
                    source=self.name,
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
            for rule in self.profile_spec.required_php:
                if rule.id == "php.uses_request":
                    count = (
                        content.count("$_get")
                        + content.count("$_post")
                        + content.count("$_request")
                    )
                elif rule.id == "php.outputs":
                    count = content.count("echo") + content.count("print")
                else:
                    count = content.count(rule.needle.lower())
                passed = count >= rule.min_count
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
                        },
                        source=self.name,
                    )
                )
        return findings

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"
