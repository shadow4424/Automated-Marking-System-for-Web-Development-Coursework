from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import requests as _requests
from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from ams.core.attempts import (
    filter_attempts_for_root,
    get_attempt_by_run_reference,
    get_student_assignment_summary,
    list_attempts,
    recompute_active_attempt,
    sync_attempts_from_storage,
)
from ams.core.config import ScoringMode
from ams.core.db import get_assignment, list_assignments
from ams.core.job_manager import job_manager
from ams.core.profiles import get_visible_profile_specs
from ams.io.export_report import (
    build_export_report,
    export_csv_zip,
    export_json as _export_json,
    export_pdf as _export_pdf,
    export_txt,
    validate_export_report,
)
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.io.web_storage import (
    cleanup_batch_run_storage,
    create_run_dir,
    extract_review_flags_from_report,
    find_submission_root,
    get_runs_root,
    load_run_info,
    safe_extract_zip,
    save_metadata,
    save_run_info,
    validate_file_size,
    validate_file_type,
)
from ams.tools.batch import discover_batch_items, run_batch, validate_submission_filename, write_outputs
from ams.web.auth import get_current_user, login_required, teacher_or_admin_required
from ams.web.route_helpers import find_run, load_run
from ams.web.routes_common import (
    is_async_job_request,
    json_error,
    redirect_with_flash,
    submit_rerun_job,
)
from ams.web.routes_dashboard import (
    _assignment_submission_locked,
    _submission_lock_message,
    _user_can_access_assignment,
)
from ams.web.routes_marking import (
    _build_pipeline,
    _build_rerun_metadata,
    _build_submission_detail_view,
    _clear_rerun_outputs,
    _prepare_source_tree,
    _prepare_zip_source,
    _queue_mark_submission_rerun,
    _replace_existing_submissions,
    _submission_identity,
)
from ams.web.validators import validate_is_zipfile
from ams.web.view_helpers import *

logger = logging.getLogger(__name__)
batch_bp = Blueprint('batch', __name__)
MAX_UPLOAD_MB = 25
PROFILE_CHOICES = tuple(get_visible_profile_specs().keys())


# Build batch readme.
# Build the batch export readme text.
def _build_batch_readme(*args, **kwargs):
    from ams.web.routes_export import _build_batch_readme as builder

    return builder(*args, **kwargs)


# Discover pending batch submissions.
# Discover pending batch submissions for one upload.
def _discover_pending_batch_submissions(submissions_root: Path, assignment_id: str, upload_timestamp: str) -> list[dict]:
    pending_by_identity: dict[tuple[str, str], dict] = {}

    for item in discover_batch_items(submissions_root):
        is_valid, parsed_student_id, parsed_assignment_id = validate_submission_filename(item.path.name)
        if not is_valid or parsed_assignment_id != assignment_id:
            continue

        student_id = MetadataValidator.sanitize_identifier(parsed_student_id)
        identity = _submission_identity(student_id, assignment_id)
        if identity is None:
            continue

        pending_by_identity[identity] = {
            "submission_id": item.id,
            "student_name": student_id,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": item.path.name,
            "upload_timestamp": upload_timestamp,
            "status": "pending",
        }

    return sorted(pending_by_identity.values(), key=lambda sub: (sub.get("student_id", ""), sub.get("submission_id", "")))

# Resolve the report path for a batch submission record.
# Resolve the stored report path for a batch record.
def _batch_report_path(run_dir: Path, record: Mapping[str, object]) -> Path | None:
    report_path = record.get("report_path")
    if isinstance(report_path, str) and report_path:
        candidate = Path(report_path)
        if candidate.exists():
            return candidate

    submission_id = record.get("id")
    if isinstance(submission_id, str) and submission_id:
        candidate = run_dir / "runs" / submission_id / "report.json"
        if candidate.exists():
            return candidate

    return None

# Rebuild batch outputs.
# Rebuild batch output files after records change.
def _rebuild_batch_outputs(run_dir: Path, run_info: dict, records: list[dict]) -> None:
    if not records:
        shutil.rmtree(run_dir, ignore_errors=True)
        return

    profile = run_info.get("profile", "frontend")
    write_outputs(run_dir, records, profile=profile)

    updated_run_info = dict(run_info)
    updated_run_info["batch_summary"] = {"records": records}
    save_run_info(run_dir, updated_run_info)
    _write_run_index_batch(run_dir, updated_run_info)
    cleanup_batch_run_storage(run_dir, updated_run_info)


# Resolve the runs root from a nested batch run directory.
# Find the runs root for a batch run directory.
def _runs_root_for_run_dir(run_dir: Path) -> Path:
    try:
        return run_dir.parents[2]
    except IndexError:
        try:
            return get_runs_root(current_app)
        except RuntimeError:
            return run_dir.parent


def _render_batch_page(
    assignments: list[dict],
    *,
    selected_assignment_id: str = "",
    github_connected: bool = False,
    github_user: str = "",
    status_code: int = 200,
):
    response = render_template(
        "marking/batch.html",
        assignments=assignments,
        selected_assignment_id=selected_assignment_id,
        github_connected=github_connected,
        github_user=github_user,
    )
    return response if status_code == 200 else (response, status_code)


