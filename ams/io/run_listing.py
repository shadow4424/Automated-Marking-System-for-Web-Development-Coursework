from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from ams.core.attempts import attempt_maps, sync_attempts_from_storage
from ams.io.json_utils import read_json_file, try_read_json


def load_run_info(run_dir: Path):
    """Load run metadata from run_info.json file."""
    info_path = run_dir / "run_info.json"
    if not info_path.exists():
        return None
    return try_read_json(info_path, default=None)


def _submission_identity(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str] | None:
    submission = submission or {}
    student_id = str(submission.get("student_id") or run.get("student_id") or "").strip()
    assignment_id = str(submission.get("assignment_id") or run.get("assignment_id") or "").strip()
    if not student_id or not assignment_id:
        return None
    return assignment_id, student_id


# Build the stable reference used for one submission record.
def _submission_ref(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str | None]:
    submission = submission or {}
    submission_id = submission.get("submission_id")
    if isinstance(submission_id, str) and submission_id.strip():
        return str(run.get("id") or ""), submission_id
    return str(run.get("id") or ""), None


# Build the sort key used for submission records.
def _submission_sort_key(run: Mapping[str, object], submission: Mapping[str, object] | None = None) -> tuple[str, str, str]:
    run_id, submission_id = _submission_ref(run, submission)
    return (
        str(run.get("created_at") or ""),
        run_id,
        submission_id or "",
    )


# Normalise run status values into a smaller set.
def _normalize_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"", "ok", "success", "succeeded", "completed", "complete"}:
        return "ok"
    return status


# Clean and prefix a review message when needed.
def _build_review_message(value: object, *, prefix: str = "") -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if prefix and not text.lower().startswith(prefix.lower()):
        text = f"{prefix}{text}"
    return text


# Read the status field from a nested metadata block.
def _get_run_status(meta: Mapping[str, Any]) -> object:
    return meta.get("status", "unknown")


# Extract threat and LLM review flags from a report.
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


# Remove duplicate review flags while preserving order.
def _deduplicate_review_flags(flags: list[str]) -> list[str]:
    deduplicated: list[str] = []
    for flag in flags:
        message = _build_review_message(flag)
        if message and message not in deduplicated:
            deduplicated.append(message)
    return deduplicated


# Build the public review-flag summary for a report.
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


# Check whether a run or submission should count as active.
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


# Collect assignment ids referenced by batch submissions.
def _assignment_ids_from_submissions(run: Mapping[str, object]) -> list[str]:
    return sorted(
        {
            str(submission.get("assignment_id") or "").strip()
            for submission in list(run.get("submissions", []) or [])
            if str(submission.get("assignment_id") or "").strip()
            and _submission_is_active_candidate(run, submission)
        }
    )


# Copy attempt metadata onto a run or submission record.
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


# Keep only the latest submission for each identity.
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


# Traverse run directories and build run records.
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


# Filter runs by active state.
def _filter_runs(runs: list[dict], *, only_active: bool) -> list[dict]:
    return _filter_latest_submissions(runs, only_active=only_active)


# Sort runs into the expected display order.
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


# List stored runs under the runs root.
def list_runs(runs_root: Path, only_active: bool = True) -> list[dict]:
    """List all runs, searching recursively through the nested directory structure."""
    if not runs_root.exists():
        return []
    runs = _traverse_run_directories(runs_root, only_active=only_active)
    runs = _filter_runs(runs, only_active=only_active)
    return _sort_and_paginate_runs(runs, only_active=only_active)


# Find the directory for one run id.
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


# Check whether a filename is allowed for download.
