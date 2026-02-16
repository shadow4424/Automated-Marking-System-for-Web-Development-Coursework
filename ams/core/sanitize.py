"""PII sanitisation utilities.

Relocated from ``evidence_bundle.py`` which has been removed.
The functions in this module are used to strip personally identifiable
information from student work before it is sent to external LLM APIs.
"""
from __future__ import annotations

import re


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


__all__ = [
    "sanitize_pii",
    "sanitize_student_files",
]
