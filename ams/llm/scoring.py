"""Phase 2: Hybrid Scoring - LLM-Assisted Partial Credit and Arbitration.

This module implements Phase 2 of the LLM integration roadmap:
- 2.1: LLM-assisted partial scoring for failed rules
- 2.2: Scoring constraints based on rule metadata
- 2.3: Close-call arbitration logic (trust-but-verify)
- 2.4: Hybrid scoring engine integration

Uses the LLMProvider abstraction via ams.llm.feedback.ask_llama.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ams.llm.feedback import ask_llama, scrub_pii, _clean_json_response, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# =============================================================================
# Data Structures
# =============================================================================


@dataclass
class HybridScore:
    """Result of hybrid scoring combining static and LLM analysis.
    
    Attributes:
        static_score: Score from deterministic static analysis (0.0, 0.5, or 1.0)
        llm_score: Score suggested by LLM analysis (0.0, 0.5, or 1.0)
        final_score: Arbitrated final score
        reasoning: Explanation for the LLM's decision
        intent_detected: Whether implementation intent was detected
        raw_response: Raw LLM response for audit trail
    """
    static_score: float
    llm_score: float | None = None
    final_score: float = 0.0
    reasoning: str = ""
    intent_detected: bool = False
    raw_response: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "static_score": self.static_score,
            "llm_score": self.llm_score,
            "final_score": self.final_score,
            "reasoning": self.reasoning,
            "intent_detected": self.intent_detected,
        }


# =============================================================================
# Partial Credit Evaluation (Phase 2.1)
# =============================================================================


def _build_partial_credit_prompt(
    rule_name: str,
    category: str,
    student_code: str,
    error_context: str,
) -> str:
    """Build prompt for partial credit evaluation.
    
    Asks the LLM to detect implementation intent despite syntax errors.
    """
    return f"""Analyze the following student code that FAILED a static check.

Rule: {rule_name}
Category: {category}
Error/Failure: {error_context}

Student Code (sanitized):
```
{student_code}
```

Your task: Determine if this code demonstrates CLEAR IMPLEMENTATION INTENT despite the failure.

Criteria for "Intent Detected":
- The student attempted to implement the required feature
- The logic/structure shows understanding of the concept
- The failure is due to minor syntax errors, typos, or incomplete implementation
- NOT just placeholder comments or empty functions

Generate a JSON object with this exact structure:
{{"intent": "yes" or "no", "reasoning": "<one sentence explanation>", "suggested_score": 0.5 or 0.0}}

