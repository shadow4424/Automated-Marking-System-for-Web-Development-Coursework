"""Phase C: Vision Reliability - Pydantic Schemas.

Provides strict validation models for Vision analysis results to ensure
deterministic parsing and prevent crashes from malformed LLM output.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class VisionIssue(BaseModel):
    """A single visual issue detected in a screenshot."""
    
    description: str = Field(
        ...,
        min_length=1,
        description="Description of the visual issue"
    )
    severity: Literal["FAIL", "WARN", "INFO"] = Field(
        default="WARN",
        description="Severity level of the issue"
    )
    
    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description cannot be empty")
        return v.strip()


class VisionResult(BaseModel):
    """Result of a vision analysis check with strict validation."""
    
    status: Literal["PASS", "FAIL", "NOT_EVALUATED"] = Field(
        ...,
        description="Overall result status"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0"
    )
    issues: List[VisionIssue] = Field(
        default_factory=list,
        description="List of detected issues"
    )
    meta: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )
    reason: str = Field(
        default="",
        description="Brief explanation of the result"
    )
    
    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        """Ensure confidence is within valid range."""
        return max(0.0, min(1.0, v))


def create_not_evaluated(reason: str, **extra_meta) -> VisionResult:
    """Create a NOT_EVALUATED result for missing screenshots or errors.
    
    Args:
        reason: Why the evaluation could not be performed.
        **extra_meta: Additional metadata to include.
        
    Returns:
        VisionResult with status=NOT_EVALUATED.
    """
    return VisionResult(
        status="NOT_EVALUATED",
        confidence=0.0,
        issues=[],
        reason=reason,
        meta={
            "reason": reason,
            **extra_meta,
        }
    )


def create_pass(reason: str = "Visual check passed", **extra_meta) -> VisionResult:
    """Create a PASS result."""
    return VisionResult(
        status="PASS",
        confidence=1.0,
        issues=[],
        reason=reason,
        meta=extra_meta,
    )


def create_fail(
    reason: str,
    issues: List[VisionIssue] | None = None,
    confidence: float = 1.0,
    **extra_meta
) -> VisionResult:
    """Create a FAIL result with detected issues."""
    return VisionResult(
        status="FAIL",
        confidence=confidence,
        issues=issues or [],
        reason=reason,
        meta=extra_meta,
    )


__all__ = [
    "VisionIssue",
    "VisionResult",
    "create_not_evaluated",
    "create_pass",
    "create_fail",
]
