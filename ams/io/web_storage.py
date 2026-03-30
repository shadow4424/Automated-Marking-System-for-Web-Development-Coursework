from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Tuple, List
from zipfile import ZipFile

from ams.core.attempts import attempt_maps, sync_attempts_from_storage
from ams.io.fs_utils import _prune_empty_parents, _remove_path_within
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
    """Resolve the actual submission root within an extracted zip."""
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
    """Load run metadata from run_info.json file."""
    info_path = run_dir / "run_info.json"
    if not info_path.exists():
        return None
    return json.loads(info_path.read_text(encoding="utf-8"))


def _submission_identity(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str] | None:
    submission = submission or {}
    student_id = str(submission.get("student_id") or run.get("student_id") or "").strip()
    assignment_id = str(submission.get("assignment_id") or run.get("assignment_id") or "").strip()
    if not student_id or not assignment_id:
        return None
    return assignment_id, student_id


def _submission_ref(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str | None]:
    submission = submission or {}
    submission_id = submission.get("submission_id")
    if isinstance(submission_id, str) and submission_id.strip():
        return str(run.get("id") or ""), submission_id
    return str(run.get("id") or ""), None


def _submission_sort_key(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str, str]:
    run_id, submission_id = _submission_ref(run, submission)
    return (
        str(run.get("created_at") or ""),
        run_id,
        submission_id or "",
    )


def _normalize_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"", "ok", "success", "succeeded", "completed", "complete"}:
        return "ok"
    return status


def _build_review_message(value: object, *, prefix: str = "") -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if prefix and not text.lower().startswith(prefix.lower()):
        text = f"{prefix}{text}"
    return text


def _get_run_status(meta: Mapping[str, Any]) -> object:
    return meta.get("status", "unknown")


def _parse_review_flag_data(report: Mapping[str, Any]) -> tuple[int, bool, list[str]]:
    metadata = report.get("metadata", {}) or {}
    findings = list(report.get("findings", []) or [])

    threat_override = bool(metadata.get("threat_override"))
    threat_count = sum(1 for finding in findings if finding.get("severity") == "THREAT")

    raw_messages: list[str] = []
    explicit_messages = metadata.get("llm_error_messages")
    if isinstance(explicit_messages, list):
        for item in explicit_messages:
            message = _build_review_message(item)
            if message:
                raw_messages.append(message)
    message = _build_review_message(metadata.get("llm_error_message"))
    if message:
        raw_messages.append(message)
    if metadata.get("llm_error_detected") and not raw_messages:
        raw_messages.append("LLM-assisted marking failed and requires review.")

    for finding in findings:
        finding_id = str(finding.get("id") or "LLM")
        evidence = finding.get("evidence", {})
        if not isinstance(evidence, Mapping):
            continue

        if finding_id == "LLM.ERROR.REQUIRES_REVIEW":
            for item in list(evidence.get("llm_error_messages") or []):
                message = _build_review_message(item)
                if message:
                    raw_messages.append(message)
            message = _build_review_message(evidence.get("llm_error_message"))
            if message:
                raw_messages.append(message)

        llm_feedback = evidence.get("llm_feedback")
        if isinstance(llm_feedback, Mapping):
            meta = llm_feedback.get("meta", {}) or {}
            if isinstance(meta, Mapping) and meta.get("fallback"):
                reason = str(meta.get("reason") or "").strip().lower()
                error = str(meta.get("error") or "").strip()
                if reason == "llm_error" or error:
                    message = _build_review_message(
                        error or "LLM feedback generation failed.",
                        prefix=f"{finding_id}: ",
                    )
                    if message:
                        raw_messages.append(message)

        hybrid_score = evidence.get("hybrid_score")
        if isinstance(hybrid_score, Mapping):
            reasoning = str(hybrid_score.get("reasoning") or "").strip()
            raw_response = hybrid_score.get("raw_response")
            if isinstance(raw_response, Mapping) and raw_response.get("error"):
                message = _build_review_message(
                    raw_response.get("error"),
                    prefix=f"{finding_id}: ",
                )
                if message:
                    raw_messages.append(message)
            elif "llm error" in reasoning.lower() or "llm parse error" in reasoning.lower():
                message = _build_review_message(
                    reasoning,
                    prefix=f"{finding_id}: ",
                )
                if message:
                    raw_messages.append(message)

        ux_review = evidence.get("ux_review")
        if isinstance(ux_review, Mapping):
            status = str(_get_run_status(ux_review) or "").strip().upper()
            feedback = str(ux_review.get("feedback") or "").strip()
            if status == "NOT_EVALUATED" and (
                feedback.lower().startswith("llm error:")
                or feedback.lower() == "could not parse model response."
            ):
                page_name = str(ux_review.get("page") or evidence.get("page") or finding_id).strip()
                message = _build_review_message(
                    feedback,
                    prefix=f"{page_name}: ",
                )
                if message:
                    raw_messages.append(message)

        vision_analysis = evidence.get("vision_analysis")
        if isinstance(vision_analysis, Mapping):
            status = str(_get_run_status(vision_analysis) or "").strip().upper()
            meta = vision_analysis.get("meta", {}) or {}
            if status == "NOT_EVALUATED" and isinstance(meta, Mapping):
                reason = str(meta.get("reason") or "").strip().lower()
                error = str(meta.get("error") or "").strip()
                if reason in {"llm_error", "parse_error"} or error:
                    message = _build_review_message(
                        error or reason or "Vision analysis could not be completed.",
                        prefix=f"{finding_id}: ",
                    )
                    if message:
                        raw_messages.append(message)

    return threat_count, threat_override, raw_messages


