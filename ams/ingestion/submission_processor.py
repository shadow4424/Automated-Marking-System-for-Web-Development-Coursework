"""Submission ingestion processor — secondary fail-safe validation.

This module is called by the core extraction engine before attempting to
unpack a submission archive.  It re-validates that the file is a genuine
ZIP so that even if the web-layer check is bypassed (e.g. via the CLI
tools or a future API) the system will never attempt to extract a
non-ZIP file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ams.web.helpers import validate_is_zipfile

logger = logging.getLogger(__name__)


class InvalidSubmissionError(Exception):
    """Raised when a submission file fails ingestion validation."""


def validate_submission_archive(file_path: str | Path) -> Path:
    """Validate that *file_path* is a genuine ZIP archive.

    Parameters
    ----------
    file_path:
        Path to the uploaded / downloaded archive.

    Returns
    -------
    Path
        The resolved :class:`~pathlib.Path` of the validated file.

    Raises
    ------
    InvalidSubmissionError
        If the file does not exist or is not a valid ZIP archive.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise InvalidSubmissionError(f"Submission file not found: {path}")

    if not validate_is_zipfile(path):
        logger.warning(
            "Ingestion fail-safe: rejected non-ZIP file %s", path,
        )
        raise InvalidSubmissionError(
            f"File is not a valid ZIP archive: {path.name}"
        )

    return path
