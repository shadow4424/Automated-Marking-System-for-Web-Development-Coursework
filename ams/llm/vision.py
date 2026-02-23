"""Vision Analysis Module for Screenshot-Based Grading.

This module provides the VisionAnalyst class which uses Vision-capable LLMs
(e.g., Qwen2-VL) to analyze screenshots and detect layout/visual issues.

Phase C: Updated with Pydantic schemas for reliability.
Phase D: Added hash-based caching for performance.
Phase E: Deterministic gating — blank-image detection & static-context injection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np

from ams.core.config import LLM_CACHE_ENABLED, VISION_MAX_TOKENS
from ams.core.factory import get_llm_provider
from ams.llm.cache import RequestCache
from ams.llm.schemas import (
    VisionResult,
    VisionIssue,
    UXReviewResult,
    create_not_evaluated,
    create_pass,
    create_fail,
)
from ams.llm.prompts import VISION_SYSTEM_PROMPT, UX_REVIEW_SYSTEM_PROMPT, UX_REVIEW_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


# =============================================================================
# Vision Analyst
# =============================================================================

# ─── Blank-image detection thresholds ────────────────────────────────────────
_WHITE_PIXEL_THRESHOLD = 0.97   # fraction of near-white pixels to count as blank
_NEAR_WHITE_VALUE      = 245    # pixel channel value considered "near-white"
_VARIANCE_THRESHOLD    = 50.0   # whole-image variance below which → solid colour


def is_visually_empty(image_path: Path) -> bool:
    """Return *True* if the screenshot is blank / solid-colour / mostly white.

    Two independent heuristics (either triggers *True*):

    1. **Low variance** – the whole image has almost no colour variation,
       meaning it is a solid-colour rectangle (regardless of which colour).
    2. **Near-white dominance** – ≥97 % of pixels have *all three* RGB
       channels above 245, i.e. the page is essentially an empty white page.

    Uses PIL + numpy for efficiency.  If the image cannot be opened the
    function returns *False* so the LLM path is still attempted.
    """
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        arr = np.asarray(img, dtype=np.float32)

        # Heuristic 1 – overall variance
        if arr.var() < _VARIANCE_THRESHOLD:
            logger.info("is_visually_empty: variance %.1f < %.1f → blank", arr.var(), _VARIANCE_THRESHOLD)
            return True

        # Heuristic 2 – near-white pixel ratio
        white_mask = np.all(arr >= _NEAR_WHITE_VALUE, axis=-1)   # per-pixel
        white_ratio = white_mask.mean()
        if white_ratio >= _WHITE_PIXEL_THRESHOLD:
            logger.info("is_visually_empty: white ratio %.2f ≥ %.2f → blank", white_ratio, _WHITE_PIXEL_THRESHOLD)
            return True

        return False
    except Exception as exc:
        logger.debug("is_visually_empty: could not analyse %s – %s", image_path, exc)
        return False


class VisionAnalyst:
    """High-level interface for visual grading using Vision LLMs.
    
    This class provides methods to analyze screenshots against
    specific requirements and return structured VisionResult objects.
    
    Phase C Guarantees:
    - Never raises exceptions to callers
    - Always returns a valid VisionResult
    - Missing screenshots return NOT_EVALUATED
    - LLM errors return NOT_EVALUATED
    
    Phase E Additions:
    - ``is_visually_empty`` pre-check short-circuits blank pages
    - ``review_ux`` accepts an optional ``context_note`` from static analysis
    
    Example:
        >>> analyst = VisionAnalyst()
        >>> result = analyst.detect_layout_issues(
        ...     "screenshot.png",
        ...     "The page should have a blue header"
        ... )
        >>> print(result.status)  # "PASS", "FAIL", or "NOT_EVALUATED"
    """
    
    SYSTEM_PROMPT = VISION_SYSTEM_PROMPT

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

    # ------------------------------------------------------------------
    # UX Review (qualitative, non-scoring)
    # ------------------------------------------------------------------

    def review_ux(
        self,
        screenshot_path: str,
        page_name: str,
        context_note: Optional[str] = None,
    ) -> "UXReviewResult":
        """Provide qualitative UX/UI feedback for a single page screenshot.

        This is a non-scoring, advisory evaluation.  The returned
        :class:`UXReviewResult` is **never** fed into the scoring engine.

        Args:
            screenshot_path: Absolute path to the page screenshot.
            page_name: The HTML filename (e.g. ``index.html``).
            context_note: Optional factual note from static analysis
                (e.g. ``"No CSS files found"``).  When provided this is
                injected at the top of the system prompt so the model
                has deterministic grounding before it looks at the image.

        Returns:
            A :class:`UXReviewResult` with status and feedback text.
        """
        try:
            # ── Phase E: blank-image gate ────────────────────────────────
            path = Path(screenshot_path)
            if path.exists() and is_visually_empty(path):
                logger.info(
                    "UX review for %s: screenshot is visually empty — "
                    "returning NEEDS_IMPROVEMENT without calling the LLM.",
                    page_name,
                )
                return UXReviewResult(
                    page=page_name,
                    status="NEEDS_IMPROVEMENT",
                    feedback=(
                        "The page appears to be blank or failed to render. "
                        "No meaningful design elements were detected in the "
                        "screenshot. Ensure the HTML file contains visible "
                        "content and that CSS styles are linked correctly."
                    ),
                    improvement_suggestion=(
                        "Add an external CSS stylesheet with layout rules, a "
                        "colour scheme, and typographic styles so the page has "
                        "a clear visual design."
                    ),
                    screenshot=screenshot_path,
                    model="deterministic_gate",
                )

            return self._review_ux_internal(screenshot_path, page_name, context_note)
        except Exception as e:
            logger.warning("UX review failed for %s: %s", page_name, e)
            return UXReviewResult(
                page=page_name,
                status="NOT_EVALUATED",
                feedback=f"UX review could not be completed: {e}",
                screenshot=screenshot_path,
                model="unknown",
            )

    def _review_ux_internal(
        self,
        screenshot_path: str,
        page_name: str,
        context_note: Optional[str] = None,
    ) -> "UXReviewResult":
        path = Path(screenshot_path)
        if not path.exists():
            return UXReviewResult(
                page=page_name,
                status="NOT_EVALUATED",
                feedback="Screenshot not found.",
                screenshot=screenshot_path,
                model="unknown",
            )

        # UX reviews are NOT cached — each student submission must receive
        # its own fresh evaluation to avoid cross-student feedback bleed in
        # batch runs (the global cache.db is content-addressed so identical
        # screenshots from different students would collide).

        user_prompt = UX_REVIEW_USER_PROMPT_TEMPLATE.format(page_name=page_name)

        # ── Phase E: inject static-analysis context into the system prompt ──
        # The system prompt has a {context_note} placeholder that gets filled
        # with per-page factual grounding from static analysis.
        note = context_note if context_note else "No additional context."
        system_prompt = UX_REVIEW_SYSTEM_PROMPT.format(context_note=note)

        logger.info("Running UX review for: %s", page_name)
        response = self.provider.complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            image_path=str(path),
            json_mode=True,
            max_tokens=VISION_MAX_TOKENS,
        )

        if response.error:
            logger.error("UX review LLM error for %s: %s", page_name, response.error)
            return UXReviewResult(
                page=page_name,
                status="NOT_EVALUATED",
                feedback=f"LLM error: {response.error}",
                screenshot=str(path),
                model=type(self.provider).__name__,
            )

        try:
            return self._parse_ux_response(response.content, page_name, str(path))
        except ValueError as e:
            logger.error("Failed to parse UX review response for %s: %s", page_name, e)
            return UXReviewResult(
                page=page_name,
                status="NOT_EVALUATED",
                feedback=f"Could not parse model response.",
                screenshot=str(path),
                model=type(self.provider).__name__,
            )

    def _parse_ux_response(self, content: str, page_name: str, screenshot_path: str) -> "UXReviewResult":
        """Parse the LLM's UX review JSON into a UXReviewResult."""
        content = content.strip()
        data = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            if "```" in content:
                json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1).strip())
                    except json.JSONDecodeError:
                        pass

        if data and "feedback" in data:
            status = str(data.get("status", "NEEDS_IMPROVEMENT")).upper()
            if status not in ("PASS", "NEEDS_IMPROVEMENT", "FAIL"):
                status = "NEEDS_IMPROVEMENT"
            # Normalise legacy "FAIL" into the new label
            if status == "FAIL":
                status = "NEEDS_IMPROVEMENT"

            improvement = str(data.get("improvement_suggestion", "")).strip()

            return UXReviewResult(
                page=page_name,
                status=status,
                feedback=str(data["feedback"]),
                improvement_suggestion=improvement,
                screenshot=screenshot_path,
                model=type(self.provider).__name__,
            )

        raise ValueError(f"Could not parse UX review response: {content[:200]}")
    
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

__all__ = ["VisionAnalyst", "VisionResult", "VisionIssue", "UXReviewResult", "is_visually_empty"]


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

