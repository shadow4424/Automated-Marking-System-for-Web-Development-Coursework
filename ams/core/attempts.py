from __future__ import annotations

import json
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ams.core.db import get_db
from ams.io.metadata import MetadataValidator


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_attempt_id(prefix: str = "attempt") -> str:
    """Generate a new attempt identifier."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}_{prefix}_{secrets.token_hex(4)}"


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a database row into an attempt dictionary."""
    if row is None:
        return {}
    data = dict(row)
    data["attempt_number"] = int(data.get("attempt_number") or 0)
    data["manual_review_required"] = bool(data.get("manual_review_required"))
    data["is_active"] = bool(data.get("is_active"))
    return data


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


def filter_attempts_for_root(attempts: list[dict[str, Any]], runs_root: Path | None) -> list[dict[str, Any]]:
    """Filter attempts to those under the current runs root."""
    return [attempt for attempt in attempts if _attempt_belongs_to_root(attempt, runs_root)]


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


def get_attempt_by_run_reference(
    run_id: str,
    batch_submission_id: str | None = None,
    runs_root: Path | None = None,
) -> dict[str, Any] | None:
    """Fetch an attempt by its run and submission reference."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM submission_attempts
            WHERE run_id = ? AND batch_submission_id = ?
            """,
            (str(run_id or ""), str(batch_submission_id or "")),
        ).fetchall()
        if rows:
            candidates = [_row_to_dict(row) for row in rows]
            filtered = filter_attempts_for_root(candidates, runs_root)
            return (filtered or candidates)[0]
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
    if assignment_id:
        clauses.append("assignment_id = ?")
        params.append(str(assignment_id))
    if student_id:
        clauses.append("student_id = ?")
        params.append(str(student_id))
    if active_only:
        clauses.append("is_active = 1")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "DESC" if newest_first else "ASC"

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


def attempt_maps(runs_root: Path | None = None) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Build lookup maps for mark and batch attempts."""
    attempts = filter_attempts_for_root(list_attempts(newest_first=True), runs_root)
    mark_map: dict[str, dict[str, Any]] = {}
    batch_map: dict[tuple[str, str], dict[str, Any]] = {}
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


def _validate_attempt_identity(assignment_id: str, student_id: str) -> tuple[str, str]:
    """Validate the attempt identity."""
    assignment_value = str(assignment_id or "").strip()
    student_value = str(student_id or "").strip()
    if not assignment_value or not student_value:
        raise ValueError("assignment_id and student_id are required for attempts")
    return assignment_value, student_value


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


def _insert_attempt_record(conn: Any, metadata: Mapping[str, Any]) -> None:
    """Insert a submission attempt record."""
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
            str(metadata.get("source_type") or ""),
            str(metadata.get("source_actor_user_id") or ""),
            str(metadata.get("created_at") or utc_now_iso()),
            str(metadata.get("submitted_at") or metadata.get("created_at") or utc_now_iso()),
            str(metadata.get("original_filename") or ""),
            str(metadata.get("source_ref") or ""),
            str(metadata.get("ingestion_status") or "pending"),
            str(metadata.get("pipeline_status") or "pending"),
            str(metadata.get("validity_status") or "pending"),
            str(metadata.get("run_id") or ""),
            str(metadata.get("run_dir") or ""),
            str(metadata.get("report_path") or ""),
            str(metadata.get("batch_run_id") or ""),
            str(metadata.get("batch_submission_id") or ""),
            metadata.get("overall_score"),
            str(metadata.get("confidence") or ""),
            1 if metadata.get("manual_review_required") else 0,
            str(metadata.get("error_message") or ""),
            utc_now_iso(),
        ),
    )


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
        _insert_attempt_record(conn, metadata)
        conn.commit()
    finally:
        conn.close()

    return get_attempt(str(metadata.get("id") or "")) or {}


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


def _load_json(path: Path) -> dict[str, Any] | None:
    """Load JSON data from disk."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


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


