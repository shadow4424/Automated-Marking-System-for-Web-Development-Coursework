from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec


class CSSRequiredRulesAssessor(Assessor):
    """Checks required CSS rules based on profile spec."""

    name = "css_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        css_files = sorted(context.discovered_files.get("css", []))

        if not self.profile_spec.required_css:
            findings.append(
                Finding(
                    id="CSS.REQ.SKIPPED",
                    category="css",
                    message="No required CSS rules defined for this profile; skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"rule_ids": []},
                    source=self.name,
                )
            )
            return findings

        # Check if CSS is required for this profile
        is_required = self.profile_spec.is_component_required("css")
        has_required_rules = self.profile_spec.has_required_rules("css")

        if not css_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="CSS.REQ.MISSING_FILES",
                        category="css",
                        message="No CSS files found; CSS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_css],
                            "expected_needles": [r.needle for r in self.profile_spec.required_css],
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
                        id="CSS.REQ.SKIPPED",
                        category="css",
                        message="No CSS files found; CSS required checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_css],
                            "expected_needles": [r.needle for r in self.profile_spec.required_css],
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

        for path in css_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id="CSS.REQ.READ_ERROR",
                        category="css",
                        message="Failed to read CSS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            brace_count = content.count("{")
            for rule in self.profile_spec.required_css:
                count = brace_count if rule.needle == "{" else content.count(rule.needle)
                passed = count >= rule.min_count
                findings.append(
                    Finding(
                        id="CSS.REQ.PASS" if passed else "CSS.REQ.FAIL",
                        category="css",
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


__all__ = ["CSSRequiredRulesAssessor"]
