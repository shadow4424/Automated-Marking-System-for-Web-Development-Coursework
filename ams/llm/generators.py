"""Phase B: LLM Feedback Reliability - Robust Generator.

Implements the FeedbackGenerator class with strict validation and
deterministic fallback handling. The system never crashes due to
bad LLM output - it always returns a valid LLMFeedback object.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ams.core.factory import get_llm_provider
from ams.llm.providers import LLMResponse
from ams.llm.schemas import FeedbackItem, LLMFeedback, create_fallback_feedback

logger = logging.getLogger(__name__)


# System prompt for structured feedback generation
FEEDBACK_SYSTEM_PROMPT = (
    "You are a backend service in an automated marking system. Follow formatting rules exactly. "
    "Output ONLY a raw JSON object with these keys: "
    '{"summary": "<brief 1-sentence summary>", "items": [{"severity": "FAIL|WARN|INFO", '
    '"message": "<feedback message>", "evidence_refs": ["file.ext:line"]}]}. '
    "No markdown, no code fences, no explanations. Use only the keys shown."
)


def _clean_json_response(text: str) -> str:
    """Extract valid JSON from potentially wrapped LLM response.
    
    Handles common LLM quirks like markdown fences and preambles.
    """
    if not text:
        return "{}"

    # Strip markdown code fences
    fence_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
    match = re.search(fence_pattern, text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()


    # Find JSON object boundaries
    json_start = text.find("{")
    if json_start != -1:
        depth = 0
        for i in range(json_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[json_start : i + 1]
                    # Strip trailing commas (common LLM error)
                    candidate = re.sub(r",\s*}", "}", candidate)
                    candidate = re.sub(r",\s*]", "]", candidate)
                    return candidate

    # Fallback: try cleaning the whole text if no outer braces found
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    return text



class FeedbackGenerator:
    """Robust LLM feedback generator with strict validation.
    
    Guarantees:
    - Always returns a valid LLMFeedback object
    - Never raises exceptions to callers
    - Validates all LLM output through Pydantic schemas
    - Falls back gracefully on any error
    
    Example:
        generator = FeedbackGenerator()
        feedback = generator.generate({"rule_id": "html.has_doctype", "code": "..."})
        # feedback is always a valid LLMFeedback, even if LLM failed
    """
    
    def __init__(self, provider=None):
        """Initialize the generator.
        
        Args:
            provider: Optional LLMProvider instance. If None, uses factory default.
        """
        self._provider = provider
    
    @property
    def provider(self):
        """Lazy-load the LLM provider."""
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider
    
    def generate(self, evidence: Dict[str, Any]) -> LLMFeedback:
        """Generate validated feedback from evidence.
        
        This method NEVER raises exceptions. All errors result in a
        deterministic fallback object.
        
        Args:
            evidence: Dictionary containing rule context and code snippets.
                Expected keys: rule_id, category, code_snippet, error_context
                
        Returns:
            LLMFeedback: Always returns a valid feedback object.
        """
        try:
            return self._generate_internal(evidence)
        except Exception as e:
            logger.warning(f"Feedback generation failed, using fallback: {e}")
            return create_fallback_feedback(e)
    
    def _generate_internal(self, evidence: Dict[str, Any]) -> LLMFeedback:
        """Internal generation logic that may raise exceptions."""
        # Step 1: Build compact prompt
        prompt = self._build_prompt(evidence)
        
        # Step 2: Call LLM provider
        response: LLMResponse = self.provider.complete(
            prompt=prompt,
            system_prompt=FEEDBACK_SYSTEM_PROMPT,
            json_mode=True,
        )
        
        if response.error:
            raise RuntimeError(f"LLM provider error: {response.error}")
        
        # Step 3: Clean and parse JSON
        cleaned = _clean_json_response(response.content)
        raw_data = json.loads(cleaned)
        
        # Step 4: Validate through Pydantic schema
        feedback = self._parse_response(raw_data)
        
        # Step 5: Add success metadata
        feedback.meta["fallback"] = False
        feedback.meta["provider"] = type(self.provider).__name__
        
        return feedback
    
    def _build_prompt(self, evidence: Dict[str, Any]) -> str:
        """Build a compact JSON prompt from evidence."""
        # Extract key fields, providing defaults
        rule_id = evidence.get("rule_id", "unknown_rule")
        category = evidence.get("category", "unknown")
        code_snippet = evidence.get("code_snippet", "")
        error_context = evidence.get("error_context", "Rule check failed.")
        
        # Truncate code if too long (keep LLM focused)
        if len(code_snippet) > 500:
            code_snippet = code_snippet[:500] + "\n... [truncated]"
        
        prompt = f"""Analyze this failed rule check and provide structured feedback.

Rule: {rule_id}
Category: {category}
Context: {error_context}

Code:
```
{code_snippet}
```

Respond with JSON only: {{"summary": "...", "items": [{{"severity": "FAIL|WARN|INFO", "message": "...", "evidence_refs": []}}]}}"""
        
        return prompt
    
    def _parse_response(self, raw_data: Dict[str, Any]) -> LLMFeedback:
        """Parse raw LLM response into validated LLMFeedback.
        
        Handles various LLM output formats and normalizes them.
        """
        # Handle case where LLM returns error object
        if "error" in raw_data and "summary" not in raw_data:
            raise ValueError(f"LLM returned error: {raw_data.get('error')}")
        
        # Normalize items if LLM used different key names
        items_raw = raw_data.get("items", [])
        if not isinstance(items_raw, list):
            items_raw = [items_raw] if items_raw else []
        
        # Parse items with validation
        validated_items = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            try:
                # Normalize severity to uppercase
                if "severity" in item:
                    item["severity"] = str(item["severity"]).upper()
                    # Map common variations
                    severity_map = {
                        "ERROR": "FAIL",
                        "WARNING": "WARN", 
                        "INFORMATION": "INFO",
                        "NOTE": "INFO",
                    }
                    item["severity"] = severity_map.get(item["severity"], item["severity"])
                
                validated_item = FeedbackItem(**item)
                validated_items.append(validated_item)
            except ValidationError as e:
                logger.debug(f"Skipping invalid feedback item: {e}")
                continue
        
        # Build the feedback object
        return LLMFeedback(
            summary=str(raw_data.get("summary", ""))[:200],
            items=validated_items,
            meta=raw_data.get("meta", {}),
        )


__all__ = [
    "FeedbackGenerator",
    "FEEDBACK_SYSTEM_PROMPT",
]
