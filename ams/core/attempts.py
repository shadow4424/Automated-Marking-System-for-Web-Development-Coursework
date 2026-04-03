from __future__ import annotations

import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ams.core.database import get_db
from ams.io.json_utils import try_read_json, write_json_file
from ams.io.metadata import MetadataValidator

# Functions for managing submission attempts
def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# Generate a new attempt identifier with a timestamp and random component for uniqueness.
def generate_attempt_id(prefix: str = "attempt") -> str:
    """Generate a new attempt identifier."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}_{prefix}_{secrets.token_hex(4)}"

# Convert a database row into an attempt dictionary.
def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a database row into an attempt dictionary."""
    if row is None:
        return {}
    data = dict(row)
    data["attempt_number"] = int(data.get("attempt_number") or 0)
    data["manual_review_required"] = bool(data.get("manual_review_required"))
    data["is_active"] = bool(data.get("is_active"))
    return data

# Check whether an attempt belongs to the current runs root by examining its run_dir.
def _attempt_belongs_to_root(attempt: dict[str, Any], runs_root: Path | None) -> bool:
    """Check whether an attempt belongs to the current runs root."""
    if runs_root is None:
        return True
    run_dir_value = str(attempt.get("run_dir") or "").strip()
    if not run_dir_value:
        return False
    try:
        Path(run_dir_value).resolve().relative_to(runs_root.resolve())
        return True
    except Exception:
        return False

# Filter attempts to those under the current runs root.
def filter_attempts_for_root(attempts: list[dict[str, Any]], runs_root: Path | None) -> list[dict[str, Any]]:
    """Filter attempts to those under the current runs root."""
    return [attempt for attempt in attempts if _attempt_belongs_to_root(attempt, runs_root)]

# Create the storage directory for an attempt.
def create_attempt_storage_dir(
    runs_root: Path,
    assignment_id: str,
    student_id: str,
    attempt_number: int,
    attempt_id: str,
) -> Path:
    """Create the storage directory for an attempt."""
    assignment_safe = MetadataValidator.sanitize_identifier(assignment_id)
    student_safe = MetadataValidator.sanitize_identifier(student_id)
    student_root = runs_root / assignment_safe / student_safe
    attempt_dir = student_root / "attempts" / f"{attempt_number:03d}_{attempt_id}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    return attempt_dir

# Fetch an attempt by its identifier.
def get_attempt(attempt_id: str) -> dict[str, Any] | None:
    """Fetch an attempt by its identifier."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM submission_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()

# Fetch an attempt by its run and submission reference.
def get_attempt_by_run_reference(
    run_id: str,
    batch_submission_id: str | None = None,
    runs_root: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch an attempt by its run and submission reference."""
    conn = get_db()
    # Finds attempts matching the run_id and batch_submission_id.
    try:
        rows = conn.execute(
            """
            SELECT * FROM submission_attempts
            WHERE run_id = ? AND batch_submission_id = ?
            """,
            (str(run_id or ""), str(batch_submission_id or "")),
        ).fetchall()
        # If rows are found, filter them for the current runs root and return the first match.
        if rows:
            candidates = [_row_to_dict(row) for row in rows]
            filtered = filter_attempts_for_root(candidates, runs_root)
            return (filtered or candidates)[0]
        # If no batch_submission_id is provided, try to find attempts matching just the run_id.
        if not batch_submission_id:
            rows = conn.execute(
                "SELECT * FROM submission_attempts WHERE id = ?",
                (str(run_id or ""),),
            ).fetchall()
            if rows:
                candidates = [_row_to_dict(row) for row in rows]
                filtered = filter_attempts_for_root(candidates, runs_root)
                return (filtered or candidates)[0]
            return None
        return None
    finally:
        conn.close()

