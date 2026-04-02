from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ams.io.json_utils import try_read_json


def get_report_metadata(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the report metadata mapping or an empty dict."""
    metadata = (report or {}).get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def get_submission_metadata(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return submission metadata from a report or an empty dict."""
    submission_metadata = get_report_metadata(report).get("submission_metadata")
    return dict(submission_metadata) if isinstance(submission_metadata, Mapping) else {}


def load_report_if_present(path: Path) -> dict[str, Any] | None:
    """Load a JSON report if the file exists and is valid."""
    if not path.exists():
        return None
    report = try_read_json(path, default=None)
    return report if isinstance(report, dict) else None


__all__ = [
    "get_report_metadata",
    "get_submission_metadata",
    "load_report_if_present",
]