def _report_has_system_assessment_failure(report: Mapping[str, Any] | None) -> bool:
    """Return True when the report shows a grading-system failure."""
    if not isinstance(report, Mapping):
        return False

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

    for finding in list(report.get("findings", []) or []):
        finding_id = str(finding.get("id") or "").strip()
        evidence = finding.get("evidence", {}) or {}
        if not isinstance(evidence, Mapping):
            evidence = {}

        if finding_id == "LLM.ERROR.REQUIRES_REVIEW":
            return True

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

        hybrid_score = evidence.get("hybrid_score")
        if isinstance(hybrid_score, Mapping):
            reasoning = str(hybrid_score.get("reasoning") or "").strip().lower()
            raw_response = hybrid_score.get("raw_response")
            if isinstance(raw_response, Mapping) and raw_response.get("error"):
                return True
            if "llm error" in reasoning or "llm parse error" in reasoning:
                return True

        ux_review = evidence.get("ux_review")
        if isinstance(ux_review, Mapping):
            status = str(ux_review.get("status") or "").strip().upper()
            feedback = str(ux_review.get("feedback") or "").strip().lower()
            if status == "NOT_EVALUATED" and (
                feedback.startswith("llm error:")
                or feedback == "could not parse model response."
            ):
                return True

        vision_analysis = evidence.get("vision_analysis")
        if isinstance(vision_analysis, Mapping):
            status = str(vision_analysis.get("status") or "").strip().upper()
            meta = vision_analysis.get("meta", {}) or {}
            if status == "NOT_EVALUATED" and isinstance(meta, Mapping):
                reason = str(meta.get("reason") or "").strip().lower()
                if reason in {"llm_error", "parse_error"} or str(meta.get("error") or "").strip():
                    return True

    return False


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


def _attempt_is_valid(attempt: dict[str, Any]) -> bool:
    """Check whether an attempt is valid."""
    return str(attempt.get("validity_status") or "").strip().lower() == "valid"


def _attempt_is_pending(attempt: dict[str, Any]) -> bool:
    """Check whether an attempt is pending."""
    return str(attempt.get("validity_status") or "").strip().lower() == "pending"


def _explain_attempt_selection(
    attempts_desc: list[dict[str, Any]],
    active_attempt: dict[str, Any] | None,
) -> str:
    """Explain why a particular attempt was selected."""
    if not attempts_desc:
        return "No submission attempt is currently recorded."

    latest_attempt = attempts_desc[0]
    if active_attempt is None:
        if _attempt_is_pending(latest_attempt):
            return "A newer submission is still processing, and no valid result is available yet."
        return "No valid submission is currently available."

    if active_attempt["id"] == latest_attempt["id"]:
        return "Active because it is the most recent valid submission."

    if _attempt_is_pending(latest_attempt):
        return "A newer submission is still processing, so the latest valid submission remains active."

    return "Latest submission was invalid, so the previous valid submission remains active."