# List attempts with optional filters for assignment, student, and active status.
def list_attempts(
    *,
    assignment_id: str | None = None,
    student_id: str | None = None,
    active_only: bool = False,
    newest_first: bool = True,
) -> list[dict[str, Any]]:
    """List stored attempts with optional filters."""
    clauses: list[str] = []
    params: list[str | int] = []
    # Build the WHERE clause based on provided filters.
    if assignment_id:
        clauses.append("assignment_id = ?")
        params.append(str(assignment_id))
    # Filters for student_id if provided, otherwise includes all students.
    if student_id:
        clauses.append("student_id = ?")
        params.append(str(student_id))
    # Filter for active attempts if requested.
    if active_only:
        clauses.append("is_active = 1")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "DESC" if newest_first else "ASC"

    # Execute the query and return the list of attempts as dictionaries.
    conn = get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM submission_attempts
            {where_sql}
            ORDER BY assignment_id, student_id, attempt_number {direction}, created_at {direction}, id {direction}
            """,
            tuple(params),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()

# Build lookup maps for mark and batch attempts.
def attempt_maps(runs_root: Path | None = None) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Build lookup maps for mark and batch attempts."""
    attempts = filter_attempts_for_root(list_attempts(newest_first=True), runs_root)
    mark_map: dict[str, dict[str, Any]] = {}
    batch_map: dict[tuple[str, str], dict[str, Any]] = {}
    # Build maps for quick lookup of attempts by run_id and batch_submission_id.
    for attempt in attempts:
        run_id = str(attempt.get("run_id") or "")
        batch_submission_id = str(attempt.get("batch_submission_id") or "")
        if not run_id:
            continue
        if batch_submission_id:
            batch_map[(run_id, batch_submission_id)] = attempt
        else:
            mark_map[run_id] = attempt
    return mark_map, batch_map

# Validate the presence of required identifiers for an attempt and return them.
def _validate_attempt_identity(assignment_id: str, student_id: str) -> tuple[str, str]:
    """Validate the attempt identity."""
    assignment_value = str(assignment_id or "").strip()
    student_value = str(student_id or "").strip()
    if not assignment_value or not student_value:
        raise ValueError("assignment_id and student_id are required for attempts")
    return assignment_value, student_value

# Build the metadata payload for an attempt record.
def _build_attempt_metadata(
    *,
    assignment_id: str,
    student_id: str,
    source_type: str,
    source_actor_user_id: str = "",
    original_filename: str = "",
    source_ref: str = "",
    submitted_at: str | None = None,
    created_at: str | None = None,
    ingestion_status: str = "pending",
    pipeline_status: str = "pending",
    validity_status: str = "pending",
    run_id: str | None = None,
    run_dir: str | None = None,
    report_path: str = "",
    batch_run_id: str = "",
    batch_submission_id: str = "",
) -> dict[str, Any]:
    """Build the metadata payload for an attempt record."""
    assignment_value, student_value = _validate_attempt_identity(assignment_id, student_id)
    attempt_id = str(run_id or generate_attempt_id("attempt"))
    created_value = str(created_at or utc_now_iso())
    return {
        "id": attempt_id,
        "assignment_id": assignment_value,
        "student_id": student_value,
        "source_type": str(source_type or ""),
        "source_actor_user_id": str(source_actor_user_id or ""),
        "created_at": created_value,
        "submitted_at": str(submitted_at or created_value),
        "original_filename": str(original_filename or ""),
        "source_ref": str(source_ref or ""),
        "ingestion_status": str(ingestion_status or "pending"),
        "pipeline_status": str(pipeline_status or "pending"),
        "validity_status": str(validity_status or "pending"),
        "run_id": attempt_id,
        "run_dir": str(run_dir or ""),
        "report_path": str(report_path or ""),
        "batch_run_id": str(batch_run_id or ""),
        "batch_submission_id": str(batch_submission_id or ""),
        "overall_score": None,
        "confidence": "",
        "manual_review_required": 0,
        "error_message": "",
    }

# Ensure that the report payload contains a writable submission metadata mapping, creating it if necessary.
def _ensure_submission_metadata(report: dict[str, Any]) -> dict[str, Any]:
    """Return a writable submission metadata mapping within a report payload."""
    metadata_root = report.get("metadata")
    if not isinstance(metadata_root, dict):
        metadata_root = {}
        report["metadata"] = metadata_root

    # Ensure there is a submission_metadata dictionary within the report metadata.
    submission_metadata = metadata_root.get("submission_metadata")
    if not isinstance(submission_metadata, dict):
        submission_metadata = {}
        metadata_root["submission_metadata"] = submission_metadata
    return submission_metadata

