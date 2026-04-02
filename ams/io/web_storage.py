from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Tuple, List

from ams.core.attempts import attempt_maps, sync_attempts_from_storage
from ams.io.fs_utils import _prune_empty_parents, _remove_path_within
from ams.io.json_utils import read_json_file, try_read_json, write_json_file
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.io.zip_handler import (  # noqa: F401 — re-exported for backward compat
    safe_extract_zip,
    find_submission_root,
    validate_file_type,
    validate_file_size,
)


# Return the configured runs root and create it if needed.
def get_runs_root(app) -> Path:
    root = app.config.get("AMS_RUNS_ROOT") or "ams_web_runs"
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    return root_path


# Create a run directory for a new submission.
def create_run_dir(
    runs_root: Path,
    mode: str,
    profile: str,
    metadata: Optional[SubmissionMetadata] = None
) -> Tuple[str, Path]:
    """Create a run directory with optional metadata-based structure."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(4)

    # If metadata provided, use student_id and assignment_id in directory structure
    if metadata:
        # Sanitize identifiers for directory names
        student_id_safe = MetadataValidator.sanitize_identifier(metadata.student_id)
        assignment_id_safe = MetadataValidator.sanitize_identifier(metadata.assignment_id)

        # Create nested structure: assignment_id/student_id/run_id
        assignment_dir = runs_root / assignment_id_safe
        student_dir = assignment_dir / student_id_safe
        student_dir.mkdir(parents=True, exist_ok=True)

        run_id = f"{timestamp}_{mode}_{profile}_{suffix}"
        run_dir = student_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Default flat structure
        run_id = f"{timestamp}_{mode}_{profile}_{suffix}"
        run_dir = runs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

    return run_id, run_dir


# Write run metadata to disk.
def save_run_info(run_dir: Path, info: Mapping[str, object]) -> None:
    """Save run info with tamper-resistant JSON formatting."""
    write_json_file(run_dir / "run_info.json", info, indent=2, sort_keys=True)


# Write submission metadata to disk with an integrity marker.
def save_metadata(run_dir: Path, metadata: SubmissionMetadata) -> None:
    """Save submission metadata in a tamper-resistant format."""
    metadata_path = run_dir / "metadata.json"
    metadata_dict = metadata.to_dict()
    # Add integrity check
    metadata_dict["_integrity"] = _compute_metadata_hash(metadata_dict)
    write_json_file(metadata_path, metadata_dict, indent=2, sort_keys=True)


# Load submission metadata and reject tampered files.
def load_metadata(run_dir: Path) -> Optional[SubmissionMetadata]:
    """Load submission metadata and verify integrity."""
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        return None

    try:
        data = read_json_file(metadata_path)
        stored_hash = data.pop("_integrity", None)

        # Verify integrity
        computed_hash = _compute_metadata_hash(data)
        if stored_hash != computed_hash:
            # Metadata may have been tampered with
            return None

        return SubmissionMetadata.from_dict(data)
    except Exception:
        return None


# Build the metadata integrity hash.
def _compute_metadata_hash(metadata_dict: dict) -> str:
    """Compute hash for metadata integrity checking."""
    import hashlib
    # Create deterministic string representation
    metadata_str = json.dumps(metadata_dict, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(metadata_str.encode('utf-8')).hexdigest()[:16]


from ams.io.run_listing import (  # noqa: F401 — re-exports
    load_run_info,
    list_runs,
    find_run_by_id,
    extract_review_flags_from_report,
)

def allowed_download(filename: str, allowed: Iterable[str]) -> bool:
    allowed_set = set(allowed)
    return filename in allowed_set or any(filename.startswith(prefix) for prefix in allowed_set)


# Store an uploaded submission and its metadata.
def store_submission_with_metadata(
    runs_root: Path,
    mode: str,
    profile: str,
    metadata: SubmissionMetadata,
    zip_file: Path,
    versioned: bool = True
) -> Tuple[str, Path]:
    """Store submission with metadata, preventing overwrites and supporting versioning."""
    # Sanitize identifiers
    student_id_safe = MetadataValidator.sanitize_identifier(metadata.student_id)
    assignment_id_safe = MetadataValidator.sanitize_identifier(metadata.assignment_id)

    # Create directory structure
    assignment_dir = runs_root / assignment_id_safe
    student_dir = assignment_dir / student_id_safe
    student_dir.mkdir(parents=True, exist_ok=True)

    # Check for existing submissions if versioned
    if versioned:
        existing_runs = [d for d in student_dir.iterdir() if d.is_dir()]
        metadata.version = len(existing_runs) + 1

    # Create run directory
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(4)
    run_id = f"{timestamp}_{mode}_{profile}_{suffix}"
    run_dir = student_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Compute file hash
    metadata.file_hash = MetadataValidator.compute_file_hash(zip_file)

    # Save metadata
    save_metadata(run_dir, metadata)

    # Copy zip file with sanitized name
    sanitized_filename = MetadataValidator.sanitize_filename(metadata.original_filename)
    stored_zip = run_dir / sanitized_filename
    import shutil
    shutil.copy2(zip_file, stored_zip)

    return run_id, run_dir



_LEGACY_BATCH_FILES = (
    "component_means.csv",
    "failure_reasons_frequency.csv",
    "findings_frequency.csv",
    "score_buckets.csv",
)
_LEGACY_BATCH_DIRS = (
    "batch_inputs",
    "legacy_batch_inputs",
)
_LEGACY_BATCH_PATTERNS = (
    "batch_reports_*.zip",
)
_TRANSIENT_SUBMISSION_DIRS = (
    "extracted",
    "rerun_source",
    "source_files",
)


# Remove batch-run storage that is no longer needed.
def cleanup_batch_run_storage(run_dir: Path, run_info: Mapping[str, object] | None = None) -> None:
    """Remove transient and legacy batch artefacts from a completed batch run."""
    root = run_dir.resolve()
    info = dict(run_info or load_run_info(root) or {})

    for dirname in _LEGACY_BATCH_DIRS:
        _remove_path_within(root, root / dirname)

    original_filename = str(info.get("original_filename") or "").strip()
    if original_filename.lower().endswith(".zip"):
        _remove_path_within(root, root / original_filename)
    _remove_path_within(root, root / "batch_submissions.zip")

    for filename in _LEGACY_BATCH_FILES:
        _remove_path_within(root, root / filename)
    for pattern in _LEGACY_BATCH_PATTERNS:
        for candidate in root.glob(pattern):
            _remove_path_within(root, candidate)

    runs_dir = root / "runs"
    if runs_dir.is_dir():
        for submission_dir in runs_dir.iterdir():
            if not submission_dir.is_dir():
                continue
            has_canonical_submission = (submission_dir / "submission").is_dir()
            for dirname in _TRANSIENT_SUBMISSION_DIRS:
                if dirname in {"extracted", "source_files"} and not has_canonical_submission:
                    continue
                _remove_path_within(root, submission_dir / dirname)


# Remove stored files for one assignment.
def purge_assignment_storage(runs_root: Path, assignment_id: str) -> int:
    """Remove filesystem artefacts associated with a deleted assignment."""
    assignment_value = str(assignment_id or "").strip()
    if not assignment_value:
        return 0

    root = runs_root.resolve()
    assignment_safe = MetadataValidator.sanitize_identifier(assignment_value)
    removed: set[Path] = set()

    assignment_dir = root / assignment_safe
    if assignment_safe and assignment_dir.exists():
        shutil.rmtree(assignment_dir, ignore_errors=True)
        removed.add(assignment_dir.resolve())

    def _matches_assignment(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        return (
            text == assignment_value
            or MetadataValidator.sanitize_identifier(text) == assignment_safe
        )

    for run_info_path in list(root.rglob("run_info.json")):
        run_dir = run_info_path.parent.resolve()
        if any(run_dir == removed_dir or removed_dir in run_dir.parents for removed_dir in removed):
            continue

        info = load_run_info(run_dir)
        if not info:
            continue

        if _matches_assignment(info.get("assignment_id")):
            shutil.rmtree(run_dir, ignore_errors=True)
            removed.add(run_dir)
            _prune_empty_parents(run_dir.parent, stop_at=root)
            continue

        submissions = list(info.get("submissions", []) or [])
        if any(_matches_assignment(submission.get("assignment_id")) for submission in submissions if isinstance(submission, Mapping)):
            shutil.rmtree(run_dir, ignore_errors=True)
            removed.add(run_dir)
            _prune_empty_parents(run_dir.parent, stop_at=root)

    return len(removed)


__all__ = [
    "get_runs_root",
    "create_run_dir",
    "safe_extract_zip",
    "save_run_info",
    "load_run_info",
    "save_metadata",
    "load_metadata",
    "list_runs",
    "find_run_by_id",
    "allowed_download",
    "find_submission_root",
    "cleanup_batch_run_storage",
    "purge_assignment_storage",
    "store_submission_with_metadata",
    "validate_file_type",
    "validate_file_size",
]
