"""Attempt synchronisation, reconciliation and file-management helpers.

Extracted from attempts.py to reduce module size.  The public API
(``recompute_active_attempt``, ``sync_attempts_from_storage``,
``get_student_assignment_summary``) is re-exported by ``attempts``
so existing call-sites keep working.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from ams.core.database import get_db
from ams.io.json_utils import try_read_json, write_json_file
from ams.io.metadata import MetadataValidator

# Lazy imports from the sibling module to avoid circularity.
from ams.core.attempts import (
    _attempt_belongs_to_root,
    _attempt_persistence_values,
    _ensure_submission_metadata,
    _row_to_dict,
    filter_attempts_for_root,
    generate_attempt_id,
    list_attempts,
    utc_now_iso,
)

# ---------------------------------------------------------------------------
# Report-analysis helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any] | None:
    """Load JSON data from disk."""
    return try_read_json(path, default=None)


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


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# File synchronisation
# ---------------------------------------------------------------------------

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
                    submission_metadata = _ensure_submission_metadata(report)
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
        write_json_file(metadata_path, metadata_payload, indent=2, sort_keys=True)

        run_info_path = update.get("run_info_path")
        run_info_payload = update.get("run_info_payload")
        if run_info_path is not None and run_info_payload is not None:
            write_json_file(run_info_path, run_info_payload, indent=2, sort_keys=True)

        report_path = update.get("report_path")
        report_payload = update.get("report_payload")
        if report_path is not None and report_payload is not None:
            write_json_file(report_path, report_payload, indent=2)


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
    write_json_file(summary_root / "active.json", active_payload, indent=2)

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


# ---------------------------------------------------------------------------
# Active-attempt recomputation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Descriptor-based reconciliation
# ---------------------------------------------------------------------------

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
            descriptor_with_identity = {
                **descriptor,
                "id": candidate_attempt_id,
                "assignment_id": assignment_id,
                "student_id": student_id,
                "attempt_number": chosen_attempt_number,
            }
            shared_values = _attempt_persistence_values(descriptor_with_identity)

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
