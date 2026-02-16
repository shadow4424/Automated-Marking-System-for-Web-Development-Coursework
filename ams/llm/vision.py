"""Vision Analysis Module for Screenshot-Based Grading.

This module provides the VisionAnalyst class which uses Vision-capable LLMs
(e.g., Qwen2-VL) to analyze screenshots and detect layout/visual issues.

Phase C: Updated with Pydantic schemas for reliability.
Phase D: Added hash-based caching for performance.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from ams.core.config import LLM_CACHE_ENABLED
from ams.core.factory import get_llm_provider
from ams.llm.cache import RequestCache
from ams.llm.vision_schemas import (
    VisionResult,
    VisionIssue,
    create_not_evaluated,
    create_pass,
    create_fail,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Vision Analyst
# =============================================================================

class VisionAnalyst:
    """High-level interface for visual grading using Vision LLMs.
    
    This class provides methods to analyze screenshots against
    specific requirements and return structured VisionResult objects.
    
    Phase C Guarantees:
    - Never raises exceptions to callers
    - Always returns a valid VisionResult
    - Missing screenshots return NOT_EVALUATED
    - LLM errors return NOT_EVALUATED
    
    Example:
        >>> analyst = VisionAnalyst()
        >>> result = analyst.detect_layout_issues(
        ...     "screenshot.png",
        ...     "The page should have a blue header"
        ... )
        >>> print(result.status)  # "PASS", "FAIL", or "NOT_EVALUATED"
    """
    
    SYSTEM_PROMPT = """You are a strict web-design grading assistant.
Your ONLY job is to verify whether ONE specific visual requirement is met in the provided screenshot.

Decision rules (follow these EXACTLY):
- Output "PASS" ONLY if the requirement is clearly, visibly present AND functioning correctly in the screenshot.
- Output "FAIL" if the requirement is missing, broken, unstyled, partially implemented, or you cannot see evidence of it.
- If the page appears completely blank, white, unstyled, or lacks any CSS styling, output "FAIL" for ANY layout or styling requirement.
- If your reasoning describes the feature as "missing", "not present", "not found", "does not have", or any negation, you MUST output "FAIL". Never output "PASS" with a negative reason.

Response format — valid JSON, nothing else:
{"result": "PASS" or "FAIL", "reason": "One-sentence explanation"}

Do NOT include any text outside the JSON object."""

    def __init__(self, provider=None, cache_enabled: bool = None):
        """Initialize the VisionAnalyst.
        
        Args:
            provider: Optional LLMProvider instance. If None, uses factory.
            cache_enabled: Whether to cache results. Defaults to LLM_CACHE_ENABLED.
        """
        self._provider = provider
        self._cache_enabled = cache_enabled if cache_enabled is not None else LLM_CACHE_ENABLED
        self._cache = RequestCache() if self._cache_enabled else None
    
    @property
    def provider(self):
        """Lazy-load the provider to avoid import issues."""
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider
    
    def detect_layout_issues(
        self, 
        screenshot_path: str, 
        requirement_context: str
    ) -> VisionResult:
        """Analyze a screenshot against a specific visual requirement.
        
        This method NEVER raises exceptions. All errors result in
        a VisionResult with status=NOT_EVALUATED.
        
        Args:
            screenshot_path: Path to the screenshot image file.
            requirement_context: The requirement to check against.
            
        Returns:
            VisionResult with status PASS, FAIL, or NOT_EVALUATED.
        """
        try:
            return self._detect_internal(screenshot_path, requirement_context)
        except Exception as e:
            logger.warning(f"Vision analysis failed unexpectedly: {e}")
            return create_not_evaluated(
                reason=f"Unexpected error: {e}",
                screenshot_found=False,
            )
    
    def _detect_internal(
        self,
        screenshot_path: str,
        requirement_context: str
    ) -> VisionResult:
        """Internal detection logic that may raise exceptions."""
        path = Path(screenshot_path)
        
        # Handle missing screenshot
        if not path.exists():
            logger.warning(f"Screenshot not found: {screenshot_path}")
            return create_not_evaluated(
                reason="missing_screenshot",
                screenshot_path=str(screenshot_path),
                screenshot_found=False,
            )
        
        # Generate cache key from image hash + requirement
        cache_key = None
        if self._cache:
            image_hash = hashlib.md5(path.read_bytes()).hexdigest()
            cache_key = f"vision:{image_hash}:{requirement_context[:100]}"
            
            cached = self._cache.get(cache_key, system_prompt=self.SYSTEM_PROMPT, model="vision")
            if cached:
                logger.info(f"Vision cache HIT for {path.name}")
                try:
                    return self._parse_response(cached["content"], str(path))
                except ValueError:
                    pass  # Cache corrupted, proceed with LLM call
        
        user_prompt = f"""Requirement to verify:
{requirement_context}

Look at the screenshot carefully. Is this specific requirement visibly met?
- If YES and you can see clear evidence of it → {{"result": "PASS", "reason": "..."}}
- If NO, missing, broken, or unstyled → {{"result": "FAIL", "reason": "..."}}