# Normalise attempt-like metadata for persistence.
def _normalize_attempt_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize attempt-like metadata for persistence."""
    created_at = str(metadata.get("created_at") or utc_now_iso())
    return {
        "source_type": str(metadata.get("source_type") or ""),
        "source_actor_user_id": str(metadata.get("source_actor_user_id") or ""),
        "created_at": created_at,
        "submitted_at": str(metadata.get("submitted_at") or created_at),
        "original_filename": str(metadata.get("original_filename") or ""),
        "source_ref": str(metadata.get("source_ref") or ""),
        "ingestion_status": str(metadata.get("ingestion_status") or "pending"),
        "pipeline_status": str(metadata.get("pipeline_status") or "pending"),
        "validity_status": str(metadata.get("validity_status") or "pending"),
        "run_id": str(metadata.get("run_id") or ""),
        "run_dir": str(metadata.get("run_dir") or ""),
        "report_path": str(metadata.get("report_path") or ""),
        "batch_run_id": str(metadata.get("batch_run_id") or ""),
        "batch_submission_id": str(metadata.get("batch_submission_id") or ""),
        "overall_score": metadata.get("overall_score"),
        "confidence": str(metadata.get("confidence") or ""),
        "manual_review_required": 1 if metadata.get("manual_review_required") else 0,
        "error_message": str(metadata.get("error_message") or ""),
    }

# Extract the normalised attempt values for shared database fields from the metadata payload.
def _attempt_persistence_values(metadata: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return normalized attempt values for shared database fields."""
    normalized = _normalize_attempt_payload(metadata)
    return (
        normalized["source_type"],
        normalized["source_actor_user_id"],
        normalized["created_at"],
        normalized["submitted_at"],
        normalized["original_filename"],
        normalized["source_ref"],
        normalized["ingestion_status"],
        normalized["pipeline_status"],
        normalized["validity_status"],
        normalized["run_id"],
        normalized["run_dir"],
        normalized["report_path"],
        normalized["batch_run_id"],
        normalized["batch_submission_id"],
        normalized["overall_score"],
        normalized["confidence"],
        normalized["manual_review_required"],
        normalized["error_message"],
    )

# Function to find an existing attempt identifier for the metadata payload.
def _find_existing_attempt_id(conn: Any, metadata: Mapping[str, Any]) -> str | None:
    """Look up an existing attempt identifier for the metadata payload."""
    row = conn.execute(
        """
        SELECT id FROM submission_attempts
        WHERE run_id = ? AND batch_submission_id = ?
        """,
        (
            str(metadata.get("run_id") or ""),
            str(metadata.get("batch_submission_id") or ""),
        ),
    ).fetchone()
    if row is None:
        return None
    return str(row["id"] or "")