def _cleanup_temp_zip(path: Path | None) -> None:
    if path is None or not path.exists():
        return
    try:
        path.unlink()
    except Exception:
        pass

# Process the main batch upload flow.
@batch_bp.route("/batch", methods=["GET", "POST"])
@teacher_or_admin_required
# Handle the main batch upload page.
def batch():
    # Filter assignments that can still accept submissions.
    def _available_batch_assignments(include_released: bool = False) -> list[dict]:
        if session.get("user_role") == "admin":
            assignments = list_assignments()
        else:
            assignments = list_assignments(teacher_id=session.get("user_id"))
        if not include_released:
            assignments = [
                assignment
                for assignment in assignments
                if not _assignment_submission_locked(assignment)
            ]
        return assignments

    assignment_options = _available_batch_assignments()
    selected_assignment_id = request.form.get("assignment_id", "").strip() if request.method == "POST" else ""

    if request.method == "GET":
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=bool(session.get("github_token")),
            github_user=session.get("github_user", ""),
        )

    # Stop early if sandboxing is required but Docker is unavailable.
    from ams.sandbox.config import get_sandbox_status, get_sandbox_config, SandboxMode
    _sb = get_sandbox_status()
    _cfg = get_sandbox_config()
    if _cfg.mode == SandboxMode.DOCKER and not _sb["enforced"]:
        flash(
            "Sandbox is required but Docker is not available. "
            "Cannot process submissions without sandboxing. "
            f"({_sb['message']})",
            "error",
        )
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=bool(session.get("github_token")),
            github_user=session.get("github_user", ""),
            status_code=503,
        )

    file = request.files.get("submission")
    assignment_id = request.form.get("assignment_id", "").strip()
    scoring_mode = ScoringMode("static_plus_llm")  # Always use static + LLM
    github_connected = bool(session.get("github_token"))
    github_user = session.get("github_user", "")
    assignment_options_all = _available_batch_assignments(include_released=True)
    assignment_map = {
        str(assignment.get("assignmentID") or "").strip(): assignment
        for assignment in assignment_options_all
    }

    # Decide whether this submission comes from GitHub or a ZIP upload.
    submission_method = request.form.get("submission_method", "upload")
    github_repo = request.form.get("github_repo", "").strip()
    github_branch = request.form.get("github_branch", "").strip()
    using_github = (submission_method == "github") and bool(github_repo)

    tmp_zip_path: Path | None = None

    if using_github:
        # GitHub submission path.
        github_token = session.get("github_token")
        if not github_token:
            flash("Please link your GitHub account first.", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=False,
                github_user=github_user,
                status_code=400,
            )

        # Validate repo format (owner/repo)
        if "/" not in github_repo or github_repo.count("/") != 1:
            flash("Invalid GitHub repository format. Use owner/repo.", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
                status_code=400,
            )

        try:
            # Branch-specific zipball
            if github_branch:
                zipball_url = f"https://api.github.com/repos/{github_repo}/zipball/{github_branch}"
            else:
                zipball_url = f"https://api.github.com/repos/{github_repo}/zipball"
            gh_resp = _requests.get(
                zipball_url,
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github+json",
                },
                stream=True,
                timeout=60,
            )
            gh_resp.raise_for_status()
        except _requests.RequestException as exc:
            logger.warning("GitHub zipball download failed for %s: %s", github_repo, exc)
            flash(f"Failed to download repository from GitHub: {exc}", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
                status_code=400,
            )

        # Save to a temporary ZIP file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            for chunk in gh_resp.iter_content(chunk_size=8192):
                tmp_file.write(chunk)
            tmp_zip_path = Path(tmp_file.name)

        branch_suffix = f"_{github_branch}" if github_branch else ""
        original_filename = f"{github_repo.replace('/', '_')}{branch_suffix}.zip"

    else:
        # ZIP upload path.
        if not file or not file.filename:
            flash("Please upload a .zip file or select a GitHub repository.", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
                status_code=400,
            )

        if not validate_file_type(file.filename):
            flash("Invalid file type. Please upload a .zip file.", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
                status_code=400,
            )

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            file.save(tmp_file.name)
            tmp_zip_path = Path(tmp_file.name)

        original_filename = MetadataValidator.sanitize_filename(file.filename)

    # Validate and resolve assignment
    valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
    if not valid_assignment:
        _cleanup_temp_zip(tmp_zip_path)
        flash(f"Invalid Assignment ID: {assignment_error}", "error")
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=github_connected,
            github_user=github_user,
            status_code=400,
        )

    # Sanitize
    assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
    assignment = assignment_map.get(assignment_id)
    if assignment is None:
        _cleanup_temp_zip(tmp_zip_path)
        flash("Select a valid assignment from the list.", "error")
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=github_connected,
            github_user=github_user,
            status_code=400,
        )
    if _assignment_submission_locked(assignment):
        _cleanup_temp_zip(tmp_zip_path)
        flash(_submission_lock_message(), "error")
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=github_connected,
            github_user=github_user,
            status_code=403,
        )
    profile = str(assignment.get("profile") or "frontend_interactive").strip()

    runs_root = get_runs_root(current_app)

    # Validate the uploaded archive before starting the batch run.
    if not validate_is_zipfile(tmp_zip_path):
        _cleanup_temp_zip(tmp_zip_path)
        flash("The uploaded file is not a valid ZIP archive.", "error")
        return _render_batch_page(
            assignment_options,
            selected_assignment_id=selected_assignment_id,
            github_connected=github_connected,
            github_user=github_user,
            status_code=400,
        )

    try:
        # Validate file size
        valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
        if not valid_size:
            flash(size_error or "File size exceeds maximum limit.", "error")
            return _render_batch_page(
                assignment_options,
                selected_assignment_id=selected_assignment_id,
                github_connected=github_connected,
                github_user=github_user,
                status_code=400,
            )

        uploader_extra: dict = {
            "ip_address": request.remote_addr or "unknown",
            "user_agent": request.headers.get("User-Agent", "unknown")[:200],
        }
        if using_github:
            uploader_extra["source"] = "github"
            uploader_extra["github_repo"] = github_repo

        batch_metadata = SubmissionMetadata(
            student_id="batch",  # Special identifier for batch runs
            assignment_id=assignment_id,
            timestamp=datetime.now(timezone.utc),
            original_filename=original_filename,
            uploader_metadata=uploader_extra,
        )

        run_id, run_dir = create_run_dir(
            runs_root=runs_root,
            mode="batch",
            profile=profile,
            metadata=batch_metadata,
        )

        # Store batch zip
        upload_zip = run_dir / original_filename
        shutil.copy2(tmp_zip_path, upload_zip)

        # Save batch metadata
        save_metadata(run_dir, batch_metadata)

        extracted = run_dir / "batch_inputs"
        extracted.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
        batch_inputs_root = find_submission_root(extracted)
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        pending_submissions = _discover_pending_batch_submissions(
            batch_inputs_root,
            assignment_id,
            created_at,
        )

        # Run the batch job in the background and return a job id.
        initial_run_info = {
            "id": run_id,
            "mode": "batch",
            "profile": profile,
            "scoring_mode": scoring_mode.value,
            "created_at": created_at,
            "assignment_id": assignment_id,
            "original_filename": original_filename,
            "source": "github" if using_github else "upload",
            "status": "pending",
            "pending_submissions": pending_submissions,
        }
        if using_github:
            initial_run_info["github_repo"] = github_repo
        save_run_info(run_dir, initial_run_info)
        _replace_existing_submissions(
            runs_root,
            [
                (
                    str(submission.get("assignment_id") or ""),
                    str(submission.get("student_id") or ""),
                )
                for submission in pending_submissions
            ],
            current_run_id=run_id,
        )

        def _run_batch_job() -> dict:
            """Executed in the thread pool."""
            try:
                batch_data = run_batch(
                    submissions_dir=extracted,
                    out_root=run_dir,
                    profile=profile,
                    keep_individual_runs=True,
                    assignment_id=assignment_id,
                    scoring_mode=scoring_mode,
                )
                run_info = {
                    "id": run_id,
                    "mode": "batch",
                    "profile": profile,
                    "scoring_mode": scoring_mode.value,
                    "created_at": initial_run_info["created_at"],
                    "summary": "batch_summary.json",
                    "batch_summary": batch_data,
                    "assignment_id": assignment_id,
                    "original_filename": original_filename,
                    "source": "github" if using_github else "upload",
                    "status": "completed",
                }
                if using_github:
                    run_info["github_repo"] = github_repo

                save_run_info(run_dir, run_info)
                _write_run_index_batch(run_dir, run_info)
                cleanup_batch_run_storage(run_dir, run_info)
                return {"run_id": run_id}
            except Exception as exc:
                failed_info = dict(initial_run_info, status="failed", error=str(exc))
                save_run_info(run_dir, failed_info)
                raise

        job_id = job_manager.submit_job("batch_mark", _run_batch_job)
        return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
    finally:
        # Clean up temporary file
        _cleanup_temp_zip(tmp_zip_path)

