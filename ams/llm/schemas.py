"""Phase B: LLM Feedback Reliability - Pydantic Schemas.

Provides strict validation models for LLM feedback to ensure deterministic
parsing and prevent crashes from malformed LLM output.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Any

from pydantic import BaseModel, Field, field_validator


class FeedbackItem(BaseModel):
    """A single feedback item with strict validation."""
    
    severity: Literal["FAIL", "WARN", "INFO"] = Field(
        ..., 
        description="Severity level of the feedback"
    )
    message: str = Field(
        ..., 
        min_length=1, 
        description="Non-empty feedback message"
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        description="List of file names/line references"
    )
    
    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("message cannot be empty or whitespace-only")
        return v.strip()


class LLMFeedback(BaseModel):
    """Complete LLM feedback response with validation."""
    
    summary: str = Field(
        default="",
        max_length=200,
        description="Brief summary of the feedback (max 200 chars)"
    )
    items: List[FeedbackItem] = Field(
        default_factory=list,
        description="List of feedback items"
    )
    meta: Dict[str, Any] = Field(
        default_factory=lambda: {"fallback": False},
        description="Metadata about the feedback generation"
    )
    
    @field_validator("summary")
    @classmethod
    def truncate_summary(cls, v: str) -> str:
        """Ensure summary doesn't exceed 200 characters."""
        if v and len(v) > 200:
            return v[:197] + "..."
        return v


def create_fallback_feedback(error: Exception | str) -> LLMFeedback:
    """Create a deterministic fallback feedback object.
    
    Used when LLM generation fails for any reason (timeout, parse error, etc.).
    
    Args:
        error: The exception or error message that caused the fallback.
        
    Returns:
        A valid LLMFeedback object with fallback=True in metadata.
    """
    error_str = str(error) if isinstance(error, Exception) else error
    return LLMFeedback(
        summary="Automated feedback could not be generated at this time.",
        items=[],
        meta={
            "fallback": True,
            "error": error_str,
        }
    )


__all__ = [
    "FeedbackItem",
    "LLMFeedback", 
    "create_fallback_feedback",
    # Vision schemas
    "VisionIssue",
    "VisionResult",
    "create_not_evaluated",
    "create_pass",
    "create_fail",
    # UX Review
    "UXReviewResult",
]


# ---------------------------------------------------------------------------
# Vision analysis schemas (formerly ams.llm.vision_schemas)
# ---------------------------------------------------------------------------

class VisionIssue(BaseModel):
    """A single visual issue detected in a screenshot."""

    description: str = Field(..., min_length=1, description="Description of the visual issue")
    severity: Literal["FAIL", "WARN", "INFO"] = Field(default="WARN", description="Severity level")

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description cannot be empty")
        return v.strip()


class VisionResult(BaseModel):
    """Result of a vision analysis check with strict validation."""

    status: Literal["PASS", "FAIL", "NOT_EVALUATED"] = Field(..., description="Overall result status")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score 0.0–1.0")
    issues: List[VisionIssue] = Field(default_factory=list, description="Detected issues")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    reason: str = Field(default="", description="Brief explanation of the result")

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


def create_not_evaluated(reason: str, **extra_meta: Any) -> VisionResult:
    """Create a NOT_EVALUATED result for missing screenshots or errors."""
    return VisionResult(
        status="NOT_EVALUATED", confidence=0.0, issues=[], reason=reason,
        meta={"reason": reason, **extra_meta},
    )


def create_pass(reason: str = "Visual check passed", **extra_meta: Any) -> VisionResult:
    """Create a PASS result."""
    return VisionResult(status="PASS", confidence=1.0, issues=[], reason=reason, meta=extra_meta)


def create_fail(
    reason: str, issues: List[VisionIssue] | None = None,
    confidence: float = 1.0, **extra_meta: Any,
) -> VisionResult:
    """Create a FAIL result with detected issues."""
    return VisionResult(
        status="FAIL", confidence=confidence, issues=issues or [],
        reason=reason, meta=extra_meta,
    )


# ---------------------------------------------------------------------------
# UX Review schema (non-scoring, qualitative feedback)
# ---------------------------------------------------------------------------

class UXReviewResult(BaseModel):
    """Result of a UX/UI qualitative review for a single page.

    This is explicitly *non-scoring* — it is advisory feedback only.
    """

    page: str = Field(..., description="HTML filename reviewed (e.g. index.html)")
    status: Literal["PASS", "NEEDS_IMPROVEMENT", "NOT_EVALUATED"] = Field(
        ..., description="Overall UX verdict"
    )
    feedback: str = Field(default="", description="Qualitative UX feedback text")
    improvement_suggestion: str = Field(default="", description="One specific, actionable suggestion for the student")
    screenshot: str = Field(default="", description="Path to the screenshot analysed")
    model: str = Field(default="", description="Name of the model that produced this review")
