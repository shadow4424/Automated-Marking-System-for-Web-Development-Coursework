from __future__ import annotations

from typing import List, Tuple

from ams.assessors.required.base_required_assessor import BaseRequiredAssessor
from ams.core.finding_ids import JS as JID
from ams.core.profiles import ProfileSpec, RequiredJSRule


class JSRequiredFeaturesAssessor(BaseRequiredAssessor):
    """Checks required JS features based on profile spec.
    
    Inherits common behaviour from BaseRequiredAssessor:
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

        # === CALCULATOR: DISPLAY DOM ===
        if needle == "creates_display_dom" or rule.id == "js.creates_display_dom":
            # Check for theDisplay reference (exact name from spec) or equivalent display input
            has_thedisplay = "thedisplay" in content_lower
            # Also accept getElementById with any 'display' related id
            has_getelm_display = "getelementbyid" in content_lower and "display" in content_lower
            count = 1 if (has_thedisplay or has_getelm_display) else 0
            return count, count >= rule.min_count

        # === CALCULATOR: DIGIT BUTTONS ===
        if needle == "creates_digit_buttons" or rule.id == "js.creates_digit_buttons":
            # Count how many digit characters appear as string literals in a createElement context
            has_createelement = "createelement" in content_lower
            if not has_createelement:
                return 0, rule.min_count == 0
            # Count digits 0-9 present as string values
            digit_count = sum(1 for d in "0123456789" if f'"{d}"' in content_lower or f"'{d}'" in content_lower)
            # Also check for decimal and equals
            has_decimal = '"." ' in content_lower or "'.' " in content_lower or '"."' in content_lower or "'.''" in content_lower
            has_equals = '"="' in content_lower or "'='" in content_lower
            total = digit_count + (1 if has_decimal else 0) + (1 if has_equals else 0)
            return total, total >= 8  # Full pass: 8+ of 12 digit/decimal/equals buttons

        # === CALCULATOR: OPERATOR BUTTONS ===
        if needle == "creates_operator_buttons" or rule.id == "js.creates_operator_buttons":
            has_createelement = "createelement" in content_lower
            if not has_createelement:
                # May still have operators as button text even without createElement
                pass
            operators_found = sum(1 for op in ['"+"', '"-"', '"*"', '"/"', "'+'" , "'-'", "'*'", "'/'"]
                                  if op in content_lower)
            # Deduplicate (+/- might both match)
            distinct_ops = sum(1 for op, alts in [('"+"', "'+'" ), ('"-"', "'-'"), ('"*"', "'*'"), ('"/"', "'/'")]
                               if any(a in content_lower for a in [op, alts]))
            return distinct_ops, distinct_ops >= 4  # Full pass: all 4 operators

        # === CALCULATOR: UPDATE DISPLAY ===
        if needle == "has_updatedisplay" or rule.id == "js.has_updateDisplay":
            has_fn_name = "updatedisplay" in content_lower
            # Also detect display.value += pattern
            has_value_concat = ("display.value" in content_lower and "+=" in content_lower) or \
                               ("thedisplay" in content_lower and "+=" in content_lower)
            count = 1 if (has_fn_name or has_value_concat) else 0
            return count, count >= rule.min_count

        # === CALCULATOR: STATE TRACKING ===
        if needle == "has_prevalue_preop" or rule.id == "js.has_prevalue_preop_state":
            has_prevalue = "prevalue" in content_lower or "prevvalue" in content_lower
            has_preop = "preop" in content_lower or "prevop" in content_lower or "operator" in content_lower
            count = sum([has_prevalue, has_preop])
            return count, count >= rule.min_count  # min_count=1, need at least 1

        # === CALCULATOR: doCalc ===
        if needle == "has_docalc" or rule.id == "js.has_doCalc":
            has_fn_name = "docalc" in content_lower or "calculate" in content_lower or "compute" in content_lower
            # Count how many arithmetic operators are handled in logic
            ops_handled = sum(1 for op in ['"+"', '"-"', '"*"', '"/"', "'+'" , "'-'", "'*'", "'/'",
                                           "case '+'", "case '-'", 'case "+"', 'case "-"']
                              if op in content_lower)
            has_arithmetic = ops_handled >= 2
            count = 1 if (has_fn_name or has_arithmetic) else 0
            return count, count >= rule.min_count

        # === CALCULATOR: DISPLAY CLEAR/RESULT ===
        if needle == "clears_display" or rule.id == "js.clears_or_updates_display_correctly":
            # Check for display value being cleared (set to "" or "0")
            has_clear = ('display.value = ""' in content_lower or
                         "display.value = ''" in content_lower or
                         "display.value=''" in content_lower or
                         'display.value=""' in content_lower or
                         "thedisplay.value = ''" in content_lower)
            count = 1 if has_clear else 0
            return count, count >= rule.min_count

        # === createElement USAGE ===
        if needle == "uses_createelement" or rule.id == "js.uses_createElement":
            count = content_lower.count("createelement(")
            return count, count >= rule.min_count

        # === AVOIDS document.write ===
        if needle == "avoids_document_write" or rule.id == "js.avoids_document_write":
            # Inverted check: passes when document.write is absent
            uses_docwrite = "document.write(" in content_lower
            count = 0 if uses_docwrite else 1
            return count, count >= rule.min_count  # min_count=1 → passes only when absent

        # === EXTRA FEATURES ===
        if needle == "extra_features" or rule.id == "js.extra_features":
            extras = ["sqrt", "math.sqrt", "percent", "memory", "sin", "cos", "tan", "clear", "clearall", "backspace"]
            count = sum(1 for e in extras if e in content_lower)
            return count, count >= rule.min_count  # min_count=0 → optional

        # === STANDARD NEEDLE COUNTING ===
        count = content_lower.count(needle)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredJSRule, passed: bool, count: int) -> str:
        """Build a human-readable message for rule evaluation result."""
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} {status}: found {count}, required {rule.min_count}"


__all__ = ["JSRequiredFeaturesAssessor"]
