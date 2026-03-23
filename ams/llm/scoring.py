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

from ams.llm.feedback import ask_llama, scrub_pii, _clean_json_response
from ams.llm.prompts import PARTIAL_CREDIT_SYSTEM_PROMPT, PARTIAL_CREDIT_USER_PROMPT_TEMPLATE

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

def _build_partial_credit_prompt(
    rule_name: str,
    category: str,
    student_code: str,
    error_context: str,
) -> str:
    """Build prompt for partial credit evaluation.
    
    Asks the LLM to detect implementation intent despite syntax errors.
    """
    return PARTIAL_CREDIT_USER_PROMPT_TEMPLATE.format(
        rule_name=rule_name,
        category=category,
        code_snippet=student_code,
        error_context=error_context,
    )


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
    else:
        message = ""
    
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


# =============================================================================
# Batch Partial Credit (Prompt Consolidation)
# =============================================================================


def evaluate_partial_credit_batch(
    items: list[dict],
) -> dict[str, HybridScore]:
    """Evaluate partial credit for multiple failed rules in a single LLM call.

    Args:
        items: List of dicts with keys:
            rule_name, student_code, error_context, category, partial_range

    Returns:
        Dict mapping rule_name -> HybridScore
    """
    if not items:
        return {}

    # Single item — delegate to the standard function
    if len(items) == 1:
        it = items[0]
        hs = evaluate_partial_credit(
            rule_name=it["rule_name"],
            student_code=it["student_code"],
            error_context=it["error_context"],
            category=it.get("category", "unknown"),
            partial_range=it.get("partial_range", (0.0, 0.5)),
        )
        return {it["rule_name"]: hs}

    rule_names = [it["rule_name"] for it in items]

    try:
        return _evaluate_batch_internal(items, rule_names)
    except Exception as e:
        logger.warning("Batch partial credit failed, using fallbacks: %s", e)
        return {
            rn: HybridScore(
                static_score=0.0,
                reasoning=f"LLM error: {e}",
                raw_response={"error": str(e)},
            )
            for rn in rule_names
        }


def _evaluate_batch_internal(
    items: list[dict],
    rule_names: list[str],
) -> dict[str, HybridScore]:
    from ams.llm.prompts import (
        BATCH_PARTIAL_CREDIT_SYSTEM_PROMPT,
        BATCH_PARTIAL_CREDIT_USER_TEMPLATE,
    )

    blocks: list[str] = []
    for it in items:
        code = scrub_pii(it["student_code"])
        if len(code) > 500:
            code = code[:500] + "\n... [truncated]"
        ctx = scrub_pii(it["error_context"])
        blocks.append(
            f"---\n"
            f"Rule: {it['rule_name']} (category: {it.get('category', 'unknown')})\n"
            f"Failure reason: {ctx}\n"
            f"Code:\n```\n{code}\n```\n"
            f"---"
        )

    prompt = BATCH_PARTIAL_CREDIT_USER_TEMPLATE.format(
        count=len(items),
        rules_block="\n\n".join(blocks),
    )

    raw_response = ask_llama(prompt, system_prompt=BATCH_PARTIAL_CREDIT_SYSTEM_PROMPT)
    cleaned = _clean_json_response(raw_response)

    try:
        parsed_top = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        parsed_top = {}

    results_list = parsed_top.get("results", [])
    if not isinstance(results_list, list):
        results_list = []

    # Build a lookup from rule_name -> partial_range for clamping
    range_map = {it["rule_name"]: it.get("partial_range", (0.0, 0.5)) for it in items}

    scored: dict[str, HybridScore] = {}
    for entry in results_list:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("rule_id", "")
        if rid not in rule_names:
            continue

        partial_range = range_map.get(rid, (0.0, 0.5))

        # Use the existing robust parser on this single entry
        entry_json = json.dumps(entry)
        parsed_entry = _parse_partial_credit_response(entry_json, entry_json)

        hs = HybridScore(static_score=0.0)
        hs.raw_response = entry
        hs.intent_detected = parsed_entry.get("intent", "no").lower() == "yes"
        hs.reasoning = parsed_entry.get("reasoning", "")

        # Apply the same reasoning-keyword override as the single-item path
        if not hs.intent_detected and hs.reasoning:
            lower_reasoning = hs.reasoning.lower()
            if any(kw in lower_reasoning for kw in ("attempted", "minor syntax", "typo")):
                logger.info(
                    "Overriding intent to YES based on reasoning keywords: %s",
                    hs.reasoning,
                )
                hs.intent_detected = True

        suggested = float(parsed_entry.get("suggested_score", 0.0))

        min_partial, max_partial = partial_range
        if hs.intent_detected and suggested == 0.0:
            suggested = 0.2
        if hs.intent_detected:
            hs.llm_score = min(max(suggested, min_partial), max_partial)
        else:
            hs.llm_score = 0.0

        hs.final_score = arbitrate_score(hs.static_score, hs.llm_score)
        scored[rid] = hs

    # Fill missing with zero-score fallbacks
    for rn in rule_names:
        if rn not in scored:
            scored[rn] = HybridScore(static_score=0.0)

    return scored


__all__ = [
    "HybridScore",
    "evaluate_partial_credit",
    "evaluate_partial_credit_batch",
    "arbitrate_score",
    "should_evaluate_partial_credit",
    "check_attempt_signal",
]
