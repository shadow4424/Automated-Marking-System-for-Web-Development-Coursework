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
]
