"""Submission unpacking and normalisation utilities."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable, List

from .models import SubmissionContext


class NormalisationError(Exception):
    """Raised when a submission cannot be safely unpacked."""


class SubmissionNormaliser:
    """Normalize a submission by safely extracting it and recording metadata."""

    def __init__(self, workspace: Path | str = "submissions") -> None:
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def normalize(self, submission_zip: Path | str) -> SubmissionContext:
        submission_zip = Path(submission_zip)
        if not submission_zip.exists():
            raise NormalisationError(f"Missing submission archive: {submission_zip}")

        target_dir = self.workspace / submission_zip.stem
        # Ensure reproducible runs by clearing existing contents for the same submission id.
        if target_dir.exists():
            for path in sorted(target_dir.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        target_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(submission_zip, "r") as zf:
            self._safe_extract_all(zf, target_dir)

        normalized_files = self._collect_files(target_dir)
        context = SubmissionContext(
            submission_zip=submission_zip,
            extracted_path=target_dir,
            normalized_files=normalized_files,
        )
        context.add_log(f"Extracted {len(normalized_files)} files to {target_dir}")
        return context

    def _safe_extract_all(self, zip_file: zipfile.ZipFile, target_dir: Path) -> None:
        for member in zip_file.infolist():
            self._safe_extract_member(zip_file, member, target_dir)

    def _safe_extract_member(self, zip_file: zipfile.ZipFile, member: zipfile.ZipInfo, target_dir: Path) -> None:
        # Prevent directory traversal attacks by verifying resolved paths.
        resolved_path = (target_dir / member.filename).resolve()
        if not str(resolved_path).startswith(str(target_dir.resolve())):
            raise NormalisationError(f"Unsafe path detected in archive: {member.filename}")
        zip_file.extract(member, target_dir)

    def _collect_files(self, base_dir: Path) -> List[Path]:
        files: List[Path] = []
        for path in sorted(base_dir.rglob("*")):
            if path.is_file():
                files.append(path)
        return files


def iter_files_with_suffix(files: Iterable[Path], suffixes: tuple[str, ...]) -> List[Path]:
    """Filter files by suffix while preserving deterministic ordering."""

    suffixes = tuple(suffix.lower() for suffix in suffixes)
    return [path for path in files if path.suffix.lower() in suffixes]
