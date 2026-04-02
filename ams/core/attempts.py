from __future__ import annotations

import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ams.core.db import get_db
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
    submitted_value = str(submitted_at or created_value)
    batch_submission_value = str(batch_submission_id or "")

    # Build the metadata dictionary for the attempt.
    return {
        "id": attempt_id,
        "assignment_id": assignment_value,
        "student_id": student_value,
        "source_type": str(source_type or ""),
        "source_actor_user_id": str(source_actor_user_id or ""),
        "created_at": created_value,
        "submitted_at": submitted_value,
        "original_filename": str(original_filename or ""),
        "source_ref": str(source_ref or ""),
        "ingestion_status": str(ingestion_status or "pending"),
        "pipeline_status": str(pipeline_status or "pending"),
        "validity_status": str(validity_status or "pending"),
        "run_id": attempt_id,
        "run_dir": str(run_dir or ""),
        "report_path": str(report_path or ""),
        "batch_run_id": str(batch_run_id or ""),
        "batch_submission_id": batch_submission_value,
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

# Function to insert a new attempt record into the database.
def _insert_attempt_record(conn: Any, metadata: Mapping[str, Any]) -> None:
    """Insert a submission attempt record."""
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
    # Check for an existing attempt with the same run_id and batch_submission_id, and return it if found.
    conn = get_db()
    try:
        existing_attempt_id = _find_existing_attempt_id(conn, metadata)
        if existing_attempt_id:
            return get_attempt(existing_attempt_id) or {}

        metadata["attempt_number"] = _next_attempt_number(conn, metadata)
        _insert_attempt_record(conn, metadata)
        conn.commit()
    finally:
        conn.close()

    return get_attempt(str(metadata.get("id") or "")) or {}

# Update an existing attempt record.
def update_attempt(attempt_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update an existing attempt record."""
    if not fields:
        return get_attempt(attempt_id)

    allowed = {
        "source_type",
        "source_actor_user_id",
        "created_at",
        "submitted_at",
        "original_filename",
        "source_ref",
        "ingestion_status",
        "pipeline_status",
        "validity_status",
        "run_id",
        "run_dir",
        "report_path",
        "batch_run_id",
        "batch_submission_id",
        "overall_score",
        "confidence",
        "manual_review_required",
        "error_message",
        "is_active",
        "selection_reason",
    }

    # Build the SET clause and parameters for the UPDATE statement based on provided fields.
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        if key in {"manual_review_required", "is_active"}:
            params.append(1 if value else 0)
        else:
            params.append(value)

    if not assignments:
        return get_attempt(attempt_id)

    assignments.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(str(attempt_id))

    # Execute the UPDATE statement to modify the attempt record in the database.
    conn = get_db()
    try:
        conn.execute(
            f"""
            UPDATE submission_attempts
            SET {', '.join(assignments)}
            WHERE id = ?
            """,
            tuple(params),
        )
        conn.commit()
    finally:
        conn.close()

    return get_attempt(attempt_id)

# Functions for loading and resolving assignment configurations
def _load_json(path: Path) -> dict[str, Any] | None:
    """Load JSON data from disk."""
    return try_read_json(path, default=None)

# Functions for managing assignment configurations
def _extract_confidence(report: dict[str, Any]) -> str:
    """Extract the confidence level from a report."""
    summary = report.get("summary", {}) or {}
    if isinstance(summary, dict) and summary.get("confidence"):
        return str(summary.get("confidence") or "")
    score_evidence = report.get("score_evidence", {}) or {}
    if isinstance(score_evidence, dict):
        confidence = score_evidence.get("confidence", {}) or {}
        if isinstance(confidence, dict) and confidence.get("level"):
            return str(confidence.get("level") or "")
    return ""

# Function to determine whether manual review is required based on report evidence and context.
def _extract_manual_review_required(report: dict[str, Any], context: dict[str, Any] | None = None) -> bool:
    """Determine whether manual review is required."""
    score_evidence = report.get("score_evidence", {}) or {}
    if isinstance(score_evidence, dict):
        review = score_evidence.get("review", {}) or {}
        if isinstance(review, dict) and review.get("recommended") is not None:
            return bool(review.get("recommended"))
    if isinstance(context, dict):
        return bool(context.get("llm_error_flagged") or context.get("threat_flagged"))
    return False

# Function to check whether a report indicates a grading-system failure based on its metadata and findings.
def _report_has_system_assessment_failure(report: Mapping[str, Any] | None) -> bool:
    """Return True when the report shows a grading-system failure."""
    if not isinstance(report, Mapping):
        return False

    # Check the report metadata for any error flags or messages that indicate a system failure.
    metadata = report.get("metadata", {}) or {}
    if isinstance(metadata, Mapping):
        if metadata.get("llm_error_detected"):
            return True
        if metadata.get("llm_error_message"):
            return True
        if list(metadata.get("llm_error_messages") or []):
            return True
        if metadata.get("system_error_detected"):
            return True
        if metadata.get("system_error_message"):
            return True
        if list(metadata.get("system_error_messages") or []):
            return True
        if metadata.get("assessment_error_detected"):
            return True
        if metadata.get("assessment_error_message"):
            return True
        if list(metadata.get("assessment_error_messages") or []):
            return True

    # Check the report findings for any indications of system failures.
    for finding in list(report.get("findings", []) or []):
        finding_id = str(finding.get("id") or "").strip()
        evidence = finding.get("evidence", {}) or {}
        if not isinstance(evidence, Mapping):
            evidence = {}

        # Certain findings are explicitly designated as requiring review due to LLM errors.
        if finding_id == "LLM.ERROR.REQUIRES_REVIEW":
            return True

        # Check for evidence of LLM errors or system failures in various types of findings.
        llm_feedback = evidence.get("llm_feedback")
        if isinstance(llm_feedback, Mapping):
            meta = llm_feedback.get("meta", {}) or {}
            if isinstance(meta, Mapping) and (
                str(meta.get("reason") or "").strip().lower() == "llm_error"
                or str(meta.get("error") or "").strip()
                or bool(meta.get("fallback"))
                and str(meta.get("reason") or "").strip().lower() == "parse_error"
            ):
                return True

        # Check for evidence of LLM errors in hybrid scoring, UX reviews, and vision analysis.
        hybrid_score = evidence.get("hybrid_score")
        if isinstance(hybrid_score, Mapping):
            reasoning = str(hybrid_score.get("reasoning") or "").strip().lower()
            raw_response = hybrid_score.get("raw_response")
            if isinstance(raw_response, Mapping) and raw_response.get("error"):
                return True
            if "llm error" in reasoning or "llm parse error" in reasoning:
                return True

        # Check for evidence of LLM errors in UX reviews, which may indicate a need for manual review.
        ux_review = evidence.get("ux_review")
        if isinstance(ux_review, Mapping):
            status = str(ux_review.get("status") or "").strip().upper()
            feedback = str(ux_review.get("feedback") or "").strip().lower()
            if status == "NOT_EVALUATED" and (
                feedback.startswith("llm error:")
                or feedback == "could not parse model response."
            ):
                return True

        # Check for evidence of vision analysis failures.
        vision_analysis = evidence.get("vision_analysis")
        if isinstance(vision_analysis, Mapping):
            status = str(vision_analysis.get("status") or "").strip().upper()
            meta = vision_analysis.get("meta", {}) or {}
            if status == "NOT_EVALUATED" and isinstance(meta, Mapping):
                reason = str(meta.get("reason") or "").strip().lower()
                if reason in {"llm_error", "parse_error"} or str(meta.get("error") or "").strip():
                    return True

    return False

# Function to derive the run status, pipeline status and validity status for an attempt.
def _derive_statuses(
    *,
    run_info: dict[str, Any],
    report: dict[str, Any] | None,
    invalid: bool = False,
) -> tuple[str, str, str]:
    """Derive the run and validity statuses for an attempt."""
    status = str(run_info.get("status") or "").strip().lower()
    threat_flagged = bool(run_info.get("threat_flagged"))
    system_failure_flagged = (
        bool(run_info.get("llm_error_flagged"))
        or bool(run_info.get("system_error_flagged"))
        or status in {"llm_error", "system_error", "infra_error", "infrastructure_error"}
        or _report_has_system_assessment_failure(report)
    )
    # The attempt is pending if the run status indicates it is still processing, otherwise it is completed.
    if status in {"pending", "queued", "running"}:
        return "pending", "pending", "pending"
    if invalid:
        return "failed", "failed", "invalid"
    if threat_flagged:
        return "completed", "failed", "invalid"
    if system_failure_flagged:
        return "completed", "failed", "invalid"
    if report is not None:
        return "completed", "completed", "valid"
    if status in {"failed", "error"} or status.startswith("invalid"):
        return "failed", "failed", "invalid"
    return "completed", "failed", "invalid"

# Function to check whether an attempt is valid based on its validity status.
def _attempt_is_valid(attempt: dict[str, Any]) -> bool:
    """Check whether an attempt is valid."""
    return str(attempt.get("validity_status") or "").strip().lower() == "valid"

# Function to check whether an attempt is pending based on its validity status.
def _attempt_is_pending(attempt: dict[str, Any]) -> bool:
    """Check whether an attempt is pending."""
    return str(attempt.get("validity_status") or "").strip().lower() == "pending"

# Function to explain why a particular attempt was selected as active.
def _explain_attempt_selection(
    attempts_desc: list[dict[str, Any]],
    active_attempt: dict[str, Any] | None,
) -> str:
    """Explain why a particular attempt was selected."""
    if not attempts_desc:
        return "No submission attempt is currently recorded."

    # The most recent attempt is considered the latest.
    latest_attempt = attempts_desc[0]
    if active_attempt is None:
        if _attempt_is_pending(latest_attempt):
            return "A newer submission is still processing, and no valid result is available yet."
        return "No valid submission is currently available."

    # If the active attempt is the same as the latest attempt.
    if active_attempt["id"] == latest_attempt["id"]:
        return "Active because it is the most recent valid submission."

    # If the active attempt is different from the latest attempt, explain based on their statuses.
    if _attempt_is_pending(latest_attempt):
        return "A newer submission is still processing, so the latest valid submission remains active."

    return "Latest submission was invalid, so the previous valid submission remains active."

# Function to merge existing attempt metadata with new payload data.
def _merge_attempt_metadata(existing: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge stored and newly generated attempt metadata."""
    merged = dict(existing or {})
    merged.update(payload)
    return merged

# Function to select which attempt files should be synchronised.
def _select_files_for_sync(
    attempts_desc: list[dict[str, Any]],
    *,
    latest_attempt_id: str,
    active_attempt_id: str,
    selection_reason: str,
) -> list[dict[str, Any]]:
    """Select which attempt files should be synchronised."""
    file_updates: list[dict[str, Any]] = []
    selected_ids = {latest_attempt_id, active_attempt_id}

    # Iterate through the attempt descriptions and prepare the metadata.
    for attempt in attempts_desc:
        run_dir_value = str(attempt.get("run_dir") or "").strip()
        if not run_dir_value:
            continue
        run_dir = Path(run_dir_value)
        if not run_dir.exists():
            continue
        
        # Determine the selection reason for this attempt.
        attempt_selection_reason = selection_reason if attempt.get("id") in selected_ids else ""
        metadata_path = run_dir / "metadata.json"
        existing_metadata = _load_json(metadata_path) if metadata_path.exists() else None

        # Build the metadata payload for this attempt, merging it with any existing metadata.
        metadata_payload = _merge_attempt_metadata(
            existing_metadata,
            {
                "attempt_id": attempt.get("id"),
                "attempt_number": attempt.get("attempt_number"),
                "assignment_id": attempt.get("assignment_id"),
                "student_id": attempt.get("student_id"),
                "source_type": attempt.get("source_type"),
                "source_actor_user_id": attempt.get("source_actor_user_id"),
                "original_filename": attempt.get("original_filename"),
                "source_ref": attempt.get("source_ref"),
                "created_at": attempt.get("created_at"),
                "submitted_at": attempt.get("submitted_at"),
                "ingestion_status": attempt.get("ingestion_status"),
                "pipeline_status": attempt.get("pipeline_status"),
                "validity_status": attempt.get("validity_status"),
                "run_id": attempt.get("run_id"),
                "report_path": attempt.get("report_path"),
                "overall_score": attempt.get("overall_score"),
                "confidence": attempt.get("confidence"),
                "manual_review_required": bool(attempt.get("manual_review_required")),
                "is_active": bool(attempt.get("is_active")),
                "latest_attempt_id": latest_attempt_id or None,
                "active_attempt_id": active_attempt_id or None,
                "selection_reason": attempt_selection_reason,
            },
        )

        # If a run_info.json file exists, load it and merge its contents with the attempt metadata.
        run_info_path = run_dir / "run_info.json"
        run_info_payload: dict[str, Any] | None = None
        if run_info_path.exists():
            run_info_payload = _load_json(run_info_path) or {}
            run_info_payload.update(
                {
                    "attempt_id": attempt.get("id"),
                    "attempt_number": attempt.get("attempt_number"),
                    "source_type": attempt.get("source_type"),
                    "source_actor_user_id": attempt.get("source_actor_user_id"),
                    "submitted_at": attempt.get("submitted_at"),
                    "ingestion_status": attempt.get("ingestion_status"),
                    "pipeline_status": attempt.get("pipeline_status"),
                    "validity_status": attempt.get("validity_status"),
                    "overall_score": attempt.get("overall_score"),
                    "confidence": attempt.get("confidence"),
                    "manual_review_required": bool(attempt.get("manual_review_required")),
                    "is_active": bool(attempt.get("is_active")),
                    "latest_attempt_id": latest_attempt_id or "",
                    "active_attempt_id": active_attempt_id or "",
                    "selection_reason": attempt_selection_reason,
                }
            )

        # If a report.json file exists, load it and merge its contents with the attempt metadata,
        # also extracting confidence and manual review flags.
        report_payload: dict[str, Any] | None = None
        report_path_value = str(attempt.get("report_path") or "").strip()
        if report_path_value:
            report_path = Path(report_path_value)
            if report_path.exists():
                report = _load_json(report_path)
                if report is not None:
                    submission_metadata = _ensure_submission_metadata(report)
                    # Update the submission metadata in the report with attempt details and selection reason.
                    submission_metadata.update(
                        {
                            "attempt_id": attempt.get("id"),
                            "attempt_number": attempt.get("attempt_number"),
                            "source_type": attempt.get("source_type"),
                            "source_actor_user_id": attempt.get("source_actor_user_id"),
                            "submitted_at": attempt.get("submitted_at"),
                            "created_at": attempt.get("created_at"),
                            "validity_status": attempt.get("validity_status"),
                            "overall_score": attempt.get("overall_score"),
                            "confidence": attempt.get("confidence"),
                            "manual_review_required": bool(attempt.get("manual_review_required")),
                            "is_active": bool(attempt.get("is_active")),
                            "latest_attempt_id": latest_attempt_id or None,
                            "active_attempt_id": active_attempt_id or None,
                            "selection_reason": attempt_selection_reason,
                        }
                    )
                    report_payload = report
                else:
                    report_path = None
            else:
                report_path = None
        else:
            report_path = None

        # Updates metadata for this attempt.
        file_updates.append(
            {
                "metadata_path": metadata_path,
                "metadata_payload": metadata_payload,
                "report_path": report_path,
                "report_payload": report_payload,
                "run_info_path": run_info_path if run_info_path.exists() else None,
                "run_info_payload": run_info_payload,
            }
        )

    return file_updates

# Function to copy the selected attempt files into the attempt storage.
def _copy_attempt_files(file_updates: list[dict[str, Any]]) -> None:
    """Copy selected files into attempt storage."""
    for update in file_updates:
        metadata_path = update["metadata_path"]
        metadata_payload = update["metadata_payload"]
        write_json_file(metadata_path, metadata_payload, indent=2, sort_keys=True)
        
        # If there is run_info to update, write the merged run_info back to disk.
        run_info_path = update.get("run_info_path")
        run_info_payload = update.get("run_info_payload")
        if run_info_path is not None and run_info_payload is not None:
            write_json_file(run_info_path, run_info_payload, indent=2, sort_keys=True)

        # If there is report data to update, write the merged report back to disk.
        report_path = update.get("report_path")
        report_payload = update.get("report_payload")
        if report_path is not None and report_payload is not None:
            write_json_file(report_path, report_payload, indent=2)

# Function to synchronise attempt file metadata.
def _sync_attempt_files(
    attempts_desc: list[dict[str, Any]],
    *,
    latest_attempt_id: str,
    active_attempt_id: str,
    selection_reason: str,
) -> None:
    """Synchronise attempt file metadata and copies on disk."""
    file_updates = _select_files_for_sync(
        attempts_desc,
        latest_attempt_id=latest_attempt_id,
        active_attempt_id=active_attempt_id,
        selection_reason=selection_reason,
    )
    _copy_attempt_files(file_updates)

# Function to write the active attempt summary files for a student.
def _write_student_summary_files(
    runs_root: Path,
    assignment_id: str,
    student_id: str,
    attempts_desc: list[dict[str, Any]],
    latest_attempt_id: str,
    active_attempt_id: str,
    selection_reason: str,
) -> None:
    """Write the active-attempt summary files for a student."""
    summary_root = runs_root / MetadataValidator.sanitize_identifier(assignment_id) / MetadataValidator.sanitize_identifier(student_id)
    summary_root.mkdir(parents=True, exist_ok=True)

    # Write the active attempt summary file.
    active_payload = {
        "assignment_id": assignment_id,
        "student_id": student_id,
        "latest_attempt_id": latest_attempt_id or None,
        "active_attempt_id": active_attempt_id or None,
        "selection_reason": selection_reason,
        "updated_at": utc_now_iso(),
    }
    write_json_file(summary_root / "active.json", active_payload, indent=2)

    # Write the attempts summary file, including metadata for all attempts.
    attempts_payload = [
        {
            "attempt_id": attempt.get("id"),
            "attempt_number": attempt.get("attempt_number"),
            "submitted_at": attempt.get("submitted_at"),
            "created_at": attempt.get("created_at"),
            "source_type": attempt.get("source_type"),
            "source_actor_user_id": attempt.get("source_actor_user_id"),
            "original_filename": attempt.get("original_filename"),
            "ingestion_status": attempt.get("ingestion_status"),
            "pipeline_status": attempt.get("pipeline_status"),
            "validity_status": attempt.get("validity_status"),
            "overall_score": attempt.get("overall_score"),
            "confidence": attempt.get("confidence"),
            "manual_review_required": bool(attempt.get("manual_review_required")),
            "is_active": bool(attempt.get("is_active")),
            "run_id": attempt.get("run_id"),
            "report_path": attempt.get("report_path"),
            "batch_run_id": attempt.get("batch_run_id"),
            "batch_submission_id": attempt.get("batch_submission_id"),
        }
        for attempt in attempts_desc
    ]
    # Write the attempts summary file with metadata for all attempts.
    write_json_file(
        summary_root / "student_summary.json",
        {
            "assignment_id": assignment_id,
            "student_id": student_id,
            "latest_attempt_id": latest_attempt_id or None,
            "active_attempt_id": active_attempt_id or None,
            "selection_reason": selection_reason,
            "updated_at": utc_now_iso(),
            "attempts": attempts_payload,
        },
        indent=2,
    )

# Main function to recompute and persist the active attempt for a student assignment.
def recompute_active_attempt(
    runs_root: Path,
    assignment_id: str,
    student_id: str,
) -> dict[str, Any]:
    """Recompute and persist the active attempt for a student assignment."""
    attempts_desc = filter_attempts_for_root(
        list_attempts(
            assignment_id=str(assignment_id or ""),
            student_id=str(student_id or ""),
            newest_first=True,
        ),
        runs_root,
    )
    # The latest attempt is the most recent attempt, while the active attempt is the most recent valid attempt. 
    latest_attempt = attempts_desc[0] if attempts_desc else None
    active_attempt = next((attempt for attempt in attempts_desc if _attempt_is_valid(attempt)), None)
    latest_attempt_id = str((latest_attempt or {}).get("id") or "")
    active_attempt_id = str((active_attempt or {}).get("id") or "")
    selection_reason = _explain_attempt_selection(attempts_desc, active_attempt)

    # Update the database records for all attempts to reflect the new active attempt selection.
    conn = get_db()
    try:
        for attempt in attempts_desc:
            conn.execute(
                """
                UPDATE submission_attempts
                SET is_active = ?,
                    selection_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if str(attempt.get("id") or "") == active_attempt_id else 0,
                    selection_reason if str(attempt.get("id") or "") == active_attempt_id else "",
                    utc_now_iso(),
                    str(attempt.get("id") or ""),
                ),
            )
        
        # Upsert the student assignment summary record to reflect the new active attempt selection.
        conn.execute(
            """
            INSERT INTO student_assignment_summary (
                assignment_id,
                student_id,
                latest_attempt_id,
                active_attempt_id,
                selection_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(assignment_id, student_id) DO UPDATE SET
                latest_attempt_id = excluded.latest_attempt_id,
                active_attempt_id = excluded.active_attempt_id,
                selection_reason = excluded.selection_reason,
                updated_at = excluded.updated_at
            """,
            (
                str(assignment_id or ""),
                str(student_id or ""),
                latest_attempt_id,
                active_attempt_id,
                selection_reason,
                utc_now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # After updating the database, refresh the attempt descriptors and synchronise the attempt files on disk.
    refreshed_attempts = filter_attempts_for_root(
        list_attempts(
            assignment_id=str(assignment_id or ""),
            student_id=str(student_id or ""),
            newest_first=True,
        ),
        runs_root,
    )
    # Synchronise the attempt files on disk.
    _sync_attempt_files(
        refreshed_attempts,
        latest_attempt_id=latest_attempt_id,
        active_attempt_id=active_attempt_id,
        selection_reason=selection_reason,
    )
    # Writes the active attempt summary file and the attempts summary file.
    _write_student_summary_files(
        runs_root,
        str(assignment_id or ""),
        str(student_id or ""),
        refreshed_attempts,
        latest_attempt_id,
        active_attempt_id,
        selection_reason,
    )
    return {
        "latest_attempt_id": latest_attempt_id or None,
        "active_attempt_id": active_attempt_id or None,
        "selection_reason": selection_reason,
    }

# Function to fetch the attempt summary for a student assignment.
def get_student_assignment_summary(assignment_id: str, student_id: str) -> dict[str, Any] | None:
    """Fetch the attempt summary for a student assignment."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT * FROM student_assignment_summary
            WHERE assignment_id = ? AND student_id = ?
            """,
            (str(assignment_id or ""), str(student_id or "")),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

# Function to build a storage descriptor for an attempt based on run information and report data.
def _mark_descriptor(
    run_dir: Path,
    run_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a descriptor for matching mark attempts."""
    assignment_id = str(run_info.get("assignment_id") or "").strip()
    student_id = str(run_info.get("student_id") or "").strip()
    if not assignment_id or not student_id:
        return None

    # run_id is derived from run_info or the run directory name.
    run_id = str(run_info.get("id") or run_dir.name)
    report_path = run_dir / str(run_info.get("report") or "report.json")
    report = _load_json(report_path) if report_path.exists() else None
    ingestion_status, pipeline_status, validity_status = _derive_statuses(
        run_info=run_info,
        report=report,
        invalid=False,
    )
    # Build and return the attempt descriptor with relevant metadata extracted from run_info and report.
    return {
        "id": str(run_info.get("attempt_id") or run_id),
        "run_id": run_id,
        "batch_submission_id": "",
        "assignment_id": assignment_id,
        "student_id": student_id,
        "attempt_number": run_info.get("attempt_number"),
        "source_type": str(run_info.get("source_type") or run_info.get("source") or "upload"),
        "source_actor_user_id": str(run_info.get("source_actor_user_id") or ""),
        "created_at": str(run_info.get("created_at") or ""),
        "submitted_at": str(run_info.get("submitted_at") or run_info.get("created_at") or ""),
        "original_filename": str(run_info.get("original_filename") or ""),
        "source_ref": str(run_info.get("source_ref") or run_info.get("github_repo") or ""),
        "ingestion_status": ingestion_status,
        "pipeline_status": pipeline_status,
        "validity_status": validity_status,
        "run_dir": str(run_dir),
        "report_path": str(report_path) if report_path.exists() else "",
        "batch_run_id": "",
        "overall_score": (report or {}).get("scores", {}).get("overall"),
        "confidence": _extract_confidence(report or {}) if report else "",
        "manual_review_required": _extract_manual_review_required(report or {}, run_info) if report else bool(run_info.get("llm_error_flagged") or run_info.get("threat_flagged")),
        "error_message": str(run_info.get("error") or ""),
    }

# Function to generate a stable reference for an attempt descriptor.
def _descriptor_ref(descriptor: Mapping[str, Any]) -> tuple[str, str]:
    """Return stable reference for an attempt descriptor."""
    return (
        str(descriptor.get("run_id") or ""),
        str(descriptor.get("batch_submission_id") or ""),
    )

# Function to generate a sort key for ordering attempt descriptors.
def _descriptor_sort_key(descriptor: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return sort key for ordering attempt descriptors."""
    return (
        str(descriptor.get("created_at") or ""),
        str(descriptor.get("run_id") or ""),
        str(descriptor.get("batch_submission_id") or ""),
    )

# Function to extract the persistence values from an attempt descriptor for database operations.
def _update_attempt_from_descriptor(
    conn: Any,
    attempt_id: str,
    descriptor: Mapping[str, Any],
) -> None:
    """Update an attempt row from a storage descriptor."""
    shared_values = _attempt_persistence_values(descriptor)
    conn.execute(
        """
        UPDATE submission_attempts
        SET source_type = ?,
            source_actor_user_id = ?,
            created_at = ?,
            submitted_at = ?,
            original_filename = ?,
            source_ref = ?,
            ingestion_status = ?,
            pipeline_status = ?,
            validity_status = ?,
            run_id = ?,
            run_dir = ?,
            report_path = ?,
            batch_run_id = ?,
            batch_submission_id = ?,
            overall_score = ?,
            confidence = ?,
            manual_review_required = ?,
            error_message = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            *shared_values,
            utc_now_iso(),
            attempt_id,
        ),
    )

# Function to build storage descriptors for attempts found in a batch run.
def _build_batch_attempt_descriptors(
    run_dir: Path,
    run_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build storage descriptors for attempts found in a batch run."""
    summary_path = run_dir / "batch_summary.json"
    summary = _load_json(summary_path)
    if summary is None:
        return []

    # Build and return a list of attempt descriptors based on the batch summary.
    descriptors: list[dict[str, Any]] = []
    run_id = str(run_info.get("id") or run_dir.name)
    created_at = str(run_info.get("created_at") or "")

    # Iterate through the records in the batch summary and build an attempt descriptor for each valid record.
    for record in list(summary.get("records", []) or []):
        if record.get("materialized_run_id"):
            continue
        # Derive the assignment_id, student_id and submission_id for this record.
        assignment_id = str(record.get("assignment_id") or run_info.get("assignment_id") or "").strip()
        student_id = str(record.get("student_id") or "").strip()
        submission_id = str(record.get("id") or "").strip()
        if not assignment_id or not student_id or not submission_id:
            continue
        
        # Derive the report path for this record.
        report_path_value = str(record.get("report_path") or "").strip()
        report_path = Path(report_path_value) if report_path_value else (run_dir / "runs" / submission_id / "report.json")
        report = _load_json(report_path) if report_path.exists() else None
        ingestion_status, pipeline_status, validity_status = _derive_statuses(
            run_info={
                "status": record.get("status"),
                "llm_error_flagged": record.get("llm_error_flagged"),
                "threat_flagged": record.get("threat_flagged"),
            },
            report=report,
            invalid=bool(record.get("invalid")),
        )

        # Build the attempt descriptor for this record and add it to the list of descriptors.
        descriptors.append(
            {
                "id": f"{run_id}__{submission_id}",
                "run_id": run_id,
                "batch_submission_id": submission_id,
                "assignment_id": assignment_id,
                "student_id": student_id,
                "attempt_number": record.get("attempt_number"),
                "source_type": str(record.get("source_type") or run_info.get("source") or "batch_upload"),
                "source_actor_user_id": str(record.get("source_actor_user_id") or run_info.get("source_actor_user_id") or ""),
                "created_at": str(record.get("upload_timestamp") or created_at),
                "submitted_at": str(record.get("submitted_at") or record.get("upload_timestamp") or created_at),
                "original_filename": str(record.get("original_filename") or ""),
                "source_ref": str(record.get("source_ref") or ""),
                "ingestion_status": ingestion_status,
                "pipeline_status": pipeline_status,
                "validity_status": validity_status,
                "run_dir": str((run_dir / "runs" / submission_id) if (run_dir / "runs" / submission_id).exists() else run_dir),
                "report_path": str(report_path) if report_path.exists() else "",
                "batch_run_id": run_id,
                "overall_score": record.get("overall"),
                "confidence": _extract_confidence(report or {}) if report else "",
                "manual_review_required": _extract_manual_review_required(report or {}, record) if report else bool(record.get("llm_error_flagged") or record.get("threat_flagged")),
                "error_message": str(record.get("error") or record.get("validation_error") or ""),
            }
        )
    return descriptors

# Function to scan the attempt storage and build descriptors for discovered runs.
def _scan_storage_for_descriptors(runs_root: Path) -> list[dict[str, Any]]:
    """Scan attempt storage and build descriptors for discovered runs."""
    if not runs_root.exists():
        return []
    
    # Recursively scan the runs root directory for run_info.json.
    descriptors: list[dict[str, Any]] = []
    for run_info_path in runs_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        run_info = _load_json(run_info_path)
        if run_info is None:
            continue
        
        # Determine the mode of this run (mark or batch) and build the appropriate attempt descriptors.
        mode = str(run_info.get("mode") or "")
        if mode == "mark":
            descriptor = _mark_descriptor(run_dir, run_info)
            if descriptor is not None:
                descriptors.append(descriptor)
            continue
        if mode == "batch":
            descriptors.extend(_build_batch_attempt_descriptors(run_dir, run_info))
    return descriptors

# Function to reconcile the database attempts with the descriptors found.
def _reconcile_attempts_with_descriptors(
    conn: Any,
    descriptors: list[dict[str, Any]],
    runs_root: Path,
) -> set[tuple[str, str]]:
    """Reconcile database attempts against descriptors found on disk."""
    touched: set[tuple[str, str]] = set()
    descriptors_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    # Group the descriptors by their assignment_id and student_id.
    for descriptor in descriptors:
        identity = (
            str(descriptor.get("assignment_id") or ""),
            str(descriptor.get("student_id") or ""),
        )
        if identity[0] and identity[1]:
            descriptors_by_identity[identity].append(descriptor)

    # Iterate through the grouped descriptors and reconcile them with the existing database records for each student assignment.
    for identity, identity_descriptors in descriptors_by_identity.items():
        assignment_id, student_id = identity
        existing_rows = conn.execute(
            """
            SELECT * FROM submission_attempts
            WHERE assignment_id = ? AND student_id = ?
            ORDER BY attempt_number ASC, created_at ASC, id ASC
            """,
            (assignment_id, student_id),
        ).fetchall()
        # Convert the existing database rows to dictionaries and filter them.
        all_db_rows = [_row_to_dict(row) for row in existing_rows]
        existing_identity = filter_attempts_for_root(all_db_rows, runs_root)
        existing_refs = {
            (str(row.get("run_id") or ""), str(row.get("batch_submission_id") or "")): row
            for row in existing_identity
        }
        # Determine the next attempt number to use for new attempts.
        used_attempt_numbers = {
            int(row.get("attempt_number") or 0)
            for row in all_db_rows
            if int(row.get("attempt_number") or 0) > 0
        }
        next_attempt_number = max(used_attempt_numbers, default=0) + 1
        # Build a mapping of the descriptors by their run_id or batch_submission_id.
        descriptors_by_ref: dict[tuple[str, str], dict[str, Any]] = {}
        for descriptor in sorted(identity_descriptors, key=_descriptor_sort_key):
            descriptors_by_ref[_descriptor_ref(descriptor)] = descriptor

        # Iterate through the descriptors for this student assignment.
        for ref, descriptor in descriptors_by_ref.items():
            existing_attempt = existing_refs.get(ref)
            if existing_attempt is not None:
                _update_attempt_from_descriptor(conn, str(existing_attempt.get("id") or ""), descriptor)
                touched.add(identity)
                continue
            
            # Create a new attempt record for this descriptor.
            proposed_attempt_id = str(descriptor.get("id") or generate_attempt_id("attempt"))
            candidate_attempt_id = proposed_attempt_id
            existing_id_row = conn.execute(
                "SELECT * FROM submission_attempts WHERE id = ?",
                (candidate_attempt_id,),
            ).fetchone()
            # If attempted, check if ID already exists and if it belongs to the same run reference and student assignment, 
            # otherwise generate a new ID.
            if existing_id_row is not None:
                existing_id_attempt = _row_to_dict(existing_id_row)
                if (
                    _descriptor_ref(existing_id_attempt) != ref
                    or not _attempt_belongs_to_root(existing_id_attempt, runs_root)
                ):
                    candidate_attempt_id = generate_attempt_id("attempt")

            # Determine the attempt number to use for this new attempt.
            requested_attempt_number = descriptor.get("attempt_number")
            if (
                isinstance(requested_attempt_number, int)
                and requested_attempt_number > 0
                and requested_attempt_number not in used_attempt_numbers
            ):
                chosen_attempt_number = requested_attempt_number
            else:
                while next_attempt_number in used_attempt_numbers:
                    next_attempt_number += 1
                chosen_attempt_number = next_attempt_number
                next_attempt_number += 1
            descriptor_with_identity = {
                **descriptor,
                "id": candidate_attempt_id,
                "assignment_id": assignment_id,
                "student_id": student_id,
                "attempt_number": chosen_attempt_number,
            }
            shared_values = _attempt_persistence_values(descriptor_with_identity)

            # Insert the new attempt record into the database.
            conn.execute(
                """
                INSERT OR IGNORE INTO submission_attempts (
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
                    candidate_attempt_id,
                    assignment_id,
                    student_id,
                    chosen_attempt_number,
                    *shared_values,
                    utc_now_iso(),
                ),
            )
            # Update the existing_refs mapping and used_attempt_numbers set with the new attempt.
            existing_refs[ref] = {
                "id": candidate_attempt_id,
                "attempt_number": chosen_attempt_number,
            }
            used_attempt_numbers.add(chosen_attempt_number)
            touched.add(identity)

    conn.commit()
    return touched

# Function to synchronise the attempts from storage and reconcile them with the database.
def sync_attempts_from_storage(runs_root: Path) -> None:
    """Synchronise the attempts from storage."""
    descriptors = _scan_storage_for_descriptors(runs_root)
    if not descriptors:
        return
    with get_db() as conn:
        touched = _reconcile_attempts_with_descriptors(conn, descriptors, runs_root)
    summaries = {
        (str(descriptor.get("assignment_id") or ""), str(descriptor.get("student_id") or ""))
        for descriptor in descriptors
        if descriptor.get("assignment_id") and descriptor.get("student_id")
    }
    for assignment_id, student_id in summaries.union(touched):
        recompute_active_attempt(runs_root, assignment_id, student_id)
    return


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
