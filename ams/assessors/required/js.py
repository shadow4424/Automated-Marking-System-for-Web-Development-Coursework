from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import JS as JID
from ams.core.profiles import ProfileSpec, RequiredJSRule


class JSRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required JS features based on profile spec.
    
    Inherits common behavior from BaseRequiredAssessor:
    - File reading and error handling
    - Snippet extraction
    - Finding creation
    - Unified run() pipeline
    
    Implements JS-specific:
    - Rule evaluation with DOM, async, and feature pattern matching
    - Message building
    - Finding ID mapping
    """

    name = "js_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        super().__init__(profile)
    
    @property
    def component_name(self) -> str:
        return "js"
    
    @property
    def required_rules(self) -> List[RequiredJSRule]:
        return list(self.profile_spec.required_js)
    
    def _get_finding_id_pass(self) -> str:
        return JID.REQ_PASS
    
    def _get_finding_id_fail(self) -> str:
        return JID.REQ_FAIL
    
    def _get_finding_id_skipped(self) -> str:
        return JID.REQ_SKIPPED
    
    def _get_finding_id_missing_files(self) -> str:
        return JID.REQ_MISSING_FILES
    
    def _evaluate_rule_impl(
        self, rule: RequiredJSRule, content: str
    ) -> Tuple[int, bool]:
        """Evaluate a single JS rule against content.
        
        Args:
            rule: The JS rule to evaluate
            content: Raw JS content as string
            
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        content_lower = content.lower()
        return self._evaluate_rule(rule, content_lower)
    
    def _evaluate_rule(
        self, rule: RequiredJSRule, content_lower: str
    ) -> tuple[int, bool]:
        """Evaluate a single JS rule and return count and pass status.
        
        Returns:
            Tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the requirement is satisfied.
        """
        needle = rule.needle.lower()
        
        # === DOM QUERY ===
        if needle == "dom_query" or rule.id == "js.has_dom_query":
            dom_queries = ["queryselector", "getelementbyid", "getelementsbyclass", 
                          "getelementsbytagname", "queryselectorall"]
            count = sum(1 for q in dom_queries if q in content_lower)
            return count, count >= rule.min_count
        
        # === DOM MANIPULATION ===
        if needle == "dom_manipulation" or rule.id == "js.has_dom_manipulation":
            dom_methods = ["innerhtml", "textcontent", "appendchild", "removechild",
                          "createelement", "setattribute", "classlist", "style."]
            count = sum(1 for m in dom_methods if m in content_lower)
            return count, count >= rule.min_count
        
        # === LOOPS ===
        if needle == "loops" or rule.id == "js.has_loops":
            has_for = "for " in content_lower or "for(" in content_lower
            has_while = "while " in content_lower or "while(" in content_lower
            has_foreach = ".foreach" in content_lower
            has_map = ".map(" in content_lower
            count = sum([has_for, has_while, has_foreach, has_map])
            return count, count >= rule.min_count
        
        # === FORM VALIDATION ===
        if needle == "form_validation" or rule.id == "js.has_form_validation":
            validation_patterns = [".value", "validity", "checkvalidity", "required",
                                  "pattern", ".length", "isnan", "typeof"]
            count = sum(1 for p in validation_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === ASYNC PATTERNS ===
        if needle == "async_patterns" or rule.id == "js.has_async_patterns":
            async_patterns = ["async ", "await ", "fetch(", "promise", ".then("]
            count = sum(1 for p in async_patterns if p in content_lower)
            return count, count >= rule.min_count
        
        # === CONST/LET ===
        if needle == "const_let" or rule.id == "js.has_const_let":
            has_const = "const " in content_lower
            has_let = "let " in content_lower
            count = (1 if has_const else 0) + (1 if has_let else 0)
            return count, count >= rule.min_count
        
        # === TEMPLATE LITERALS ===
        if needle == "`" or rule.id == "js.has_template_literals":
            count = content_lower.count("`")
            return count, count >= rule.min_count
        
        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredJSRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["JSRequiredFeaturesAssessor"]



__all__ = ["JSRequiredFeaturesAssessor"]
