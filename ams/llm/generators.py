"""Phase B: LLM Feedback Reliability - Robust Generator."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from pydantic import ValidationError

from ams.core.llm_factory import get_llm_provider
from ams.llm.providers import LLMResponse
from ams.llm.schemas import FeedbackItem, LLMFeedback, create_fallback_feedback
from ams.llm.utils import clean_json_response
from ams.llm.prompts import FEEDBACK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class FeedbackGenerator:
    """Robust LLM feedback generator with strict validation."""

    def __init__(self, provider=None):
        """Initialise the generator. Args: provider: Optional LLMProvider instance. If None, uses factory default."""
        self._provider = provider

    @property
    def provider(self):
        """Lazy-load the LLM provider."""
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    def generate(self, evidence: Dict[str, Any]) -> LLMFeedback:
        """Generate validated feedback from evidence."""
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
        cleaned = clean_json_response(response.content)
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

        prompt = f"""Analyse this failed rule check and provide structured feedback.

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
        """Parse raw LLM response into validated LLMFeedback. Handles various LLM output formats and normalises them."""
        # Stop early if the provider returned an error.
        if "error" in raw_data and "summary" not in raw_data:
            raise ValueError(f"LLM returned error: {raw_data.get('error')}")

        # Normalise items if the LLM used different key names.
        items_raw = raw_data.get("items", [])
        if not isinstance(items_raw, list):
            items_raw = [items_raw] if items_raw else []

        # Parse items with validation
        validated_items = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            try:
                # Normalise severity to uppercase.
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
    "BatchFeedbackGenerator",
]


class BatchFeedbackGenerator:
    """Batch LLM feedback generator — consolidates multiple failed rules into one prompt."""

    def __init__(self, provider=None):
        self._provider = provider

    @property
    def provider(self):
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider

    def generate_batch(
        self, evidence_list: list[dict[str, Any]]
    ) -> dict[str, LLMFeedback]:
        """Generate feedback for multiple rules in a single LLM call."""
        if not evidence_list:
            return {}

        # Single item — delegate to standard generator
        if len(evidence_list) == 1:
            gen = FeedbackGenerator(provider=self._provider)
            fb = gen.generate(evidence_list[0])
            return {evidence_list[0]["rule_id"]: fb}

        rule_ids = [e["rule_id"] for e in evidence_list]

        try:
            return self._generate_internal(evidence_list, rule_ids)
        except Exception as e:
            logger.warning("Batch feedback generation failed, using fallbacks: %s", e)
            fallback = create_fallback_feedback(e)
            return {rid: fallback for rid in rule_ids}


    def _generate_internal(
        self,
        evidence_list: list[dict[str, Any]],
        rule_ids: list[str],
    ) -> dict[str, LLMFeedback]:
        from ams.llm.prompts import (
            BATCH_FEEDBACK_SYSTEM_PROMPT,
        )

        prompt = self._build_batch_prompt(evidence_list)

        response: LLMResponse = self.provider.complete(
            prompt=prompt,
            system_prompt=BATCH_FEEDBACK_SYSTEM_PROMPT,
            json_mode=True,
        )

        if response.error:
            raise RuntimeError(f"LLM provider error: {response.error}")

        cleaned = clean_json_response(response.content)
        raw_data = json.loads(cleaned)

        return self._parse_batch_response(raw_data, rule_ids)

    def _build_batch_prompt(self, evidence_list: list[dict[str, Any]]) -> str:
        from ams.llm.prompts import BATCH_FEEDBACK_USER_TEMPLATE

        blocks: list[str] = []
        for ev in evidence_list:
            snippet = ev.get("code_snippet", "")
            if len(snippet) > 500:
                snippet = snippet[:500] + "\n... [truncated]"
            blocks.append(
                f"---\n"
                f"Rule: {ev['rule_id']} | Category: {ev.get('category', 'unknown')}\n"
                f"Context: {ev.get('error_context', 'Rule check failed.')}\n"
                f"Code:\n```\n{snippet}\n```\n"
                f"---"
            )

        return BATCH_FEEDBACK_USER_TEMPLATE.format(
            count=len(evidence_list),
            rules_block="\n\n".join(blocks),
        )

    def _parse_batch_response(
        self,
        raw_data: dict[str, Any],
        rule_ids: list[str],
    ) -> dict[str, LLMFeedback]:
        results_list = raw_data.get("results", [])
        if not isinstance(results_list, list):
            results_list = [results_list] if results_list else []

        parsed: dict[str, LLMFeedback] = {}
        for item in results_list:
            if not isinstance(item, dict):
                continue
            rid = item.get("rule_id", "")
            if rid not in rule_ids:
                continue

            items_raw = item.get("items", [])
            if not isinstance(items_raw, list):
                items_raw = [items_raw] if items_raw else []

            validated_items: list[FeedbackItem] = []
            for fi in items_raw:
                if not isinstance(fi, dict):
                    continue
                try:
                    if "severity" in fi:
                        fi["severity"] = str(fi["severity"]).upper()
                        severity_map = {
                            "ERROR": "FAIL",
                            "WARNING": "WARN",
                            "INFORMATION": "INFO",
                            "NOTE": "INFO",
                        }
                        fi["severity"] = severity_map.get(
                            fi["severity"], fi["severity"]
                        )
                    validated_items.append(FeedbackItem(**fi))
                except (ValidationError, Exception):
                    continue

            parsed[rid] = LLMFeedback(
                summary=str(item.get("summary", ""))[:200],
                items=validated_items,
                meta={
                    "fallback": False,
                    "provider": type(self.provider).__name__,
                    "batch": True,
                },
            )

        # Fill missing rule_ids with fallbacks
        for rid in rule_ids:
            if rid not in parsed:
                parsed[rid] = create_fallback_feedback(
                    "Rule missing from batch LLM response"
                )

        return parsed