# Filter assignments that can still accept submissions.
# List assignments that can be used for batch uploads.
def _available_batch_assignments(include_released: bool = False) -> list[dict]:
    if session.get("user_role") == "admin":
        assignments = list_assignments()
    else:
        assignments = list_assignments(teacher_id=session.get("user_id"))
    if not include_released:
        assignments = [
            assignment
            for assignment in assignments
            if not _assignment_submission_locked(assignment)
        ]
    return assignments

# Load batch summary records.
# Load the stored batch summary records.
def _load_batch_summary_records(run_dir: Path) -> dict | None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return None
    try:
        batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(batch_summary, dict):
        return None
    records = batch_summary.get("records", []) or []
    if not isinstance(records, list):
        return None
    batch_summary["records"] = list(records)
    return batch_summary

# Persist batch outputs.
# Persist batch summary outputs after processing.
def _persist_batch_outputs(run_dir: Path, run_info: dict, records: list[dict]) -> dict:
    profile = str(run_info.get("profile") or "frontend")
    write_outputs(run_dir, records, profile=profile)
    updated_batch_summary = _load_batch_summary_records(run_dir) or {"records": list(records)}
    updated_run_info = dict(run_info)
    updated_run_info["batch_summary"] = updated_batch_summary
    updated_run_info["summary"] = "batch_summary.json"
    save_run_info(run_dir, updated_run_info)
    _write_run_index_batch(run_dir, updated_run_info)
    cleanup_batch_run_storage(run_dir, updated_run_info)
    return updated_run_info

