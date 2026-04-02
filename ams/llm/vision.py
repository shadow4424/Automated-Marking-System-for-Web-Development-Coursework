"""Vision Analysis Module for Screenshot-Based Grading."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np

from ams.core.config import LLM_CACHE_ENABLED, VISION_MAX_TOKENS
from ams.core.llm_factory import get_llm_provider
from ams.io.json_utils import parse_llm_json_block
from ams.llm.cache import RequestCache
from ams.llm.schemas import (
    VisionResult,
    VisionIssue,
    UXReviewResult,
    create_not_evaluated,
    create_pass,
    create_fail,
)
from ams.llm.prompts import (
    VISION_SYSTEM_PROMPT,
    UX_REVIEW_SYSTEM_PROMPT,
    build_detect_layout_issues_prompt,
    build_review_ux_prompt,
)
from ams.llm.utils import parse_detect_layout_issues_response, parse_review_ux_response

logger = logging.getLogger(__name__)


# Vision Analyst


# Blank-image detection thresholds.
_WHITE_PIXEL_THRESHOLD = 0.985  # Fraction of near-white pixels to count as blank
_NEAR_WHITE_VALUE      = 245    # Pixel channel value considered "near-white"
_VARIANCE_THRESHOLD    = 20.0   # Whole-image variance below which → solid colour


def is_visually_empty(image_path: Path) -> bool:
    """Return *True* if the screenshot is blank / solid-colour / mostly white."""
    try:
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        arr = np.asarray(img, dtype=np.float32)

        # Heuristic 1 – overall variance
        variance = arr.var()
        low_variance = variance < _VARIANCE_THRESHOLD

        # Heuristic 2 – near-white pixel ratio
        white_mask = np.all(arr >= _NEAR_WHITE_VALUE, axis=-1)   # Per-pixel
        white_ratio = white_mask.mean()
        high_white = white_ratio >= _WHITE_PIXEL_THRESHOLD

        # Both must agree to declare blank (AND logic)
        if low_variance and high_white:
            logger.info(
                "is_visually_empty: variance %.1f < %.1f AND white ratio %.3f ≥ %.3f → blank",
                variance, _VARIANCE_THRESHOLD, white_ratio, _WHITE_PIXEL_THRESHOLD,
            )
            return True

        if low_variance or high_white:
            logger.debug(
                "is_visually_empty: partial trigger (variance=%.1f %s %.1f, "
                "white=%.3f %s %.3f) — NOT blank (AND required)",
                variance, "<" if low_variance else ">=", _VARIANCE_THRESHOLD,
                white_ratio, ">=" if high_white else "<", _WHITE_PIXEL_THRESHOLD,
            )

        return False
    except Exception as exc:
        logger.debug("is_visually_empty: could not analyse %s – %s", image_path, exc)
        return False


class VisionAnalyst:
    """High-level interface for visual grading using Vision LLMs."""

    SYSTEM_PROMPT = VISION_SYSTEM_PROMPT

    def __init__(self, provider=None, cache_enabled: bool = None):
        """Initialise the VisionAnalyst."""
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
        """Analyse a screenshot against a specific visual requirement."""
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

        # Stop early if the screenshot file is missing.
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

        user_prompt = build_detect_layout_issues_prompt(requirement_context)

        logger.info(f"Analyzing screenshot: {path.name}")
        logger.debug(f"Requirement: {requirement_context}")

        response = self.provider.complete(
            prompt=user_prompt,
            system_prompt=self.SYSTEM_PROMPT,
            image_path=str(path),
            json_mode=True,
        )

        # Return a not-evaluated result when the LLM call fails.
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
            result = parse_detect_layout_issues_response(
                response.content,
                str(path),
                type(self.provider).__name__,
                contradiction_checker=self._reason_contradicts_pass,
            )
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
        """Compare desktop and mobile screenshots for responsive design."""
        # For now, analyse the mobile screenshot against the responsiveness requirement
        return self.detect_layout_issues(
            mobile_screenshot,
            "The page should be responsive and readable on mobile. "
            "Content should not overflow, text should be legible, "
            "and navigation should be accessible."
        )


    # UX Review (qualitative, non-scoring)


    def review_ux(
        self,
        screenshot_path: str,
        page_name: str,
        context_note: Optional[str] = None,
    ) -> "UXReviewResult":
        """Provide qualitative UX/UI feedback for a single page screenshot."""
        try:
            # Phase E: blank-image gate.
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
                    improvement_recommendation=(
                        "Verify the HTML file contains visible elements "
                        "(headings, paragraphs, images) and check that CSS "
                        "stylesheets are properly linked with <link> tags in "
                        "the <head> section."
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
        # Its own fresh evaluation to avoid cross-student feedback bleed in
        # Batch runs (the global cache.db is content-addressed so identical
        # Screenshots from different students would collide).

        user_prompt = build_review_ux_prompt(page_name)

        # Phase E: inject static-analysis context into the system prompt.
        # The system prompt has a {context_note} placeholder that gets filled
        # With per-page factual grounding from static analysis.
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
            return parse_review_ux_response(
                response.content,
                page_name,
                str(path),
                type(self.provider).__name__,
            )
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
            data = parse_llm_json_block(content)
        except (ValueError, json.JSONDecodeError):
            pass

        if data and "feedback" in data:
            status = str(data.get("status", "NEEDS_IMPROVEMENT")).upper()
            if status not in ("PASS", "NEEDS_IMPROVEMENT", "FAIL"):
                status = "NEEDS_IMPROVEMENT"
            # Normalise legacy "FAIL" into the new label
            if status == "FAIL":
                status = "NEEDS_IMPROVEMENT"

            improvement_rec = str(
                data.get("improvement_recommendation")
                or data.get("recommendation")
                or data.get("improvement_suggestion")
                or data.get("suggestion")
                or ""
            ).strip()

            return UXReviewResult(
                page=page_name,
                status=status,
                feedback=str(data["feedback"]),
                improvement_recommendation=improvement_rec,
                screenshot=screenshot_path,
                model=type(self.provider).__name__,
            )

        raise ValueError(f"Could not parse UX review response: {content[:200]}")

    def _parse_response(self, content: str, screenshot_path: str) -> VisionResult:
        """Parse the LLM response into a VisionResult."""
        content = content.strip()

        # Try direct JSON parse
        data = None
        try:
            data = parse_llm_json_block(content)
        except (ValueError, json.JSONDecodeError):
            pass

        if data and "result" in data and "reason" in data:
            result_str = str(data["result"]).upper()
            reason = str(data["reason"])

            # Contradiction guard: if the reason text indicates a failure
            # But the model hallucinated "PASS", override to FAIL.
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


    # Contradiction detection

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
        """Return *True* if the reason text contains negative language that contradicts a PASS verdict."""
        return bool(cls._NEGATIVE_PATTERNS.search(reason))


# Module Export


__all__ = ["VisionAnalyst", "VisionResult", "VisionIssue", "UXReviewResult", "is_visually_empty"]
