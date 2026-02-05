"""Unified evidence bundle builder for LLM integration (Phase 0).

This module provides functionality to assemble all assessment data
into a single JSON-serialisable structure for future LLM input.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ams.core.config import RUBRIC_VERSION

if TYPE_CHECKING:
    from ams.core.models import BehaviouralEvidence, RuleResult


# Regex patterns for common PII
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_STUDENT_ID_PATTERN = re.compile(r"\b[Ss]?\d{6,9}\b")  # 6-9 digit student IDs
_NAME_IN_HEADER_PATTERN = re.compile(
    r"(?:Author|Name|Student|By)[\s:]*([A-Z][a-z]+ [A-Z][a-z]+)",
    re.IGNORECASE,
)


def sanitize_pii(text: str) -> str:
    """Replace emails, student IDs, and names with placeholders.
    
    This function removes personally identifiable information from
    code snippets before they are sent to external LLM APIs.
    
    Args:
        text: Raw text that may contain PII
        
    Returns:
        Text with PII replaced by placeholders
    """
    if not text:
        return text
    
    # Replace emails
    text = _EMAIL_PATTERN.sub("[EMAIL]", text)
    # Replace student IDs
    text = _STUDENT_ID_PATTERN.sub("[STUDENT_ID]", text)
    # Replace names in headers (e.g., "Author: John Smith")
    text = _NAME_IN_HEADER_PATTERN.sub(r"\1: [STUDENT_NAME]", text)
    
    return text


def sanitize_student_files(files: dict[str, str]) -> dict[str, str]:
    """Sanitize PII from all student file contents.
    
    Args:
        files: Dictionary mapping file paths to file contents
        
    Returns:
        Dictionary with sanitized file contents
    """
    return {path: sanitize_pii(content) for path, content in files.items()}


def build_evidence_bundle(
    rule_results: list["RuleResult"],
    static_scores: dict[str, float],
    behavioural_results: list["BehaviouralEvidence"],
    student_files: dict[str, str],
    profile_name: str = "",
    sanitize: bool = True,
) -> dict:
    """Build a unified JSON-serialisable evidence bundle for LLM input.
    
    This function assembles all assessment data into a single structure
    that can be passed to an LLM for feedback generation and partial
    mark determination in future phases.
    
    Args:
        rule_results: List of RuleResult objects from static assessors
        static_scores: Dictionary mapping component names to scores
        behavioural_results: List of BehaviouralEvidence from runtime tests
        student_files: Dictionary mapping file paths to file contents
        profile_name: Name of the assessment profile used
        sanitize: If True, remove PII from student files before bundling
    
    Returns:
        A JSON-serialisable dictionary containing all evidence
    """
    # Sanitize student files if requested
    files_to_bundle = sanitize_student_files(student_files) if sanitize else dict(student_files)
    
    # Sanitize evidence snippets in rule results
    sanitized_results = []
    for r in rule_results:
        result_dict = r.to_dict()
        if sanitize:
            result_dict["evidence_snippet"] = sanitize_pii(result_dict.get("evidence_snippet", ""))
            result_dict["context_before"] = sanitize_pii(result_dict.get("context_before", ""))
            result_dict["context_after"] = sanitize_pii(result_dict.get("context_after", ""))
        sanitized_results.append(result_dict)
    
    return {
        "rubric_version": RUBRIC_VERSION,
        "profile": profile_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule_results": sanitized_results,
        "categories": {},  # Reserved for Phase 1 - category summaries
        "weights": {},  # Reserved for Phase 2 - weight overrides
        "static_scores": dict(static_scores),
        "behavioural_results": [b.to_dict() for b in behavioural_results],
        "student_files": files_to_bundle,
    }


__all__ = [
    "build_evidence_bundle",
    "sanitize_pii",
    "sanitize_student_files",
    "RUBRIC_VERSION",
]