Respond with JSON only."""

        logger.info(f"Analyzing screenshot: {path.name}")
        logger.debug(f"Requirement: {requirement_context}")
        
        response = self.provider.complete(
            prompt=user_prompt,
            system_prompt=self.SYSTEM_PROMPT,
            image_path=str(path),
            json_mode=True,
        )
        
        # Handle LLM errors
        if response.error:
            logger.error(f"Vision analysis LLM error: {response.error}")
            return create_not_evaluated(
                reason="llm_error",
                error=response.error,
                screenshot_found=True,
            )
        
        # Cache the response
        if self._cache and cache_key:
            self._cache.set(
                cache_key,
                system_prompt=self.SYSTEM_PROMPT,
                model="vision",
                response=response.content,
            )
        
        # Parse the JSON response
        try:
            result = self._parse_response(response.content, str(path))
            logger.info(f"Vision result: {result.status} - {result.reason}")
            return result
        except ValueError as e:
            logger.error(f"Failed to parse vision response: {e}")
            return create_not_evaluated(
                reason="parse_error",
                error=str(e),
                raw_response=response.content[:200],
                screenshot_found=True,
            )
    
    def check_responsiveness(
        self,
        desktop_screenshot: str,
        mobile_screenshot: str,
    ) -> VisionResult:
        """Compare desktop and mobile screenshots for responsive design.
        
        Args:
            desktop_screenshot: Path to desktop viewport screenshot.
            mobile_screenshot: Path to mobile viewport screenshot.
            
        Returns:
            VisionResult with responsiveness assessment.
        """
        # For now, analyze mobile screenshot with responsiveness requirement
        return self.detect_layout_issues(
            mobile_screenshot,
            "The page should be responsive and readable on mobile. "
            "Content should not overflow, text should be legible, "
            "and navigation should be accessible."
        )
    
    def _parse_response(self, content: str, screenshot_path: str) -> VisionResult:
        """Parse the LLM response into a VisionResult.
        
        Args:
            content: Raw LLM response string.
            screenshot_path: Path to the screenshot for metadata.
            
        Returns:
            VisionResult with PASS or FAIL status.
            
        Raises:
            ValueError: If parsing fails.
        """
        content = content.strip()
        
        # Try direct JSON parse
        data = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```" in content:
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1).strip())
                    except json.JSONDecodeError:
                        pass
        
        if data and "result" in data and "reason" in data:
            result_str = str(data["result"]).upper()
            reason = str(data["reason"])

            # Contradiction guard: if the reason text indicates a failure
            # but the model hallucinated "PASS", override to FAIL.
            if result_str == "PASS" and self._reason_contradicts_pass(reason):
                logger.warning(
                    "Vision status/reason contradiction detected — "
                    "overriding PASS → FAIL  (reason: %s)", reason,
                )
                result_str = "FAIL"
            
            if result_str == "PASS":
                return create_pass(
                    reason=reason,
                    screenshot=screenshot_path,
                    model=type(self.provider).__name__,
                )
            else:
                return create_fail(
                    reason=reason,
                    issues=[VisionIssue(description=reason, severity="FAIL")],
                    screenshot=screenshot_path,
                    model=type(self.provider).__name__,
                )
        
        raise ValueError(f"Could not parse response: {content[:200]}")

    # ------------------------------------------------------------------
    # Contradiction detection
    # ------------------------------------------------------------------
    _NEGATIVE_PATTERNS = re.compile(
        r"\b(?:"
        r"(?:does\s+not|doesn'?t|do\s+not|don'?t|is\s+not|isn'?t|are\s+not|aren'?t)"
        r"|not\s+(?:present|found|visible|implemented|applied|styled|detected|used|included)"
        r"|missing|absent|lacking|no\s+(?:media|style|layout|responsive|css)"
        r"|unstyled|broken|empty|blank"
        r")\b",
        re.IGNORECASE,
    )

    @classmethod
    def _reason_contradicts_pass(cls, reason: str) -> bool:
        """Return *True* if the reason text contains negative language that
        contradicts a PASS verdict.

        This is a safety net: even with a hardened prompt, small local
        models sometimes output PASS alongside a reason that clearly
        describes a missing or broken feature.
        """
        return bool(cls._NEGATIVE_PATTERNS.search(reason))


# =============================================================================
# Module Export
# =============================================================================

__all__ = ["VisionAnalyst", "VisionResult", "VisionIssue"]


# =============================================================================
# Demo / Standalone Test
# =============================================================================

if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    if len(sys.argv) < 2:
        print("Usage: python -m ams.llm.vision <screenshot.png> [requirement]")
        print("Example: python -m ams.llm.vision page.png 'Header should be blue'")
        sys.exit(1)
    
    screenshot = sys.argv[1]
    requirement = sys.argv[2] if len(sys.argv) > 2 else "The page should be visually correct and well-designed"
    
    analyst = VisionAnalyst()
    result = analyst.detect_layout_issues(screenshot, requirement)
    
    print(f"\nStatus: {result.status}")
    print(f"Reason: {result.reason}")
    print(f"Confidence: {result.confidence}")
    if result.meta:
        print(f"Meta: {result.meta}")