# Resolve batch rerun source.
# Resolve the source files needed for a batch rerun.
def _resolve_batch_rerun_source(run_dir: Path, submission_id: str, record: Mapping[str, object]) -> tuple[Path, Path]:
    submission_dir = run_dir / "runs" / submission_id
    submission_root = submission_dir / "submission"
    if submission_root.exists():
        return submission_dir, _prepare_source_tree(submission_root, submission_dir / "rerun_source")

    extracted_dir = submission_dir / "extracted"
    if extracted_dir.exists():
        return submission_dir, find_submission_root(extracted_dir)

    source_value = str(record.get("path") or "").strip()
    if source_value:
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        else:
            source_path = source_path.resolve()
        if source_path.is_file():
            return submission_dir, _prepare_zip_source(source_path, submission_dir / "rerun_source")
        if source_path.is_dir():
            return submission_dir, _prepare_source_tree(source_path, submission_dir / "rerun_source")

    raise FileNotFoundError("Stored submission content is unavailable.")

# Apply batch report.
# Apply a fresh report to a batch summary record.
def _apply_batch_report(record: dict, report_path: Path, report: Mapping[str, object], assignment_id: str) -> None:
    meta = report.get("metadata", {}) or {}
    submission_meta = meta.get("submission_metadata", {}) or {}
    scores = report.get("scores", {}) or {}
    by_component = scores.get("by_component", {}) or {}
    review_flags = extract_review_flags_from_report(report)

    record["report_path"] = str(report_path)
    record["student_id"] = submission_meta.get("student_id") or record.get("student_id")
    record["assignment_id"] = submission_meta.get("assignment_id") or record.get("assignment_id") or assignment_id
    record["original_filename"] = submission_meta.get("original_filename") or record.get("original_filename")
    record["overall"] = scores.get("overall")
    record["components"] = {
        component: ((by_component.get(component) or {}).get("score"))
        for component in ("html", "css", "js", "php", "sql", "api")
    }
    llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
    record["status"] = "llm_error" if llm_error_flagged else "ok"
    record["pipeline_status"] = "failed" if llm_error_flagged else "completed"
    record["validity_status"] = "invalid" if llm_error_flagged else "valid"
    record["rerun_pending"] = False
    record["invalid"] = False
    record.pop("error", None)
    record.pop("validation_error", None)
    record["threat_flagged"] = bool(review_flags.get("threat_flagged"))
    if review_flags.get("threat_count"):
        record["threat_count"] = int(review_flags.get("threat_count") or 0)
    else:
        record.pop("threat_count", None)
    record["llm_error_flagged"] = llm_error_flagged
    record["llm_error_message"] = review_flags.get("llm_error_message")
    record["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])

# Rerun marking for one batch submission.
# Re-run one submission from a batch job.
def _rerun_batch_submission(run_dir: Path, run_info: Mapping[str, object], submission_id: str) -> dict:
    batch_summary = _load_batch_summary_records(run_dir)
    if batch_summary is None:
        raise FileNotFoundError("Batch summary could not be loaded.")
    records = list(batch_summary.get("records", []) or [])
    target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
    if target is None:
        raise FileNotFoundError("Batch submission record not found.")

    submission_dir, submission_source = _resolve_batch_rerun_source(run_dir, submission_id, target)
    metadata = _build_rerun_metadata(
        student_id=target.get("student_id"),
        assignment_id=target.get("assignment_id") or run_info.get("assignment_id"),
        original_filename=target.get("original_filename"),
        timestamp=target.get("upload_timestamp") or run_info.get("created_at"),
        source=run_info.get("source"),
        github_repo=run_info.get("github_repo"),
    )
    _clear_rerun_outputs(submission_dir)
    try:
        report_path = _build_pipeline(run_info).run(
            submission_path=submission_source,
            workspace_path=submission_dir,
            profile=str(run_info.get("profile") or "frontend"),
            metadata=metadata,
            skip_threat_scan=True,
        )
    except Exception as exc:
        target["report_path"] = None
        target["status"] = "error"
        target["rerun_pending"] = False
        target["overall"] = None
        target["components"] = {
            component: None for component in ("html", "css", "js", "php", "sql")
        }
        target["error"] = str(exc)
        target["invalid"] = False
        failed_run_info = dict(run_info)
        failed_run_info["status"] = "failed"
        failed_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _persist_batch_outputs(run_dir, failed_run_info, records)
        raise
    report = json.loads(report_path.read_text(encoding="utf-8"))
    _apply_batch_report(target, report_path, report, str(run_info.get("assignment_id") or ""))
    updated_run_info = dict(run_info)
    updated_run_info["status"] = "completed"
    updated_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated_run_info.pop("error", None)
    updated_run_info = _persist_batch_outputs(run_dir, updated_run_info, records)
    save_run_info(run_dir, updated_run_info)
    runs_root = _runs_root_for_run_dir(run_dir)
    assignment_id = str(target.get("assignment_id") or run_info.get("assignment_id") or "")
    student_id = str(target.get("student_id") or "")
    if assignment_id and student_id:
        sync_attempts_from_storage(runs_root)
        recompute_active_attempt(runs_root, assignment_id, student_id)
    return target

# Queue batch submission rerun.
# Queue a background rerun for one batch submission.
def _queue_batch_submission_rerun(
    run_dir: Path,
    run_info: Mapping[str, object],
    submission_id: str,
    *,
    view_url: str,
    refresh_url: str,
):
    batch_summary = _load_batch_summary_records(run_dir)
    if batch_summary is None:
        raise FileNotFoundError("Batch summary could not be loaded.")

    records = list(batch_summary.get("records", []) or [])
    target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
    if target is None:
        raise FileNotFoundError("Batch submission record not found.")
    if str(target.get("status") or "").strip().lower() == "pending":
        raise RuntimeError("Submission is already queued for rerun.")

    target["status"] = "pending"
    target["rerun_pending"] = True
    target["overall"] = None
    target["components"] = {
        component: None for component in ("html", "css", "js", "php", "sql")
    }
    target.pop("error", None)
    target.pop("validation_error", None)

    queued_run_info = dict(run_info)
    queued_run_info["status"] = "pending"
    queued_run_info["last_rerun_requested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    queued_run_info.pop("error", None)
    _persist_batch_outputs(run_dir, queued_run_info, records)

    # Run the batch rerun job and return the refresh payload.
    def _run_batch_rerun_job() -> dict:
        updated = _rerun_batch_submission(run_dir, queued_run_info, submission_id)
        return {
            "run_id": str(queued_run_info.get("id") or run_dir.name),
            "submission_id": submission_id,
            "assignment_id": str(updated.get("assignment_id") or queued_run_info.get("assignment_id") or ""),
            "student_id": str(updated.get("student_id") or submission_id),
            "view_url": view_url,
            "refresh_url": refresh_url,
        }

    assignment_id = str(target.get("assignment_id") or queued_run_info.get("assignment_id") or "")
    label = f"Rerun: {target.get('student_id') or submission_id}"
    return submit_rerun_job(
        job_manager,
        _run_batch_rerun_job,
        run_id=str(queued_run_info.get("id") or run_dir.name),
        label=label,
        assignment_id=assignment_id,
        view_url=view_url,
        refresh_url=refresh_url,
    )

# Queue or run a rerun for one assignment submission.
@batch_bp.route("/teacher/assignment/<assignment_id>/submissions/rerun", methods=["POST"])
@batch_bp.route("/teacher/assignment/<assignment_id>/threats/reprocess", methods=["POST"])
@teacher_or_admin_required
# Queue a rerun from the assignment-level batch view.
def assignment_submission_rerun(assignment_id: str):
    if not _user_can_access_assignment(assignment_id):
        if is_async_job_request():
            return json_error("You do not have access to this assignment.", 403)
        return redirect_with_flash("teacher_dashboard.dashboard", "You do not have access to this assignment.", "error")

    run_id = str(request.form.get("run_id") or "").strip()
    submission_id = str(request.form.get("submission_id") or "").strip()
    if not run_id:
        if is_async_job_request():
            return json_error("Rerun failed: missing run ID.", 400)
        return redirect_with_flash(
            "assignment_mgmt.assignment_detail",
            "Rerun failed: missing run ID.",
            "error",
            assignment_id=assignment_id,
        )

    run_dir, run_info = load_run(run_id)
    if run_dir is None:
        if is_async_job_request():
            return json_error("Rerun failed: submission not found.", 404)
        return redirect_with_flash(
            "assignment_mgmt.assignment_detail",
            "Rerun failed: submission not found.",
            "error",
            assignment_id=assignment_id,
        )

    try:
        if run_info.get("mode") == "batch":
            if not submission_id:
                if is_async_job_request():
                    return json_error("Rerun failed: missing batch submission ID.", 400)
                return redirect_with_flash(
                    "assignment_mgmt.assignment_detail",
                    "Rerun failed: missing batch submission ID.",
                    "error",
                    assignment_id=assignment_id,
                )
            return _queue_batch_submission_rerun(
                run_dir,
                run_info,
                submission_id,
                view_url=url_for("batch.batch_submission_view", run_id=run_id, submission_id=submission_id),
                refresh_url=url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id),
            )
        return _queue_mark_submission_rerun(
            run_dir,
            run_info,
            view_url=url_for("runs.run_detail", run_id=run_id),
            refresh_url=url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id),
        )
    except Exception as exc:
        if is_async_job_request():
            return json_error(str(exc), 400)
        return redirect_with_flash(
            "assignment_mgmt.assignment_detail",
            f"Rerun failed: {exc}",
            "error",
            assignment_id=assignment_id,
        )

