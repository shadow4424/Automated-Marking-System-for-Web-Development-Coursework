"""Shared upload-validation helpers for the AMS web layer."""
from __future__ import annotations

import zipfile
from pathlib import Path


def validate_is_zipfile(file_path: str | Path) -> bool:
    """Return True only if *file_path* is a genuine ZIP archive."""
    path = Path(file_path)
    if not path.is_file():
        return False
    return zipfile.is_zipfile(path)