# Function to compute the next attempt number for a student assignment.
def _next_attempt_number(conn: Any, metadata: Mapping[str, Any]) -> int:
    """Compute the next attempt number for a student assignment."""
    return (
        int(
            conn.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0)
                FROM submission_attempts
                WHERE assignment_id = ? AND student_id = ?
                """,
                (
                    str(metadata.get("assignment_id") or ""),
                    str(metadata.get("student_id") or ""),
                ),
            ).fetchone()[0]
            or 0
        )
        + 1
    )

# Create and persist a new submission attempt.
def create_attempt(
    *,
    assignment_id: str,
    student_id: str,
    source_type: str,
    source_actor_user_id: str = "",
    original_filename: str = "",
    source_ref: str = "",
    submitted_at: str | None = None,
    created_at: str | None = None,
    ingestion_status: str = "pending",
    pipeline_status: str = "pending",
    validity_status: str = "pending",
    run_id: str | None = None,
    run_dir: str | None = None,
    report_path: str = "",
    batch_run_id: str = "",
    batch_submission_id: str = "",
) -> dict[str, Any]:
    """Create and persist a new submission attempt."""
    metadata = _build_attempt_metadata(
        assignment_id=assignment_id,
        student_id=student_id,
        source_type=source_type,
        source_actor_user_id=source_actor_user_id,
        original_filename=original_filename,
        source_ref=source_ref,
        submitted_at=submitted_at,
        created_at=created_at,
        ingestion_status=ingestion_status,
        pipeline_status=pipeline_status,
        validity_status=validity_status,
        run_id=run_id,
        run_dir=run_dir,
        report_path=report_path,
        batch_run_id=batch_run_id,
        batch_submission_id=batch_submission_id,
    )
    conn = get_db()
    try:
        existing_attempt_id = _find_existing_attempt_id(conn, metadata)
        if existing_attempt_id:
            return get_attempt(existing_attempt_id) or {}

        metadata["attempt_number"] = _next_attempt_number(conn, metadata)
        shared_values = _attempt_persistence_values(metadata)
        conn.execute(
            """
            INSERT INTO submission_attempts (
                id,
                assignment_id,
                student_id,
                attempt_number,
                source_type,
                source_actor_user_id,
                created_at,
                submitted_at,
                original_filename,
                source_ref,
                ingestion_status,
                pipeline_status,
                validity_status,
                run_id,
                run_dir,
                report_path,
                batch_run_id,
                batch_submission_id,
                overall_score,
                confidence,
                manual_review_required,
                error_message,
                is_active,
                selection_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?)
            """,
            (
                str(metadata.get("id") or ""),
                str(metadata.get("assignment_id") or ""),
                str(metadata.get("student_id") or ""),
                int(metadata.get("attempt_number") or 0),
                *shared_values,
                utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return get_attempt(str(metadata.get("id") or "")) or {}

# Build a filtered UPDATE statement with bool field conversion.
def _build_update_sql(
    table: str, id_col: str, allowed: frozenset[str], bool_fields: frozenset[str], **fields: Any
) -> tuple[str, list[Any]]:
    """Return (sql, params) for a filtered UPDATE, converting bool fields to int."""
    filtered = {k: v for k, v in fields.items() if k in allowed}
    if not filtered:
        return "", []
    set_parts = [f"{k} = ?" for k in filtered]
    params: list[Any] = [
        (1 if v else 0) if k in bool_fields else v
        for k, v in filtered.items()
    ]
    set_parts.append("updated_at = ?")
    params.append(utc_now_iso())
    return f"UPDATE {table} SET {', '.join(set_parts)} WHERE {id_col} = ?", params

# Update an existing attempt record.
def update_attempt(attempt_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update an existing attempt record."""
    if not fields:
        return get_attempt(attempt_id)

    _ALLOWED = frozenset({
        "source_type", "source_actor_user_id", "created_at", "submitted_at",
        "original_filename", "source_ref", "ingestion_status", "pipeline_status",
        "validity_status", "run_id", "run_dir", "report_path", "batch_run_id",
        "batch_submission_id", "overall_score", "confidence", "manual_review_required",
        "error_message", "is_active", "selection_reason",
    })
    _BOOL_FIELDS = frozenset({"manual_review_required", "is_active"})

    sql, params = _build_update_sql(
        "submission_attempts", "id", _ALLOWED, _BOOL_FIELDS, **fields
    )
    if not sql:
        return get_attempt(attempt_id)

    params.append(str(attempt_id))
    conn = get_db()
    try:
        conn.execute(sql, tuple(params))
        conn.commit()
    finally:
        conn.close()

    return get_attempt(attempt_id)

# Functions for loading and resolving assignment configurations


# Re-export sync/reconciliation API from dedicated module.
from ams.core.attempt_sync import (  # noqa: E402
    get_student_assignment_summary,
    recompute_active_attempt,
    sync_attempts_from_storage,
)

__all__ = [
    "attempt_maps",
    "create_attempt",
    "create_attempt_storage_dir",
    "generate_attempt_id",
    "get_attempt",
    "get_attempt_by_run_reference",
    "get_student_assignment_summary",
    "list_attempts",
    "recompute_active_attempt",
    "sync_attempts_from_storage",
    "update_attempt",
    "utc_now_iso",
]
