"""ZIP extraction and file validation utilities."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Iterable, List, Optional, Tuple
from zipfile import ZipFile


def safe_extract_zip(zip_path: Path, dest_dir: Path, max_size_mb: int = 100) -> None:
    """Safely extract ZIP file with size and path validation."""
    max_size_bytes = max_size_mb * 1024 * 1024
    total_size = 0

    with ZipFile(zip_path, "r") as zf:
        # Validate ZIP file first
        for info in zf.infolist():
            # Check individual file size
            if info.file_size > max_size_bytes:
                raise ValueError(f"File {info.filename} exceeds maximum size limit")

            # Check total extracted size
            total_size += info.file_size
            if total_size > max_size_bytes:
                raise ValueError("Total extracted size would exceed maximum limit")

            member_path = PurePosixPath(info.filename)
            _validate_zip_entry(member_path)

        # Extract after validation
        for info in zf.infolist():
            member_path = PurePosixPath(info.filename)
            _validate_zip_entry(member_path)
            target = dest_dir.joinpath(*member_path.parts)

            # Additional path validation
            try:
                target.resolve().relative_to(dest_dir.resolve())
            except ValueError:
                raise ValueError(f"Zip entry would escape extraction directory: {info.filename}")

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    dst.write(src.read())


def _validate_zip_entry(member_path: PurePosixPath) -> None:
    """Reject zip entries that would escape the extraction root."""
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError("Zip entry would escape extraction directory")


def find_submission_root(extracted_dir: Path) -> Path:
    """Resolve the actual submission root within an extracted zip."""
    junk = {"__MACOSX", ".DS_Store", "Thumbs.db"}
    entries: List[Path] = [p for p in extracted_dir.iterdir() if not p.name.startswith(".") and p.name not in junk]
    top_level_dirs = [p for p in entries if p.is_dir()]
    top_level_files = [p for p in entries if p.is_file()]
    if len(top_level_dirs) == 1 and not top_level_files:
        return top_level_dirs[0]
    return extracted_dir


def validate_file_type(filename: str, allowed_extensions: Iterable[str] = (".zip",)) -> bool:
    """Validate file type by extension."""
    if not filename:
        return False
    filename_lower = filename.lower()
    return any(filename_lower.endswith(ext.lower()) for ext in allowed_extensions)


def validate_file_size(file_path: Path, max_size_mb: int = 25) -> Tuple[bool, Optional[str]]:
    """Validate file size."""
    max_size_bytes = max_size_mb * 1024 * 1024
    try:
        size = file_path.stat().st_size
        if size > max_size_bytes:
            return False, f"File size {size / 1024 / 1024:.2f} MB exceeds maximum {max_size_mb} MB"
        return True, None
    except OSError as exc:
        return False, f"Cannot read file: {exc}"


__all__ = [
    "safe_extract_zip",
    "find_submission_root",
    "validate_file_type",
    "validate_file_size",
]
