from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec


class JSRequiredFeaturesAssessor(Assessor):
    """Checks required JS features based on profile spec."""

    name = "js_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        js_files = sorted(context.discovered_files.get("js", []))

        if not self.profile_spec.required_js:
            findings.append(
                Finding(
                    id="JS.REQ.SKIPPED",
                    category="js",
                    message="No required JS rules defined for this profile; skipped.",
                    severity=Severity.SKIPPED,
                    evidence={"rule_ids": []},
                    source=self.name,
                )
            )
            return findings

        # Check if JS is required for this profile
        is_required = self.profile_spec.is_component_required("js")
        has_required_rules = self.profile_spec.has_required_rules("js")

        if not js_files:
            if is_required and has_required_rules:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id="JS.REQ.MISSING_FILES",
                        category="js",
                        message="No JS files found; JS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_js],
                            "needles": [r.needle for r in self.profile_spec.required_js],
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
                        id="JS.REQ.SKIPPED",
                        category="js",
                        message="No JS files found; JS required checks not applicable.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_ids": [r.id for r in self.profile_spec.required_js],
                            "needles": [r.needle for r in self.profile_spec.required_js],
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

        for path in js_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError as exc:
                findings.append(
                    Finding(
                        id="JS.REQ.READ_ERROR",
                        category="js",
                        message="Failed to read JS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                content = ""
            content_lower = content.lower()
            content_raw = path.read_text(encoding="utf-8", errors="replace")
            for rule in self.profile_spec.required_js:
                count, passed = self._evaluate_rule(rule, content_lower, content_raw)
                findings.append(
                    Finding(
                        id="JS.REQ.PASS" if passed else "JS.REQ.FAIL",
                        category="js",
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

    def _evaluate_rule(
        self, rule, content_lower: str, content_raw: str
    ) -> tuple[int, bool]:
        """Evaluate a single JS rule against the file content.
        
        Returns:
            A tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the rule requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === DOM QUERY ===
        if needle == "dom_query" or rule.id == "js.has_dom_query":
            # Check for various DOM query methods
            dom_queries = ["queryselector", "getelementbyid", "getelementsbyclass", 
                          "getelementsbytagname", "queryselectorall"]
            count = sum(1 for q in dom_queries if q in content_lower)
            return count, count >= rule.min_count
        
        # === DOM MANIPULATION ===
        if needle == "dom_manipulation" or rule.id == "js.has_dom_manipulation":
            # Check for DOM manipulation methods
            dom_methods = ["innerhtml", "textcontent", "appendchild", "removechild",
                          "createelement", "setattribute", "classlist", "style."]
            count = sum(1 for m in dom_methods if m in content_lower)
            return count, count >= rule.min_count
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "js.has_loops":
            # Check for various loop patterns
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = ".foreach" in content_lower
            has_map = ".map(" in content_lower
            count = sum([has_for, has_while, has_foreach, has_map])
            return count, count >= rule.min_count
        
        # === FORM VALIDATION ===
        if needle == "form_validation" or rule.id == "js.has_form_validation":
            # Check for validation patterns
            validation_patterns = [".value", "validity", "checkvalidity", "required",
                                  "pattern", ".length", "isnan", "typeof"]
            count = sum(1 for p in validation_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === ASYNC PATTERNS ===
        if needle == "async_patterns" or rule.id == "js.has_async_patterns":
            # Check for async/await, fetch, Promise
            async_patterns = ["async ", "await ", "fetch(", "promise", ".then("]
            count = sum(1 for p in async_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === CONST/LET ===
        if needle == "const_let" or rule.id == "js.has_const_let":
            # Check for modern variable declarations
            has_const = "const " in content_lower
            has_let = "let " in content_lower
            count = (1 if has_const else 0) + (1 if has_let else 0)
            return count, count >= rule.min_count
        
        # === TEMPLATE LITERALS ===
        if needle == "`" or rule.id == "js.has_template_literals":
            # Check for backticks in raw content (case-sensitive)
            count = content_raw.count("`")
            return count, count >= rule.min_count
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _message(self, rule_id: str, passed: bool, count: int, min_count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule_id} {status}: found {count}, required {min_count}"


__all__ = ["JSRequiredFeaturesAssessor"]
