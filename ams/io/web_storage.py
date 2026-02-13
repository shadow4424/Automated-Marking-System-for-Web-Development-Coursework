from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Optional, Tuple, List
from zipfile import ZipFile

from ams.io.metadata import MetadataValidator, SubmissionMetadata


def get_runs_root(app) -> Path:
    root = app.config.get("AMS_RUNS_ROOT") or "ams_web_runs"
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    return root_path


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
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError("Zip entry would escape extraction directory")


def find_submission_root(extracted_dir: Path) -> Path:
    """Resolve the actual submission root within an extracted zip.

    If there is exactly one top-level directory and no files, descend into it.
    Otherwise, return the extracted_dir.
    """
    junk = {"__MACOSX", ".DS_Store", "Thumbs.db"}
    entries: List[Path] = [p for p in extracted_dir.iterdir() if not p.name.startswith(".") and p.name not in junk]
    top_level_dirs = [p for p in entries if p.is_dir()]
    top_level_files = [p for p in entries if p.is_file()]
    if len(top_level_dirs) == 1 and not top_level_files:
        return top_level_dirs[0]
    return extracted_dir


def save_run_info(run_dir: Path, info: Mapping[str, object]) -> None:
    """Save run info with tamper-resistant JSON formatting."""
    info_path = run_dir / "run_info.json"
    # Use consistent formatting for tamper detection
    info_path.write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")


def save_metadata(run_dir: Path, metadata: SubmissionMetadata) -> None:
    """Save submission metadata in a tamper-resistant format."""
    metadata_path = run_dir / "metadata.json"
    metadata_dict = metadata.to_dict()
    # Add integrity check
    metadata_dict["_integrity"] = _compute_metadata_hash(metadata_dict)
    metadata_path.write_text(json.dumps(metadata_dict, indent=2, sort_keys=True), encoding="utf-8")


def load_metadata(run_dir: Path) -> Optional[SubmissionMetadata]:
    """Load submission metadata and verify integrity."""
    metadata_path = run_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        stored_hash = data.pop("_integrity", None)
        
        # Verify integrity
        computed_hash = _compute_metadata_hash(data)
        if stored_hash != computed_hash:
            # Metadata may have been tampered with
            return None
        
        return SubmissionMetadata.from_dict(data)
    except Exception:
        return None


def _compute_metadata_hash(metadata_dict: dict) -> str:
    """Compute hash for metadata integrity checking."""
    import hashlib
    # Create deterministic string representation
    metadata_str = json.dumps(metadata_dict, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(metadata_str.encode('utf-8')).hexdigest()[:16]


def load_run_info(run_dir: Path):
    """Load run metadata from run_info.json file.
    
    Args:
        run_dir: Path to the run directory containing run_info.json
        
    Returns:
        Dictionary containing run metadata, or None if file doesn't exist
    """
    info_path = run_dir / "run_info.json"
    if not info_path.exists():
        return None
    return json.loads(info_path.read_text(encoding="utf-8"))


def list_runs(runs_root: Path) -> list[dict]:
    """List all runs, searching recursively through the nested directory structure."""
    runs: list[dict] = []
    if not runs_root.exists():
        return runs
    
    # Search recursively for run_info.json files (marker for a run directory)
    for run_info_path in runs_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        info = load_run_info(run_dir)
        if info:
            info["id"] = run_dir.name
            info["_run_dir"] = str(run_dir)  # Store full path for lookups
            
            # Try to load score from report.json (single runs)
            report_path = run_dir / "report.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    scores = report.get("scores", {})
                    if scores and "overall" in scores:
                        # Store score as percentage (0-100) for dashboard display
                        info["score"] = scores["overall"] * 100
                except Exception:
                    pass
            
            # For batch runs, try to load average score from batch_summary.json
            if info.get("mode") == "batch" and info.get("score") is None:
                batch_summary_path = run_dir / "batch_summary.json"
                if batch_summary_path.exists():
                    try:
                        batch_summary = json.loads(batch_summary_path.read_text(encoding="utf-8"))
                        overall_stats = batch_summary.get("summary", {}).get("overall_stats", {}) or {}
                        mean_score = overall_stats.get("mean")
                        if mean_score is not None:
                            # Store score as percentage (0-100) for dashboard display
                            info["score"] = mean_score * 100
                    except Exception:
                        pass
            
            index_path = run_dir / "run_index.json"
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    info["submissions"] = index.get("submissions", [])
                except Exception:
                    info["submissions"] = []
            runs.append(info)
    
    # Sort by run id (timestamp-based) descending
    runs.sort(key=lambda r: r.get("id", ""), reverse=True)
    return runs


def find_run_by_id(runs_root: Path, run_id: str) -> Optional[Path]:
    """Find a run directory by its ID, searching recursively.
    
    Args:
        runs_root: The root directory for runs.
        run_id: The run ID to find.
        
    Returns:
        The Path to the run directory, or None if not found.
    """
    if not runs_root.exists():
        return None
    
    # First, check if it exists directly under runs_root (flat structure)
    direct_path = runs_root / run_id
    if direct_path.exists() and (direct_path / "run_info.json").exists():
        return direct_path
    
    # Search recursively for the run_id directory
    for run_info_path in runs_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        if run_dir.name == run_id:
            return run_dir
    
    return None


def allowed_download(filename: str, allowed: Iterable[str]) -> bool:
    allowed_set = set(allowed)
    return filename in allowed_set or any(filename.startswith(prefix) for prefix in allowed_set)


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
    "store_submission_with_metadata",
    "validate_file_type",
    "validate_file_size",
]