def _merge_attempt_metadata(existing: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge stored and newly generated attempt metadata."""
    merged = dict(existing or {})
    merged.update(payload)
    return merged


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

    for attempt in attempts_desc:
        run_dir_value = str(attempt.get("run_dir") or "").strip()
        if not run_dir_value:
            continue
        run_dir = Path(run_dir_value)
        if not run_dir.exists():
            continue

        attempt_selection_reason = selection_reason if attempt.get("id") in selected_ids else ""
        metadata_path = run_dir / "metadata.json"
        existing_metadata = _load_json(metadata_path) if metadata_path.exists() else None
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

        report_payload: dict[str, Any] | None = None
        report_path_value = str(attempt.get("report_path") or "").strip()
        if report_path_value:
            report_path = Path(report_path_value)
            if report_path.exists():
                report = _load_json(report_path)
                if report is not None:
                    metadata_root = report.setdefault("metadata", {})
                    if not isinstance(metadata_root, dict):
                        metadata_root = {}
                        report["metadata"] = metadata_root
                    submission_metadata = metadata_root.setdefault("submission_metadata", {})
                    if not isinstance(submission_metadata, dict):
                        submission_metadata = {}
                        metadata_root["submission_metadata"] = submission_metadata
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


def _copy_attempt_files(file_updates: list[dict[str, Any]]) -> None:
    """Copy selected files into attempt storage."""
    for update in file_updates:
        metadata_path = update["metadata_path"]
        metadata_payload = update["metadata_payload"]
        metadata_path.write_text(json.dumps(metadata_payload, indent=2, sort_keys=True), encoding="utf-8")

        run_info_path = update.get("run_info_path")
        run_info_payload = update.get("run_info_payload")
        if run_info_path is not None and run_info_payload is not None:
            run_info_path.write_text(json.dumps(run_info_payload, indent=2, sort_keys=True), encoding="utf-8")

        report_path = update.get("report_path")
        report_payload = update.get("report_payload")
        if report_path is not None and report_payload is not None:
            report_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")


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

    active_payload = {
        "assignment_id": assignment_id,
        "student_id": student_id,
        "latest_attempt_id": latest_attempt_id or None,
        "active_attempt_id": active_attempt_id or None,
        "selection_reason": selection_reason,
        "updated_at": utc_now_iso(),
    }
    (summary_root / "active.json").write_text(json.dumps(active_payload, indent=2), encoding="utf-8")

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
    (summary_root / "student_summary.json").write_text(
        json.dumps(
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
        ),
        encoding="utf-8",
    )


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
    latest_attempt = attempts_desc[0] if attempts_desc else None
    active_attempt = next((attempt for attempt in attempts_desc if _attempt_is_valid(attempt)), None)
    latest_attempt_id = str((latest_attempt or {}).get("id") or "")
    active_attempt_id = str((active_attempt or {}).get("id") or "")
    selection_reason = _explain_attempt_selection(attempts_desc, active_attempt)

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

    refreshed_attempts = filter_attempts_for_root(
        list_attempts(
            assignment_id=str(assignment_id or ""),
            student_id=str(student_id or ""),
            newest_first=True,
        ),
        runs_root,
    )
    _sync_attempt_files(
        refreshed_attempts,
        latest_attempt_id=latest_attempt_id,
        active_attempt_id=active_attempt_id,
        selection_reason=selection_reason,
    )
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


def _mark_descriptor(
    run_dir: Path,
    run_info: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a descriptor for matching mark attempts."""
    assignment_id = str(run_info.get("assignment_id") or "").strip()
    student_id = str(run_info.get("student_id") or "").strip()
    if not assignment_id or not student_id:
        return None

    run_id = str(run_info.get("id") or run_dir.name)
    report_path = run_dir / str(run_info.get("report") or "report.json")
    report = _load_json(report_path) if report_path.exists() else None
    ingestion_status, pipeline_status, validity_status = _derive_statuses(
        run_info=run_info,
        report=report,
        invalid=False,
    )
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


def _descriptor_ref(descriptor: Mapping[str, Any]) -> tuple[str, str]:
    """Return stable reference for an attempt descriptor."""
    return (
        str(descriptor.get("run_id") or ""),
        str(descriptor.get("batch_submission_id") or ""),
    )


def _descriptor_sort_key(descriptor: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return sort key for ordering attempt descriptors."""
    return (
        str(descriptor.get("created_at") or ""),
        str(descriptor.get("run_id") or ""),
        str(descriptor.get("batch_submission_id") or ""),
    )


def _update_attempt_from_descriptor(
    conn: Any,
    attempt_id: str,
    descriptor: Mapping[str, Any],
) -> None:
    """Update an attempt row from a storage descriptor."""
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
            descriptor.get("source_type") or "",
            descriptor.get("source_actor_user_id") or "",
            descriptor.get("created_at") or utc_now_iso(),
            descriptor.get("submitted_at") or descriptor.get("created_at") or utc_now_iso(),
            descriptor.get("original_filename") or "",
            descriptor.get("source_ref") or "",
            descriptor.get("ingestion_status") or "pending",
            descriptor.get("pipeline_status") or "pending",
            descriptor.get("validity_status") or "pending",
            descriptor.get("run_id") or "",
            descriptor.get("run_dir") or "",
            descriptor.get("report_path") or "",
            descriptor.get("batch_run_id") or "",
            descriptor.get("batch_submission_id") or "",
            descriptor.get("overall_score"),
            descriptor.get("confidence") or "",
            1 if descriptor.get("manual_review_required") else 0,
            descriptor.get("error_message") or "",
            utc_now_iso(),
            attempt_id,
        ),
    )


def _build_batch_attempt_descriptors(
    run_dir: Path,
    run_info: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build storage descriptors for attempts found in a batch run."""
    summary_path = run_dir / "batch_summary.json"
    summary = _load_json(summary_path)
    if summary is None:
        return []

    descriptors: list[dict[str, Any]] = []
    run_id = str(run_info.get("id") or run_dir.name)
    created_at = str(run_info.get("created_at") or "")

    for record in list(summary.get("records", []) or []):
        if record.get("materialized_run_id"):
            continue
        assignment_id = str(record.get("assignment_id") or run_info.get("assignment_id") or "").strip()
        student_id = str(record.get("student_id") or "").strip()
        submission_id = str(record.get("id") or "").strip()
        if not assignment_id or not student_id or not submission_id:
            continue

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


def _scan_storage_for_descriptors(runs_root: Path) -> list[dict[str, Any]]:
    """Scan attempt storage and build descriptors for discovered runs."""
    if not runs_root.exists():
        return []

    descriptors: list[dict[str, Any]] = []
    for run_info_path in runs_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        run_info = _load_json(run_info_path)
        if run_info is None:
            continue

        mode = str(run_info.get("mode") or "")
        if mode == "mark":
            descriptor = _mark_descriptor(run_dir, run_info)
            if descriptor is not None:
                descriptors.append(descriptor)
            continue
        if mode == "batch":
            descriptors.extend(_build_batch_attempt_descriptors(run_dir, run_info))
    return descriptors


def _reconcile_attempts_with_descriptors(
    conn: Any,
    descriptors: list[dict[str, Any]],
    runs_root: Path,
) -> set[tuple[str, str]]:
    """Reconcile database attempts against descriptors found on disk."""
    touched: set[tuple[str, str]] = set()
    descriptors_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for descriptor in descriptors:
        identity = (
            str(descriptor.get("assignment_id") or ""),
            str(descriptor.get("student_id") or ""),
        )
        if identity[0] and identity[1]:
            descriptors_by_identity[identity].append(descriptor)

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
        all_db_rows = [_row_to_dict(row) for row in existing_rows]
        existing_identity = filter_attempts_for_root(all_db_rows, runs_root)
        existing_refs = {
            (str(row.get("run_id") or ""), str(row.get("batch_submission_id") or "")): row
            for row in existing_identity
        }
        used_attempt_numbers = {
            int(row.get("attempt_number") or 0)
            for row in all_db_rows
            if int(row.get("attempt_number") or 0) > 0
        }
        next_attempt_number = max(used_attempt_numbers, default=0) + 1

        descriptors_by_ref: dict[tuple[str, str], dict[str, Any]] = {}
        for descriptor in sorted(identity_descriptors, key=_descriptor_sort_key):
            descriptors_by_ref[_descriptor_ref(descriptor)] = descriptor

        for ref, descriptor in descriptors_by_ref.items():
            existing_attempt = existing_refs.get(ref)
            if existing_attempt is not None:
                _update_attempt_from_descriptor(conn, str(existing_attempt.get("id") or ""), descriptor)
                touched.add(identity)
                continue

            proposed_attempt_id = str(descriptor.get("id") or generate_attempt_id("attempt"))
            candidate_attempt_id = proposed_attempt_id
            existing_id_row = conn.execute(
                "SELECT * FROM submission_attempts WHERE id = ?",
                (candidate_attempt_id,),
            ).fetchone()
            if existing_id_row is not None:
                existing_id_attempt = _row_to_dict(existing_id_row)
                if (
                    _descriptor_ref(existing_id_attempt) != ref
                    or not _attempt_belongs_to_root(existing_id_attempt, runs_root)
                ):
                    candidate_attempt_id = generate_attempt_id("attempt")

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
                    descriptor.get("source_type") or "",
                    descriptor.get("source_actor_user_id") or "",
                    descriptor.get("created_at") or utc_now_iso(),
                    descriptor.get("submitted_at") or descriptor.get("created_at") or utc_now_iso(),
                    descriptor.get("original_filename") or "",
                    descriptor.get("source_ref") or "",
                    descriptor.get("ingestion_status") or "pending",
                    descriptor.get("pipeline_status") or "pending",
                    descriptor.get("validity_status") or "pending",
                    descriptor.get("run_id") or "",
                    descriptor.get("run_dir") or "",
                    descriptor.get("report_path") or "",
                    descriptor.get("batch_run_id") or "",
                    descriptor.get("batch_submission_id") or "",
                    descriptor.get("overall_score"),
                    descriptor.get("confidence") or "",
                    1 if descriptor.get("manual_review_required") else 0,
                    descriptor.get("error_message") or "",
                    utc_now_iso(),
                ),
            )
            existing_refs[ref] = {
                "id": candidate_attempt_id,
                "attempt_number": chosen_attempt_number,
            }
            used_attempt_numbers.add(chosen_attempt_number)
            touched.add(identity)

    conn.commit()
    return touched


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
