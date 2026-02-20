"""Phase 1: Feedback & Reliability - LLM Feedback Generation Module.

This module implements Phase 1 of the LLM integration roadmap:
- 1.1: Strict JSON output with explicit system prompts
- 1.2: Feedback templates for rule-based explanations
- 1.3: PII scrubbing before sending to LLM
- 1.4: Safety rails (LLM provides feedback only, no scores)

Refactored to use the LLMProvider abstraction from ams.core.factory.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ams.core.factory import get_llm_provider
from ams.llm.providers import LLMResponse
from ams.llm.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================


# =============================================================================
# Phase 1.3: PII Scrubbing
# =============================================================================


def scrub_pii(text: str) -> str:
    """Scrub personally identifiable information from text before sending to LLM.

    Replaces:
    - Email addresses with [EMAIL_REDACTED]
    - Student IDs (8-digit or c/s + 7 digits) with [STUDENT_ID]

    Args:
        text: Input text potentially containing PII.

    Returns:
        Sanitized text with PII replaced by placeholders.
    """
    if not text:
        return text

    # Email pattern: basic but covers most cases
    email_pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    text = re.sub(email_pattern, "[EMAIL_REDACTED]", text)

    # Student ID patterns:
    # - 8 consecutive digits: 12345678
    # - c or s prefix + 7 digits: c1234567, s1234567
    student_id_pattern = r"\b[cCsS]?\d{7,8}\b"
    text = re.sub(student_id_pattern, "[STUDENT_ID]", text)

    return text


# =============================================================================
# Phase 1.2: Feedback Templates
# =============================================================================


# =============================================================================
# LLM Communication (Refactored to use LLMProvider)
# =============================================================================

# Re-export from shared utility for backward compatibility
from ams.llm.utils import clean_json_response as _clean_json_response  # noqa: F401


def ask_llama(prompt: str, system_prompt: str = SYSTEM_PROMPT) -> str:
    """Send a prompt to the configured LLM provider and return the response.

    This function now uses the LLMProvider abstraction, respecting config.py.

    Args:
        prompt: User prompt to send.
        system_prompt: System prompt for behavior control.

    Returns:
        Raw response content from the LLM.
    """
    provider = get_llm_provider()
    
    response: LLMResponse = provider.complete(
        prompt=prompt,
        system_prompt=system_prompt,
        json_mode=True,  # Enable JSON mode for cleaner output
    )
    
    if response.error:
        return f'{{"error": "llm_error", "message": "{response.error}"}}'
    
    return response.content


# =============================================================================
# Main Feedback Generation Function
# =============================================================================


def generate_feedback(
    rule_name: str,
    student_code: str,
    error_context: str,
    category: str = "unknown",
) -> dict[str, Any]:
    """Generate structured feedback for a failed rule check.

    This is the main entry point for Phase 1 feedback generation.
    Now uses the Phase B FeedbackGenerator with Pydantic validation.

    Args:
        rule_name: The identifier of the failed rule (e.g., "html.has_doctype").
        student_code: The relevant snippet of student code.
        error_context: Description of what went wrong.
        category: Rule category (e.g., "Structure", "Semantics").

    Returns:
        Parsed JSON feedback as a dictionary. Always returns a valid dict,
        never raises exceptions.
    """
    from ams.llm.generators import FeedbackGenerator
    
    # Phase 1.3: Scrub PII before sending
    sanitized_code = scrub_pii(student_code)
    sanitized_context = scrub_pii(error_context)

    # Build evidence dict for the generator
    evidence = {
        "rule_id": rule_name,
        "category": category,
        "code_snippet": sanitized_code,
        "error_context": sanitized_context,
    }

    # Use the new robust generator
    generator = FeedbackGenerator()
    feedback = generator.generate(evidence)
    
    # Convert to dict for backward compatibility
    result = feedback.model_dump()
    
    # Map to legacy format if needed (keep backward compat)
    if feedback.items:
        # Include first item's message as top-level for legacy consumers
        result["rule"] = rule_name
        result["category"] = category
        result["result"] = feedback.items[0].severity if feedback.items else "INFO"
        result["evidence"] = feedback.summary
    
    logger.debug(f"Feedback generated for rule: {rule_name}, fallback={feedback.meta.get('fallback', False)}")
    return result


# =============================================================================
# Demo / CLI Entry Point
# =============================================================================


def main():
    """Demonstrate Phase 1 feedback generation."""
    print("=" * 60)
    print("Phase 1: Feedback & Reliability - Demo")
    print("=" * 60)

    # Mock student submission containing PII
    mock_submission = """
    <!-- Student: John Smith (c1234567) at john.smith@uni.edu -->
    <!DOCTYPE html>
    <html>
    <head>
        <title>My Page</title>
    </head>
    <body>
        <h1>Welcome</h1>
        <p>This is my page.</p>
    </body>
    </html>
    """

    mock_error_context = (
        "The HTML structure check failed because the document is missing "
        "semantic elements (header, nav, main, footer). Student c1234567's "
        "submission lacks proper structure."
    )

    # Step 1: Demonstrate PII scrubbing
    print("\n[Step 1] PII Scrubbing")
    print("-" * 40)
    print("Original text (truncated):")
    print(mock_submission[:100] + "...")
    sanitized = scrub_pii(mock_submission)
    print("\nSanitized text (truncated):")
    print(sanitized[:100] + "...")

    # Step 2: Generate feedback
    print("\n[Step 2] Generating Feedback via LLM")
    print("-" * 40)
    print("Sending request to LM Studio...")

    feedback = generate_feedback(
        rule_name="html.has_semantic_structure",
        student_code=mock_submission,
        error_context=mock_error_context,
        category="Semantics",
    )

    # Step 3: Display result
    print("\n[Step 3] Parsed Feedback Result")
    print("-" * 40)
    print(json.dumps(feedback, indent=2))

    print("\n" + "=" * 60)
    print("Demo Complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
