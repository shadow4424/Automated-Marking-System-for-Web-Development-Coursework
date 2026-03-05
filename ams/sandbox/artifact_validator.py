"""Post-execution artifact integrity verification.

Verifies that expected artifacts (screenshots, output files) physically
exist and are well-formed after sandbox execution, before the pipeline
hands them to downstream consumers (LLM vision, report generation).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from ams.core.finding_ids import BROWSER
from ams.core.models import Finding, FindingCategory, Severity

logger = logging.getLogger(__name__)

# Minimum valid screenshot size in bytes (reject near-empty files)
_MIN_SCREENSHOT_BYTES = 500


def validate_screenshot(
    workspace_path: Path,
    source: str = "artifact_validator",
) -> tuple[Optional[Path], List[Finding]]:
    """Verify that a browser screenshot exists and is usable.

    Searches the standard artifact locations for a screenshot PNG.

    Returns:
        ``(screenshot_path_or_None, list_of_findings)``
        If the screenshot is valid, path is returned and findings is empty.
        If missing or corrupt, path is ``None`` and a ``BROWSER.CAPTURE_FAIL``
        finding is emitted.
    """
    findings: List[Finding] = []

    # Standard locations for browser screenshots
    search_dirs = [
        workspace_path / "artifacts" / "browser",
        workspace_path / "submission" / "artifacts" / "browser",
    ]

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for png in sorted(search_dir.glob("*.png")):
            try:
                size = png.stat().st_size
                if size >= _MIN_SCREENSHOT_BYTES:
                    logger.debug("Artifact validator: valid screenshot %s (%d bytes)", png, size)
                    return png, findings
                else:
                    logger.warning(
                        "Artifact validator: screenshot too small %s (%d bytes)",
                        png, size,
                    )
            except OSError as exc:
                logger.warning("Artifact validator: cannot stat %s: %s", png, exc)

    # No valid screenshot found
    findings.append(Finding(
        id=BROWSER.CAPTURE_FAIL,
        category="browser",
        message=(
            "Browser screenshot is missing or corrupt. "
            "Visual analysis and UX review will be skipped for this submission."
        ),
        severity=Severity.WARN,
        evidence={
            "searched_dirs": [str(d) for d in search_dirs],
            "min_size_bytes": _MIN_SCREENSHOT_BYTES,
        },
        source=source,
        finding_category=FindingCategory.EVIDENCE,
    ))

    logger.warning("Artifact validator: no valid screenshot found in %s", workspace_path)
    return None, findings


__all__ = ["validate_screenshot"]
