"""Shared utility functions for LLM modules."""
from __future__ import annotations

import json
import logging
import re
import base64
import io

from ams.io.json_utils import parse_llm_json_block
from ams.llm.schemas import UXReviewResult, VisionIssue, VisionResult, create_fail, create_pass

logger = logging.getLogger(__name__)


def clean_json_response(text: str) -> str:
    """Extract valid JSON from a potentially wrapped LLM response."""
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


def encode_image_safely(image_path: str, max_size: int = 768) -> str:
    """Resize an image and compress it to JPEG before base64-encoding."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dependency-gated
        raise ImportError(
            "Pillow is required for safe image encoding. Install with: pip install pillow"
        ) from exc

    jpeg_quality = 85

    with Image.open(image_path) as img:
        original_dims = img.size

        if img.mode != "RGB":
            img = img.convert("RGB")

        if max(img.width, img.height) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=jpeg_quality)

        jpeg_bytes = buffer.tell()
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

        logger.info(
            "encode_image_safely: %s orig=%s final=%dx%d format=JPEG quality=%d payload=%d bytes",
            image_path,
            original_dims,
            img.width,
            img.height,
            jpeg_quality,
            jpeg_bytes,
        )
        return encoded


def parse_detect_layout_issues_response(
    content: str,
    screenshot_path: str,
    model_name: str,
    contradiction_checker=None,
) -> VisionResult:
    """Parse a vision layout-response payload into a VisionResult."""
    content = content.strip()
    data = None
    try:
        data = parse_llm_json_block(content)
    except (ValueError, json.JSONDecodeError):
        pass

    if data and "result" in data and "reason" in data:
        result_str = str(data["result"]).upper()
        reason = str(data["reason"])

        if contradiction_checker and result_str == "PASS" and contradiction_checker(reason):
            logger.warning(
                "Vision status/reason contradiction detected - overriding PASS to FAIL (reason: %s)",
                reason,
            )
            result_str = "FAIL"

        if result_str == "PASS":
            return create_pass(
                reason=reason,
                screenshot=screenshot_path,
                model=model_name,
            )

        return create_fail(
            reason=reason,
            issues=[VisionIssue(description=reason, severity="FAIL")],
            screenshot=screenshot_path,
            model=model_name,
        )

    raise ValueError(f"Could not parse response: {content[:200]}")


def parse_review_ux_response(
    content: str,
    page_name: str,
    screenshot_path: str,
    model_name: str,
) -> UXReviewResult:
    """Parse a UX-review payload into a UXReviewResult."""
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
            model=model_name,
        )

    raise ValueError(f"Could not parse UX review response: {content[:200]}")