def _deduplicate_review_flags(flags: list[str]) -> list[str]:
    deduplicated: list[str] = []
    for flag in flags:
        message = _build_review_message(flag)
        if message and message not in deduplicated:
            deduplicated.append(message)
    return deduplicated


def extract_review_flags_from_report(report: Mapping[str, Any]) -> dict[str, object]:
    threat_count, threat_override, raw_messages = _parse_review_flag_data(report)
    llm_messages = _deduplicate_review_flags(raw_messages)

    return {
        "threat_count": threat_count,
        "threat_flagged": threat_count > 0 and not threat_override,
        "llm_error_flagged": bool(llm_messages),
        "llm_error_messages": llm_messages,
        "llm_error_message": llm_messages[0] if llm_messages else None,
    }


def _submission_is_active_candidate(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> bool:
    if submission is not None and (
        "attempt_id" in submission or "is_active" in submission or "validity_status" in submission
    ):
        validity_status = str(submission.get("validity_status") or "").strip().lower()
        if validity_status == "valid":
            return bool(submission.get("is_active"))
        if validity_status in {"invalid", "pending"}:
            return False
        return bool(submission.get("is_active"))
    if submission is None and ("attempt_id" in run or "is_active" in run or "validity_status" in run):
        validity_status = str(run.get("validity_status") or "").strip().lower()
        if validity_status == "valid":
            return bool(run.get("is_active"))
        if validity_status in {"invalid", "pending"}:
            return False
        return bool(run.get("is_active"))
    if submission is not None:
        if submission.get("invalid") is True:
            return False
        return not _normalize_status(submission.get("status")).startswith("invalid")
    return not _normalize_status(run.get("status")).startswith("invalid")


def _assignment_ids_from_submissions(run: Mapping[str, object]) -> list[str]:
    return sorted(
        {
            str(submission.get("assignment_id") or "").strip()
            for submission in list(run.get("submissions", []) or [])
            if str(submission.get("assignment_id") or "").strip()
            and _submission_is_active_candidate(run, submission)
        }
    )


def _apply_attempt_metadata(target: dict[str, object], attempt: Mapping[str, object]) -> None:
    target["attempt_id"] = attempt.get("id")
    target["attempt_number"] = attempt.get("attempt_number")
    target["source_type"] = attempt.get("source_type")
    target["source_actor_user_id"] = attempt.get("source_actor_user_id")
    target["submitted_at"] = attempt.get("submitted_at")
    target["ingestion_status"] = attempt.get("ingestion_status")
    target["pipeline_status"] = attempt.get("pipeline_status")
    target["validity_status"] = attempt.get("validity_status")
    target["confidence"] = attempt.get("confidence")
    target["manual_review_required"] = bool(attempt.get("manual_review_required"))
    target["is_active"] = bool(attempt.get("is_active"))
    target["selection_reason"] = attempt.get("selection_reason")
    if attempt.get("overall_score") is not None and target.get("overall") is None:
        target["overall"] = attempt.get("overall_score")
    if attempt.get("report_path") and not target.get("report_path"):
        target["report_path"] = attempt.get("report_path")
    if attempt.get("batch_run_id"):
        target["batch_run_id"] = attempt.get("batch_run_id")
    if attempt.get("batch_submission_id"):
        target["batch_submission_id"] = attempt.get("batch_submission_id")


def _filter_latest_submissions(
    runs: list[dict],
    *,
    only_active: bool,
) -> list[dict]:
    if not only_active:
        return list(runs)

    latest_by_identity: dict[tuple[str, str], tuple[tuple[str, str, str], tuple[str, str | None]]] = {}

    for run in runs:
        submissions = list(run.get("submissions", []) or [])
        if run.get("mode") == "batch" and submissions:
            for submission in submissions:
                if not _submission_is_active_candidate(run, submission):
                    continue
                identity = _submission_identity(run, submission)
                if identity is None:
                    continue
                candidate = (_submission_sort_key(run, submission), _submission_ref(run, submission))
                current = latest_by_identity.get(identity)
                if current is None or candidate[0] > current[0]:
                    latest_by_identity[identity] = candidate
            continue

        if not _submission_is_active_candidate(run):
            continue
        identity = _submission_identity(run, submissions[0] if submissions else None)
        if identity is None:
            continue
        candidate = (_submission_sort_key(run, submissions[0] if submissions else None), _submission_ref(run, submissions[0] if submissions else None))
        current = latest_by_identity.get(identity)
        if current is None or candidate[0] > current[0]:
            latest_by_identity[identity] = candidate

    filtered_runs: list[dict] = []
    for run in runs:
        submissions = list(run.get("submissions", []) or [])
        if run.get("mode") == "batch":
            kept_submissions: list[dict] = []
            had_active_candidates = False
            for submission in submissions:
                if not _submission_is_active_candidate(run, submission):
                    continue
                had_active_candidates = True
                identity = _submission_identity(run, submission)
                if identity is None:
                    kept_submissions.append(submission)
                    continue
                latest = latest_by_identity.get(identity)
                if latest and latest[1] == _submission_ref(run, submission):
                    kept_submissions.append(submission)

            if submissions and not kept_submissions:
                if had_active_candidates:
                    continue
                run_copy = dict(run)
                run_copy["submissions"] = []
                filtered_runs.append(run_copy)
                continue

            run_copy = dict(run)
            run_copy["submissions"] = kept_submissions
            filtered_runs.append(run_copy)
            continue

        if not _submission_is_active_candidate(run):
            continue
        identity = _submission_identity(run, submissions[0] if submissions else None)
        if identity is None:
            filtered_runs.append(run)
            continue

        latest = latest_by_identity.get(identity)
        if latest and latest[1] == _submission_ref(run, submissions[0] if submissions else None):
            filtered_runs.append(run)

    return filtered_runs


def _traverse_run_directories(runs_root: Path, *, only_active: bool) -> list[dict]:
    runs: list[dict] = []
    sync_attempts_from_storage(runs_root)
    mark_attempt_map, batch_attempt_map = attempt_maps(runs_root)

    for run_info_path in runs_root.rglob("run_info.json"):
        run_dir = run_info_path.parent
        info = load_run_info(run_dir)
        if info:
            if only_active and info.get("active") is False:
                continue
            info["id"] = str(info.get("id") or run_dir.name)
            info["_run_dir"] = str(run_dir)
            batch_summary_data: dict | None = None

            report_path = run_dir / "report.json"
            run_status = str(_get_run_status(info) or "").strip().lower()
            if report_path.exists() and run_status not in {"pending", "failed", "error"}:
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    scores = report.get("scores", {})
                    if scores and "overall" in scores:
                        info["score"] = scores["overall"] * 100
                    review_flags = extract_review_flags_from_report(report)
                    info["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                    if review_flags.get("threat_count"):
                        info["threat_count"] = int(review_flags.get("threat_count") or 0)
                    else:
                        info.pop("threat_count", None)
                    info["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                    info["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
                    if info["llm_error_messages"]:
                        info["llm_error_message"] = str(info["llm_error_messages"][0])
                        if run_status in {"ok", "completed", "complete", "success", "succeeded", ""}:
                            info["status"] = "llm_error"
                    else:
                        info.pop("llm_error_message", None)
                except Exception:
                    pass

            if info.get("mode") == "batch":
                batch_summary_path = run_dir / "batch_summary.json"
                if batch_summary_path.exists():
                    try:
                        batch_summary_data = json.loads(batch_summary_path.read_text(encoding="utf-8"))
                    except Exception:
                        batch_summary_data = None

            index_path = run_dir / "run_index.json"
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    info["submissions"] = index.get("submissions", [])
                except Exception:
                    info["submissions"] = []
            elif info.get("mode") == "batch":
                if batch_summary_data is not None:
                    try:
                        info["submissions"] = []
                        for rec in batch_summary_data.get("records", []):
                            student_val = rec.get("student_id") or rec.get("id", "Unknown")
                            info["submissions"].append({
                                "submission_id": rec.get("id"),
                                "student_name": student_val,
                                "student_id": student_val,
                                "assignment_id": rec.get("assignment_id") or info.get("assignment_id"),
                                "original_filename": rec.get("original_filename"),
                                "upload_timestamp": rec.get("upload_timestamp") or info.get("created_at"),
                                "overall": rec.get("overall"),
                                "components": rec.get("components") or {},
                                "threat_count": rec.get("threat_count"),
                                "threat_flagged": bool(rec.get("threat_flagged") or rec.get("threat_count")),
                                "llm_error_flagged": bool(rec.get("llm_error_flagged")),
                                "llm_error_message": rec.get("llm_error_message"),
                                "llm_error_messages": list(rec.get("llm_error_messages") or []),
                                "status": rec.get("status"),
                                "invalid": rec.get("invalid"),
                                "error": rec.get("error") or rec.get("validation_error"),
                            })
                    except Exception:
                        pass
            elif info.get("mode") == "mark" and info.get("student_id") and info.get("assignment_id"):
                info["submissions"] = [{
                    "submission_id": info.get("id"),
                    "student_name": info.get("student_id"),
                    "student_id": info.get("student_id"),
                    "assignment_id": info.get("assignment_id"),
                    "original_filename": info.get("original_filename"),
                    "upload_timestamp": info.get("created_at"),
                    "overall": (float(info.get("score")) / 100.0) if info.get("score") is not None else None,
                    "status": info.get("status"),
                    "invalid": False,
                    "threat_flagged": bool(info.get("threat_flagged")),
                    "llm_error_flagged": bool(info.get("llm_error_flagged")),
                    "llm_error_message": info.get("llm_error_message"),
                    "llm_error_messages": list(info.get("llm_error_messages") or []),
                }]
            elif info.get("mode") == "batch":
                pending_submissions = info.get("pending_submissions", []) or []
                if isinstance(pending_submissions, list):
                    info["submissions"] = [dict(sub) for sub in pending_submissions]

            if info.get("mode") == "batch":
                info["submissions"] = [
                    dict(submission)
                    for submission in list(info.get("submissions", []) or [])
                    if not str((submission or {}).get("materialized_run_id") or "").strip()
                ]

            for sub in info.get("submissions", []):
                if not sub.get("student_name"):
                    sub["student_name"] = sub.get("student_id") or "Unknown"
                if not sub.get("student_id"):
                    sub["student_id"] = sub.get("student_name") or "Unknown"
                if sub.get("invalid") is None:
                    sub["invalid"] = False
                attempt = batch_attempt_map.get((str(info.get("id") or ""), str(sub.get("submission_id") or "")))
                if attempt:
                    _apply_attempt_metadata(sub, attempt)

            if info.get("mode") == "batch" and batch_summary_data is not None and info.get("submissions"):
                summary_by_id = {
                    str(record.get("id") or ""): record
                    for record in batch_summary_data.get("records", []) or []
                    if str(record.get("id") or "")
                }
                for sub in info.get("submissions", []):
                    record = summary_by_id.get(str(sub.get("submission_id") or ""))
                    if not record:
                        continue
                    if not sub.get("status"):
                        sub["status"] = record.get("status")
                    if sub.get("invalid") is False and record.get("invalid") is True:
                        sub["invalid"] = True
                    if not sub.get("error"):
                        sub["error"] = record.get("error") or record.get("validation_error")
                    if not sub.get("assignment_id"):
                        sub["assignment_id"] = record.get("assignment_id")
                    if sub.get("overall") is None:
                        sub["overall"] = record.get("overall")
                    if not sub.get("components"):
                        sub["components"] = record.get("components") or {}
                    if not sub.get("threat_count"):
                        sub["threat_count"] = record.get("threat_count")
                    if not sub.get("threat_flagged"):
                        sub["threat_flagged"] = bool(record.get("threat_flagged") or record.get("threat_count"))
                    if not sub.get("llm_error_flagged"):
                        sub["llm_error_flagged"] = bool(record.get("llm_error_flagged"))
                    if not sub.get("llm_error_message"):
                        sub["llm_error_message"] = record.get("llm_error_message")
                    if not sub.get("llm_error_messages"):
                        sub["llm_error_messages"] = list(record.get("llm_error_messages") or [])
                    if (
                        sub.get("llm_error_flagged")
                        and str(sub.get("status") or "").strip().lower() in {"", "ok", "completed", "complete", "success", "succeeded"}
                    ):
                        sub["status"] = "llm_error"

            assignment_ids = _assignment_ids_from_submissions(info)
            if assignment_ids:
                info["_assignment_ids"] = assignment_ids
                if len(assignment_ids) == 1 and str(info.get("assignment_id") or "").strip() not in assignment_ids:
                    info["assignment_id"] = assignment_ids[0]
            if info.get("mode") == "mark":
                attempt = mark_attempt_map.get(str(info.get("id") or ""))
                if attempt:
                    _apply_attempt_metadata(info, attempt)
                    submissions = list(info.get("submissions", []) or [])
                    if submissions:
                        _apply_attempt_metadata(submissions[0], attempt)
            runs.append(info)

    return runs


def _filter_runs(runs: list[dict], *, only_active: bool) -> list[dict]:
    return _filter_latest_submissions(runs, only_active=only_active)


def _sort_and_paginate_runs(runs: list[dict], *, only_active: bool) -> list[dict]:
    if not only_active:
        runs.sort(
            key=lambda r: (
                str(r.get("created_at") or ""),
                str(r.get("id") or ""),
            ),
            reverse=True,
        )
        return runs

    runs.sort(key=lambda r: r.get("id", ""), reverse=True)
    return runs


def list_runs(runs_root: Path, only_active: bool = True) -> list[dict]:
    """List all runs, searching recursively through the nested directory structure."""
    if not runs_root.exists():
        return []
    runs = _traverse_run_directories(runs_root, only_active=only_active)
    runs = _filter_runs(runs, only_active=only_active)
    return _sort_and_paginate_runs(runs, only_active=only_active)


def find_run_by_id(runs_root: Path, run_id: str) -> Optional[Path]:
    """Find a run directory by its ID, searching recursively."""
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
        try:
            run_info = json.loads(run_info_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(run_info.get("id") or "") == str(run_id):
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