@batch_bp.route("/batch/<run_id>/submissions/<submission_id>/view")
@login_required
# Show one submission from a batch run.
def batch_submission_view(run_id: str, submission_id: str):
    """View a batch submission's report in the browser (like a single submission)."""
    runs_root = get_runs_root(current_app)
    sync_attempts_from_storage(runs_root)
    run_dir = find_run(run_id)
    if run_dir is None:
        return "Run not found", 404

    submission_dir = run_dir / "runs" / submission_id
    report_path = submission_dir / "report.json"

    # Security check
    try:
        report_path.resolve().relative_to(run_dir.resolve())
    except Exception:
        return "Not allowed", 403

    run_info = load_run_info(run_dir) or {}
    batch_summary = _load_batch_summary_records(run_dir) or {}
    record = next(
        (
            item
            for item in list(batch_summary.get("records", []) or [])
            if str(item.get("id") or "") == submission_id
        ),
        None,
    )
    record_status = str((record or {}).get("status") or "ok").strip().lower()

    report = None
    if record_status not in {"pending", "failed", "error"}:
        if not report_path.exists():
            return "Report not found", 404
        report = ensure_check_stats(
            json.loads(report_path.read_text(encoding="utf-8"))
        )
        review_flags = extract_review_flags_from_report(report)
        if record is not None:
            record["threat_flagged"] = bool(review_flags.get("threat_flagged"))
            record["threat_count"] = int(review_flags.get("threat_count") or 0)
            record["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
            record["llm_error_message"] = review_flags.get("llm_error_message")
            record["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
            if record["llm_error_flagged"] and record_status in {"", "ok", "completed", "complete", "success", "succeeded"}:
                record_status = "llm_error"

    # Extract real student_id from batch_summary or report metadata
    # Submission_id is the full stem like "testStudent_test_assignment1.
    # We need just the parsed student part e.g. "testStudent")
    real_student_id = submission_id  # Fallback
    real_assignment_id = run_info.get("assignment_id", "")
    attempt = get_attempt_by_run_reference(run_id, submission_id, runs_root=runs_root)

    # Use report metadata first because it is the most reliable source.
    report_meta = (report or {}).get("metadata", {}).get("submission_metadata", {})
    if report_meta.get("student_id"):
        real_student_id = report_meta["student_id"]
    elif record and record.get("student_id"):
        real_student_id = str(record.get("student_id"))
    if report_meta.get("assignment_id"):
        real_assignment_id = report_meta["assignment_id"]
    elif record and record.get("assignment_id"):
        real_assignment_id = str(record.get("assignment_id"))

    # Fallback: try batch_summary.json records
    if real_student_id == submission_id:
        for rec in batch_summary.get("records", []) or []:
            if rec.get("id") == submission_id:
                real_student_id = rec.get("student_id", submission_id)
                real_assignment_id = rec.get("assignment_id", real_assignment_id)
                break

    user = get_current_user()
    if user and user["role"] == "student" and real_student_id != user["userID"]:
        flash("You do not have access to this submission.", "error")
        return redirect(url_for("student.dashboard"))

    assignment = get_assignment(real_assignment_id) if real_assignment_id else None
    marks_released = assignment["marks_released"] if assignment else True
    attempt_history = []
    attempt_summary = None
    if real_assignment_id and real_student_id:
        attempt_history = filter_attempts_for_root(
            list_attempts(
                assignment_id=str(real_assignment_id),
                student_id=str(real_student_id),
                newest_first=True,
            ),
            runs_root,
        )
        attempt_summary = get_student_assignment_summary(
            str(real_assignment_id),
            str(real_student_id),
        )
    back_url = (
        url_for("student.coursework")
        if user and user["role"] == "student"
        else (
            url_for("assignment_mgmt.assignment_detail", assignment_id=run_info.get("assignment_id", ""))
            if run_info.get("assignment_id")
            else url_for("runs.runs")
        )
    )

    submission_run_info = {
        "mode": "mark",
        "profile": run_info.get("profile", "frontend"),
        "assignment_id": real_assignment_id,
        "student_id": real_student_id,
        "created_at": run_info.get("created_at", ""),
        "status": record_status,
        "llm_error_flagged": bool((record or {}).get("llm_error_flagged")),
        "llm_error_message": (record or {}).get("llm_error_message"),
        "llm_error_messages": list((record or {}).get("llm_error_messages") or []),
    }
    if attempt:
        submission_run_info.update(
            {
                "attempt_id": attempt.get("id"),
                "attempt_number": attempt.get("attempt_number"),
                "source_type": attempt.get("source_type"),
                "source_actor_user_id": attempt.get("source_actor_user_id"),
                "submitted_at": attempt.get("submitted_at"),
                "validity_status": attempt.get("validity_status"),
                "confidence": attempt.get("confidence"),
                "manual_review_required": bool(attempt.get("manual_review_required")),
                "is_active": bool(attempt.get("is_active")),
                "selection_reason": attempt.get("selection_reason"),
            }
        )

    # Submission_dir doubles as the "run_dir" for threat file loading
    # Because batch sub-runs store their files under runs/<id>/submission/
    detail_run_info = submission_run_info
    detail_report = report
    return render_template(
        "marking/run_detail.html",
        run=detail_run_info,
        run_id=run_id,
        report=detail_report,
        marks_released=marks_released,
        detail_view=_build_submission_detail_view(detail_run_info, detail_report),
        threat_file_contents=load_threat_file_contents(
            (detail_report or {}).get("findings", []), submission_dir
        ),
        attempt=attempt,
        attempt_history=attempt_history,
        attempt_summary=attempt_summary,
        batch_submission_id=submission_id,  # Flag to show back button
        back_url=back_url,
    )

# Render the batch submission report view.
@batch_bp.route("/batch/<run_id>/submissions/<submission_id>/report.json")
@login_required
# Show the raw report for one batch submission.
def batch_submission_report(run_id: str, submission_id: str):
    run_dir, run_info = load_run(run_id)
    if run_dir is None:
        return "Run not found", 404
    report_path = (run_dir / "runs" / submission_id / "report.json").resolve()
    try:
        report_path.relative_to(run_dir.resolve())
    except Exception:
        return "Not allowed", 403
    if not report_path.exists():
        return "Report not found", 404
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return "Report not found", 404
    report_meta = report.get("metadata", {}).get("submission_metadata", {})
    real_student_id = str(report_meta.get("student_id") or "")
    user = get_current_user()
    if user and user["role"] == "student" and real_student_id and real_student_id != user["userID"]:
        flash("You do not have access to this submission.", "error")
        return redirect(url_for("student.dashboard"))
    profile = run_info.get("profile", "")
    dl_name = f"report_{submission_id}_{profile}_{run_id}.json"
    return send_file(report_path, as_attachment=True, download_name=dl_name)

# Build exported content for a batch submission report.
def _export_report_content(
    report: dict, run_id: str, fmt: str
) -> tuple:
    """Build an ExportReport from a raw report dict and render the requested format."""
    er = build_export_report(report, run_id=run_id)
    validate_export_report(er)
    if fmt == "json":
        return _export_json(er), "application/json", "json"
    if fmt == "txt":
        return export_txt(er), "text/plain", "txt"
    if fmt == "csv":
        return export_csv_zip(er), "application/zip", "zip"
    if fmt == "pdf":
        return _export_pdf(er), "application/pdf", "pdf"
    raise ValueError(f"Unknown export format: {fmt}")

@batch_bp.route("/batch/<run_id>/submissions/<submission_id>/export/<format>")
@login_required
# Export one batch submission report.
def batch_submission_export(run_id: str, submission_id: str, format: str):
    """Export batch submission in various formats (csv, txt, pdf, json)."""
    if format not in ("csv", "txt", "pdf", "json"):
        return "Invalid format", 400

    run_dir, run_info = load_run(run_id)
    if run_dir is None:
        return "Run not found", 404
    report_path = (run_dir / "runs" / submission_id / "report.json").resolve()
    try:
        report_path.relative_to(run_dir.resolve())
    except Exception:
        return "Not allowed", 403
    if not report_path.exists():
        return "Report not found", 404

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return "Report not found", 404

    report_meta = report.get("metadata", {}).get("submission_metadata", {})
    real_student_id = str(report_meta.get("student_id") or "")
    user = get_current_user()
    if user and user["role"] == "student" and real_student_id and real_student_id != user["userID"]:
        flash("You do not have access to this submission.", "error")
        return redirect(url_for("student.dashboard"))

    profile = run_info.get("profile", "")
    base_name = f"report_{submission_id}_{profile}_{run_id}"

    try:
        content, mimetype, ext = _export_report_content(report, run_id=run_id, fmt=format)
    except ValueError as exc:
        current_app.logger.warning("Export failed for batch submission %s: %s", submission_id, exc)
        return "Report data insufficient for export", 422
    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'},
    )

# Queue or run a rerun for one batch report row.
@batch_bp.route("/batch/<run_id>/submissions/<submission_id>/rerun", methods=["POST"])
@teacher_or_admin_required
# Queue a rerun from the batch submission view.
def batch_submission_rerun(run_id: str, submission_id: str):
    run_dir, run_info = load_run(run_id)
    if run_dir is None:
        if is_async_job_request():
            return json_error("Submission not found.", 404)
        return redirect_with_flash("runs.runs", "Rerun failed: submission not found.", "error")

    try:
        return _queue_batch_submission_rerun(
            run_dir,
            run_info,
            submission_id,
            view_url=url_for("batch.batch_submission_view", run_id=run_id, submission_id=submission_id),
            refresh_url=url_for("batch.batch_submission_view", run_id=run_id, submission_id=submission_id),
        )
    except Exception as exc:
        if is_async_job_request():
            return json_error(str(exc), 400)
        return redirect_with_flash(
            "batch.batch_submission_view",
            f"Rerun failed: {exc}",
            "error",
            run_id=run_id,
            submission_id=submission_id,
        )

# Write batch reports zip.
# Write the zip of batch reports for one run.
def _write_batch_reports_zip(run_dir: Path, profile: str, run_id: str) -> None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return
    batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = batch_summary.get("records", []) or []

    zip_path = run_dir / f"batch_reports_{profile}_{run_id}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(summary_path, f"{run_id}/batch_summary.json")
        for filename in ("batch_summary.csv",):
            file = run_dir / filename
            if file.is_file():
                zf.write(file, f"{run_id}/{filename}")
        readme = _build_batch_readme(run_id, profile, batch_summary)
        zf.writestr(f"{run_id}/README.txt", readme)
        evaluation_dir = run_dir / "evaluation"
        if evaluation_dir.exists():
            for file in sorted(evaluation_dir.rglob("*")):
                if file.is_file():
                    arc = f"{run_id}/evaluation/{file.relative_to(evaluation_dir).as_posix()}"
                    zf.write(file, arc)
        for rec in records:
            rpath = rec.get("report_path")
            if rpath and Path(rpath).exists():
                path = Path(rpath)
                submission_id = rec.get("id", "submission")
                arc = f"{run_id}/submissions/{submission_id}/report.json"
                zf.write(path, arc)

# Write run index batch.
# Write the run index for a batch job.
def _write_run_index_batch(run_dir: Path, run_info: dict) -> None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return
    try:
        batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return
    submissions = []
    records = batch_summary.get("records", []) or []
    for rec in records:
        entry = {
            "submission_id": rec.get("id"),
            "student_name": None,
            "student_id": rec.get("student_id"),
            "assignment_id": rec.get("assignment_id"),
            "original_filename": rec.get("original_filename"),
            "upload_timestamp": rec.get("upload_timestamp"),
            "attempt_id": rec.get("attempt_id"),
            "attempt_number": rec.get("attempt_number"),
            "source_type": rec.get("source_type"),
            "validity_status": rec.get("validity_status"),
            "is_active": rec.get("is_active"),
            "overall": rec.get("overall"),
            "components": rec.get("components") or {},
            "threat_count": rec.get("threat_count"),
            "threat_flagged": bool(rec.get("threat_flagged") or rec.get("threat_count")),
            "llm_error_flagged": bool(rec.get("llm_error_flagged")),
            "llm_error_message": rec.get("llm_error_message"),
            "llm_error_messages": list(rec.get("llm_error_messages") or []),
            "status": rec.get("status"),
            "invalid": bool(rec.get("invalid")),
            "error": rec.get("error") or rec.get("validation_error"),
        }
        rpath = rec.get("report_path")
        if rpath and Path(rpath).exists():
            try:
                rep = json.loads(Path(rpath).read_text(encoding="utf-8"))
                meta = rep.get("metadata", {}) or {}
                submission_meta = meta.get("submission_metadata") or {}
                ident = meta.get("student_identity", {}) or {}
                scores = rep.get("scores", {}) or {}
                findings = rep.get("findings", []) or []
                entry["student_name"] = ident.get("name_normalized") or ident.get("name_raw")
                entry["student_id"] = submission_meta.get("student_id") or ident.get("student_id") or entry["student_id"]
                entry["assignment_id"] = submission_meta.get("assignment_id") or entry["assignment_id"]
                entry["original_filename"] = submission_meta.get("original_filename") or meta.get("original_filename") or entry["original_filename"]
                entry["upload_timestamp"] = submission_meta.get("timestamp") or entry["upload_timestamp"]
                if scores.get("overall") is not None:
                    entry["overall"] = scores.get("overall")
                by_component = scores.get("by_component") or {}
                if isinstance(by_component, dict):
                    entry["components"] = {
                        component: (component_scores or {}).get("score")
                        for component, component_scores in by_component.items()
                    }
                review_flags = extract_review_flags_from_report(rep)
                entry["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                if review_flags.get("threat_count"):
                    entry["threat_count"] = int(review_flags.get("threat_count") or 0)
                entry["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                entry["llm_error_message"] = review_flags.get("llm_error_message")
                entry["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
                if entry["llm_error_flagged"] and str(entry.get("status") or "").strip().lower() in {"", "ok", "completed", "complete", "success", "succeeded"}:
                    entry["status"] = "llm_error"
            except Exception:
                pass
        submissions.append(entry)
    index = {
        "run_id": run_info.get("id"),
        "mode": run_info.get("mode"),
        "profile": run_info.get("profile"),
        "created_at": run_info.get("created_at"),
        "overall": None,
        "status": "ok",
        "submissions": submissions,
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