RULES:
- Output ONLY the JSON object. No markdown, no code fences.
- "suggested_score" must be 0.5 if intent="yes", else 0.0.
- You are NOT assigning the final mark. This is a suggestion only."""


def evaluate_partial_credit(
    rule_name: str,
    student_code: str,
    error_context: str,
    category: str = "unknown",
    partial_range: tuple[float, float] = (0.0, 0.5),
) -> HybridScore:
    """Evaluate whether a failed rule deserves partial credit.
    
    This function is called when:
    - static_score == 0.0 (rule failed)
    - rule.partial_allowed == True
    
    Args:
        rule_name: The identifier of the failed rule.
        student_code: The relevant snippet of student code.
        error_context: Description of what went wrong.
        category: Rule category (e.g., "Structure", "Semantics").
        partial_range: Allowed range for partial credit (min, max).
    
    Returns:
        HybridScore with LLM evaluation and arbitrated final score.
    """
    result = HybridScore(static_score=0.0)
    
    # Scrub PII before sending
    sanitized_code = scrub_pii(student_code)
    sanitized_context = scrub_pii(error_context)
    
    # Build and send prompt
    prompt = _build_partial_credit_prompt(
        rule_name=rule_name,
        category=category,
        student_code=sanitized_code,
        error_context=sanitized_context,
    )
    
    logger.debug(f"Evaluating partial credit for rule: {rule_name}")
    raw_response = ask_llama(prompt)
    cleaned = _clean_json_response(raw_response)
    
    try:
        parsed = json.loads(cleaned)
        result.raw_response = parsed
        
        intent = parsed.get("intent", "no").lower()
        result.intent_detected = intent == "yes"
        result.reasoning = parsed.get("reasoning", "")
        
        suggested = float(parsed.get("suggested_score", 0.0))
        
        # Enforce partial_range constraints (Phase 2.2)
        min_partial, max_partial = partial_range
        if result.intent_detected:
            result.llm_score = min(max(suggested, min_partial), max_partial)
        else:
            result.llm_score = 0.0
        
        # Arbitrate final score (Phase 2.3)
        result.final_score = arbitrate_score(result.static_score, result.llm_score)
        
        logger.debug(f"Partial credit evaluation: intent={result.intent_detected}, score={result.final_score}")
        
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse LLM partial credit response: {e}")
        result.llm_score = None
        result.final_score = 0.0
        result.reasoning = f"LLM parse error: {e}"
        result.raw_response = {"error": str(e), "raw": raw_response[:500]}
    
    return result


# =============================================================================
# Score Arbitration (Phase 2.3)
# =============================================================================


def arbitrate_score(static_score: float, llm_score: float | None) -> float:
    """Arbitrate between static and LLM scores.
    
    Policy: Trust-but-verify - take the minimum of the two scores.
    This ensures the LLM cannot inflate grades, only provide partial credit
    for failed static checks where intent is detected.
    
    Args:
        static_score: Score from static analysis (0.0, 0.5, or 1.0)
        llm_score: Score suggested by LLM (0.0, 0.5, or 1.0, or None)
    
    Returns:
        Arbitrated final score.
    """
    if llm_score is None:
        return static_score
    
    # Special case: Static failed (0.0), LLM detected intent (0.5)
    # Allow the LLM to upgrade to 0.5 for partial credit
    if static_score == 0.0 and llm_score > 0.0:
        return llm_score
    
    # Default: Trust-but-verify - take the lower score
    return min(static_score, llm_score)


# =============================================================================
# Convenience Functions for Pipeline Integration
# =============================================================================


def check_attempt_signal(
    student_code: str,
    attempt_signal: str | None,
) -> bool:
    """Check if student code contains an attempt signal pattern.
    
    Phase D: Gate partial credit by requiring evidence of attempt.
    This prevents LLM from hallucinating credit for empty files.
    
    Args:
        student_code: The student's code to check.
        attempt_signal: Regex pattern to detect attempt (e.g., r"function\\s+calculate").
        
    Returns:
        True if attempt is detected or no signal is defined.
    """
    import re
    
    if not attempt_signal:
        # No signal defined - allow partial credit evaluation
        return True
    
    if not student_code or not student_code.strip():
        # Empty code - no attempt
        return False
    
    try:
        return bool(re.search(attempt_signal, student_code, re.IGNORECASE | re.MULTILINE))
    except re.error as e:
        logger.warning(f"Invalid attempt_signal regex '{attempt_signal}': {e}")
        return True  # Allow on regex error to avoid blocking


def should_evaluate_partial_credit(
    static_score: float,
    partial_allowed: bool,
    student_code: str = "",
    attempt_signal: str | None = None,
) -> bool:
    """Determine if partial credit evaluation should run.
    
    Phase D update: Now checks attempt signal before allowing partial credit.
    
    Args:
        static_score: Current score from static analysis.
        partial_allowed: Whether the rule allows partial credit.
        student_code: The student's code (for attempt signal check).
        attempt_signal: Regex pattern to detect attempt.
    
    Returns:
        True if LLM should evaluate for partial credit.
    """
    if static_score != 0.0:
        return False  # Only evaluate failed rules
        
    if not partial_allowed:
        return False  # Rule doesn't allow partial credit
    
    # Phase D: Check attempt signal (gate on evidence of attempt)
    if not check_attempt_signal(student_code, attempt_signal):
        logger.debug(f"Attempt signal not found, denying partial credit")
        return False
    
    return True


__all__ = [
    "HybridScore",
    "evaluate_partial_credit",
    "arbitrate_score",
    "should_evaluate_partial_credit",
    "check_attempt_signal",
]
