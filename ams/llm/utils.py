"""Shared utility functions for LLM modules.

Consolidates common helpers that were previously duplicated across
providers.py, feedback.py, and generators.py.
"""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def clean_json_response(text: str) -> str:
    """Extract valid JSON from a potentially wrapped LLM response.

    Handles common LLM quirks:
    - Markdown code fences (```json ... ```)
    - Preambles like "Here is the JSON:"
    - Trailing text after JSON
    - Trailing commas (common LLM error)

    Args:
        text: Raw LLM output that may contain wrapped JSON.

    Returns:
        Clean JSON string ready for parsing, or the original text if
        cleaning would break the content.
    """
    if not text:
        return text

    original = text

    # Strip markdown code fences
    fence_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
    match = re.search(fence_pattern, text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
        logger.debug("Stripped markdown fences from LLM response")

    # Find JSON object/array boundaries
    json_start = None
    for i, char in enumerate(text):
        if char in "{[":
            json_start = i
            break

    if json_start is not None:
        bracket_map = {"{": "}", "[": "]"}
        open_bracket = text[json_start]
        close_bracket = bracket_map[open_bracket]
        depth = 0

        for i in range(json_start, len(text)):
            if text[i] == open_bracket:
                depth += 1
            elif text[i] == close_bracket:
                depth -= 1
                if depth == 0:
                    text = text[json_start : i + 1]
                    # Strip trailing commas (common LLM error)
                    text = re.sub(r",\s*}", "}", text)
                    text = re.sub(r",\s*]", "]", text)
                    break

    # Validate it's actually JSON
    try:
        json.loads(text)
        if text != original:
            logger.debug("Cleaned JSON: removed %d chars", len(original) - len(text))
        return text
    except json.JSONDecodeError:
        logger.warning("JSON cleaning failed, returning original")
        return original
