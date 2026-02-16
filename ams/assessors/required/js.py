from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.core.finding_ids import JS as JID
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
                    id=JID.REQ_SKIPPED,
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

        # If JS is not required for this profile, generate per-rule SKIPPED findings
        if not is_required:
            for rule in self.profile_spec.required_js:
                findings.append(
                    Finding(
                        id=JID.REQ_SKIPPED,
                        category="js",
                        message=f"Rule '{rule.id}' skipped: JS not required for this profile.",
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

        if not js_files:
            if is_required and has_required_rules:
                # Required for profile but missing - generate per-rule FAIL findings
                for rule in self.profile_spec.required_js:
                    findings.append(
                        Finding(
                            id=JID.REQ_MISSING_FILES,
                            category="js",
                            message=f"Rule '{rule.id}' not evaluated: No JS files found in submission.",
                            severity=Severity.FAIL,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "weight": rule.weight,
                                "skip_reason": "no_js_files_found",
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
                # Not required or no rules defined - generate per-rule SKIPPED findings
                for rule in self.profile_spec.required_js:
                    findings.append(
                        Finding(
                            id=JID.REQ_SKIPPED,
                            category="js",
                            message=f"Rule '{rule.id}' not evaluated: {rule.description}. JS not required for this profile or no files found.",
                            severity=Severity.SKIPPED,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "needle": rule.needle,
                                "weight": rule.weight,
                                "skip_reason": "component_not_required" if not is_required else "no_files_found",
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
                        id=JID.REQ_READ_ERROR,
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
                count, passed, snippet = self._evaluate_rule(rule, content_lower, content_raw)
                findings.append(
                    Finding(
                        id=JID.REQ_PASS if passed else JID.REQ_FAIL,
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
                            "snippet": snippet,
                            "content": content_raw[:500],
                        },
                        source=self.name,
                    )
                )
        return findings

    def _evaluate_rule(
        self, rule, content_lower: str, content_raw: str
    ) -> tuple[int, bool, str]:
        """Evaluate a single JS rule and return count, pass status, and snippet.
        
        Returns:
            Tuple of (count, passed, snippet_string)
        """
        needle = rule.needle.lower()
        snippet = ""
        
        # === DOM QUERY ===
        if needle == "dom_query" or rule.id == "js.has_dom_query":
            dom_queries = ["queryselector", "getelementbyid", "getelementsbyclass", 
                          "getelementsbytagname", "queryselectorall"]
            count = sum(1 for q in dom_queries if q in content_lower)
            if count > 0:
                # Find first match
                for q in dom_queries:
                    if q in content_lower:
                        snippet = self._extract_snippet(content_raw, q)
                        break
            return count, count >= rule.min_count, snippet
        
        # === DOM MANIPULATION ===
        if needle == "dom_manipulation" or rule.id == "js.has_dom_manipulation":
            dom_methods = ["innerhtml", "textcontent", "appendchild", "removechild",
                          "createelement", "setattribute", "classlist", "style."]
            count = sum(1 for m in dom_methods if m in content_lower)
            if count > 0:
                for m in dom_methods:
                    if m in content_lower:
                        snippet = self._extract_snippet(content_raw, m)
                        break
            return count, count >= rule.min_count, snippet
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "js.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = ".foreach" in content_lower
            has_map = ".map(" in content_lower
            count = sum([has_for, has_while, has_foreach, has_map])
            if count > 0:
                if has_for: snippet = self._extract_snippet(content_raw, "for")
                elif has_while: snippet = self._extract_snippet(content_raw, "while")
                elif has_foreach: snippet = self._extract_snippet(content_raw, "forEach")
                elif has_map: snippet = self._extract_snippet(content_raw, "map")
            return count, count >= rule.min_count, snippet
        
        # === FORM VALIDATION ===
        if needle == "form_validation" or rule.id == "js.has_form_validation":
            validation_patterns = [".value", "validity", "checkvalidity", "required",
                                  "pattern", ".length", "isnan", "typeof"]
            count = sum(1 for p in validation_patterns if p in content_lower)
            if count > 0:
                for p in validation_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === ASYNC PATTERNS ===
        if needle == "async_patterns" or rule.id == "js.has_async_patterns":
            async_patterns = ["async ", "await ", "fetch(", "promise", ".then("]
            count = sum(1 for p in async_patterns if p in content_lower)
            if count > 0:
                for p in async_patterns:
                    if p in content_lower:
                        snippet = self._extract_snippet(content_raw, p)
                        break
            return count, count >= rule.min_count, snippet
        
        # === CONST/LET ===
        if needle == "const_let" or rule.id == "js.has_const_let":
            has_const = "const " in content_lower
            has_let = "let " in content_lower
            count = (1 if has_const else 0) + (1 if has_let else 0)
            if count > 0:
                if has_const: snippet = self._extract_snippet(content_raw, "const ")
                elif has_let: snippet = self._extract_snippet(content_raw, "let ")
            return count, count >= rule.min_count, snippet
        
        # === TEMPLATE LITERALS ===
        if needle == "`" or rule.id == "js.has_template_literals":
            count = content_raw.count("`")
            if count > 0:
                snippet = self._extract_snippet(content_raw, "`")
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


__all__ = ["JSRequiredFeaturesAssessor"]
