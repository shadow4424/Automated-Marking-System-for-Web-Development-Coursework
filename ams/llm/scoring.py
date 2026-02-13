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
import re
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

# Dedicated system prompt for partial credit — keeps the small model on-track
PARTIAL_CREDIT_SYSTEM_PROMPT = (
    "You are a scoring engine. You receive student code that FAILED a check. "
    "You MUST respond with ONLY a JSON object containing exactly three keys: "
    '"intent" (string: "yes" or "no"), '
    '"reasoning" (string: one sentence), '
    '"suggested_score" (number: 0.0 to 0.5). '
    "Do NOT use any other keys. Do NOT use summary, items, severity, or message keys. "
    "Output ONLY the JSON object, nothing else."
)


def _build_partial_credit_prompt(
    rule_name: str,
    category: str,
    student_code: str,
    error_context: str,
) -> str:
    """Build prompt for partial credit evaluation.
    
    Asks the LLM to detect implementation intent despite syntax errors.
    """
    return f"""Student code FAILED rule "{rule_name}" (category: {category}).
Failure reason: {error_context}

Code:
```
{student_code}
```

Decide if this code shows effort toward "{rule_name}". Scoring guide:
- 0.4 to 0.5: student clearly tried to implement this feature but has bugs or syntax errors
- 0.2 to 0.4: student wrote related code but did not fully address the rule
- 0.1 to 0.2: student wrote code in this language showing general effort, even if the specific feature is missing
- 0.0: file is empty, contains only comments, or is completely unrelated

You may choose ANY value from 0.0 to 0.5 (e.g. 0.15, 0.25, 0.35, 0.45).

Respond with a JSON object containing exactly three keys:
  "intent" — set to "yes" if ANY code effort exists, "no" only if the file is empty or unrelated
  "reasoning" — one sentence describing what code the student wrote
  "suggested_score" — a number between 0.0 and 0.5"""


def _parse_partial_credit_response(raw_response: str, cleaned: str) -> dict:
    """Robustly parse the LLM response for partial credit, handling wrong formats.
    
    The small model sometimes responds with the feedback format instead of
    the partial credit format. This function handles both cases.
    
    Returns:
        dict with keys: intent, reasoning, suggested_score
    """
    
    # Try standard JSON parse first
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        parsed = {}
    
    # Case 1: Correct format — has "intent" key
    if "intent" in parsed:
        return parsed
    
    # Case 2: Wrong format — model returned {"summary": "...", "items": [...]}
    # Extract useful information from the wrong format
    result = {"intent": "no", "reasoning": "", "suggested_score": 0.0}
    
    # Try to extract reasoning from summary or message fields
    summary = parsed.get("summary", "")
    items = parsed.get("items", [])
    if items and isinstance(items, list) and len(items) > 0:
        item = items[0] if isinstance(items[0], dict) else {}
        message = item.get("message", "")
        severity = item.get("severity", "").upper()
    else:
        message = ""
        severity = ""
    
    reasoning_text = summary or message or raw_response
    result["reasoning"] = reasoning_text[:200]
    
    # Detect intent from the text content
    lower_text = (summary + " " + message + " " + raw_response).lower()
    
    # Positive indicators — student attempted something
    positive_signals = [
        "attempt", "tried", "partial", "incomplete", "minor",
        "syntax error", "typo", "close", "almost", "nearly",
        "logic is", "implemented", "demonstrates", "shows",
        "used", "included", "present", "found", "exists",
    ]
    # Negative indicators — no attempt at all
    negative_signals = [
        "empty", "no code", "no attempt", "missing", "not found",
        "not present", "placeholder", "no meaningful", "no implementation",
        "completely unrelated", "no evidence", "blank",
    ]
    
    pos_count = sum(1 for s in positive_signals if s in lower_text)
    neg_count = sum(1 for s in negative_signals if s in lower_text)
    
    if pos_count > neg_count:
        result["intent"] = "yes"
        # Score based on strength of positive signals
        if any(s in lower_text for s in ["minor", "almost", "close", "nearly", "syntax error", "typo"]):
            result["suggested_score"] = 0.5
        elif any(s in lower_text for s in ["partial", "incomplete", "attempt"]):
            result["suggested_score"] = 0.3
        else:
            result["suggested_score"] = 0.2
    
    # Also try to find a numeric score in the response via regex
    score_match = re.search(r'"suggested_score"\s*:\s*([\d.]+)', raw_response)
    if score_match:
        try:
            extracted_score = float(score_match.group(1))
            if 0.0 < extracted_score <= 0.5:
                result["suggested_score"] = extracted_score
                result["intent"] = "yes"
        except ValueError:
            pass
    
    logger.info(f"Fallback partial credit parse: pos={pos_count}, neg={neg_count}, result={result}")
    return result


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
    
    # Build and send prompt with dedicated system prompt
    prompt = _build_partial_credit_prompt(
        rule_name=rule_name,
        category=category,
        student_code=sanitized_code,
        error_context=sanitized_context,
    )
    
    logger.debug(f"Evaluating partial credit for rule: {rule_name}")
    raw_response = ask_llama(prompt, system_prompt=PARTIAL_CREDIT_SYSTEM_PROMPT)
    cleaned = _clean_json_response(raw_response)
    
    try:
        # Use robust parser that handles wrong LLM formats
        parsed = _parse_partial_credit_response(raw_response, cleaned)
        result.raw_response = parsed
        
        intent = parsed.get("intent", "no").lower()
        result.intent_detected = intent == "yes"
        result.reasoning = parsed.get("reasoning", "")
        
        # Fallback: Check reasoning for keywords if intent is strict "no"
        if not result.intent_detected and result.reasoning:
            lower_reasoning = result.reasoning.lower()
            if "attempted" in lower_reasoning or "minor syntax" in lower_reasoning or "typo" in lower_reasoning:
                logger.info(f"Overriding intent to YES based on reasoning keywords: {result.reasoning}")
                result.intent_detected = True
        
        suggested = float(parsed.get("suggested_score", 0.0))
        
        # Enforce partial_range constraints (Phase 2.2)
        min_partial, max_partial = partial_range
        
        # Robustness: If intent detected but score 0, default to a low partial score (0.2)
        if result.intent_detected and suggested == 0.0:
            suggested = 0.2
            
        if result.intent_detected:
            # Clamp to allowed range (never exceeds 50%)
            result.llm_score = min(max(suggested, min_partial), max_partial)
        else:
            result.llm_score = 0.0
        
        # Arbitrate final score (Phase 2.3)
        result.final_score = arbitrate_score(result.static_score, result.llm_score)
        
        logger.info(f"Partial credit for {rule_name}: intent={result.intent_detected}, "
                     f"suggested={suggested}, final={result.final_score}, reasoning={result.reasoning[:100]}")
        
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
