"""Shared upload-validation helpers for the AMS web layer.

All validation functions that are used by multiple blueprints (marking,
batch, API) live here so they can be tested and reused independently.
"""
from __future__ import annotations

import zipfile
from pathlib import Path


def validate_is_zipfile(file_path: str | Path) -> bool:
    """Return ``True`` only if *file_path* is a genuine ZIP archive.

    This goes beyond a simple extension check: it uses Python's built-in
    :func:`zipfile.is_zipfile`, which inspects the file's magic bytes and
    internal structure.

    Parameters
    ----------
    file_path:
        Absolute or relative path to the file on disk.

    Returns
    -------
    bool
        ``True`` when the file exists **and** is a valid ZIP archive.
    """
    path = Path(file_path)
    if not path.is_file():
        return False
    return zipfile.is_zipfile(path)
