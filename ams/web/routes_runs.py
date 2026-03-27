from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from ams.core.attempts import (
    filter_attempts_for_root,
    get_attempt_by_run_reference,
    get_student_assignment_summary,
    list_attempts,
    sync_attempts_from_storage,
)
from ams.core.config import ScoringMode
from ams.core.db import get_assignment
from ams.core.job_manager import job_manager
from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import (
    extract_review_flags_from_report,
    find_run_by_id,
    find_submission_root,
    get_runs_root,
    list_runs,
    load_run_info,
    safe_extract_zip,
    save_run_info,
)
from ams.web.auth import get_current_user, login_required, teacher_or_admin_required
from ams.web.routes_batch import _load_batch_summary_records, _persist_batch_outputs
from ams.web.routes_dashboard import _flash_assignment_review_state, _user_can_access_assignment
from ams.web.routes_marking import (
    _build_submission_detail_view,
    _is_async_job_request,
    _queue_mark_submission_rerun,
    _safe_delete_within_run,
)
from ams.web.view_helpers import *

runs_bp = Blueprint("runs", __name__)
MAX_UPLOAD_MB = 25


def _match(
    run: dict[str, Any],
    *,
    mode_filter: str = "",
    profile_filter: str = "",
    query: str = "",
) -> bool:
    if mode_filter and run.get("mode") != mode_filter:
        return False
    if profile_filter and run.get("profile") != profile_filter:
        return False
    if query and query.lower() not in run.get("id", "").lower():
        subs = run.get("submissions", []) or []
        hit = False
        for sub in subs:
            for key in ("submission_id", "student_name", "student_id", "original_filename"):
                val = sub.get(key)
                if isinstance(val, str) and query.lower() in val.lower():
                    hit = True
                    break
            if hit:
                break
        if not hit:
            return False
    return True


@runs_bp.route("/runs")
@login_required
def runs():
    runs_root = get_runs_root(current_app)
    all_runs = list_runs(runs_root, only_active=False)
    mode_filter = request.args.get("mode") or ""
    profile_filter = request.args.get("profile") or ""
    query = request.args.get("q") or ""
    filtered = [
        run
        for run in all_runs
        if _match(
            run,
            mode_filter=mode_filter,
            profile_filter=profile_filter,
            query=query,
        )
    ]
    return render_template(
        "marking/runs.html",
        runs=filtered,
        mode_filter=mode_filter,
        profile_filter=profile_filter,
        query=query,
    )


@runs_bp.route("/runs/<run_id>/delete", methods=["POST"])
@teacher_or_admin_required
def delete_run(run_id: str):
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        flash("Run not found.", "error")
        return redirect(url_for("runs.runs"))
    shutil.rmtree(run_dir, ignore_errors=True)
    flash(f"Run '{run_id[:24]}...' deleted.", "success")
    return redirect(url_for("runs.runs"))


@runs_bp.route("/teacher/assignment/<assignment_id>/threats/delete", methods=["POST"])
@teacher_or_admin_required
def assignment_threat_delete(assignment_id: str):
    if not _user_can_access_assignment(assignment_id):
        flash("You do not have access to this assignment.", "error")
        return redirect(url_for("teacher_dashboard.dashboard"))

    run_id = str(request.form.get("run_id") or "").strip()
    submission_id = str(request.form.get("submission_id") or "").strip()
    if not run_id:
        flash("Threat resolution failed: missing run ID.", "error")
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        flash("Threat resolution failed: submission not found.", "error")
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

    run_info = load_run_info(run_dir) or {}
    if run_info.get("mode") == "batch":
        if not submission_id:
            flash("Threat resolution failed: missing batch submission ID.", "error")
            return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

        batch_summary = _load_batch_summary_records(run_dir)
        if batch_summary is None:
            flash("Threat resolution failed: batch summary could not be loaded.", "error")
            return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

        records = list(batch_summary.get("records", []) or [])
        target = next((record for record in records if str(record.get("id") or "") == submission_id), None)
        if target is None:
            flash("Threat resolution failed: flagged submission record not found.", "error")
            return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

        _safe_delete_within_run(run_dir, run_dir / "runs" / submission_id)
        _safe_delete_within_run(run_dir, target.get("path"))
        remaining = [record for record in records if str(record.get("id") or "") != submission_id]
        _persist_batch_outputs(run_dir, run_info, remaining)
        _flash_assignment_review_state(
            assignment_id,
            f"Flagged submission for '{target.get('student_id') or submission_id}' deleted.",
        )
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

    shutil.rmtree(run_dir, ignore_errors=True)
    _flash_assignment_review_state(
        assignment_id,
        f"Flagged submission for '{run_info.get('student_id') or run_id}' deleted.",
    )
    return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))


@runs_bp.route("/runs/<run_id>")
@login_required
def run_detail(run_id: str):
    runs_root = get_runs_root(current_app)
    sync_attempts_from_storage(runs_root)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return "Run not found", 404
    run_info = load_run_info(run_dir)

    user = get_current_user()
    if user and user["role"] == "student":
        run_student_id = run_info.get("student_id", "")
        if run_student_id != user["userID"]:
            batch_summary = run_info.get("batch_summary", [])
            if isinstance(batch_summary, Mapping):
                batch_summary = batch_summary.get("records", [])
            found = False
            if isinstance(batch_summary, list):
                for rec in batch_summary:
                    if rec.get("student_id") == user["userID"]:
                        found = True
                        break
            if not found:
                flash("You do not have access to this submission.", "error")
                return redirect(url_for("student.dashboard"))

    assignment_id = run_info.get("assignment_id", "")
    assignment = get_assignment(assignment_id) if assignment_id else None
    marks_released = assignment["marks_released"] if assignment else True

    attempt = get_attempt_by_run_reference(run_id, runs_root=runs_root)
    attempt_history = []
    attempt_summary = None
    if assignment_id and run_info.get("student_id"):
        attempt_history = filter_attempts_for_root(
            list_attempts(
                assignment_id=str(assignment_id),
                student_id=str(run_info.get("student_id") or ""),
                newest_first=True,
            ),
            runs_root,
        )
        attempt_summary = get_student_assignment_summary(
            str(assignment_id),
            str(run_info.get("student_id") or ""),
        )
    if attempt:
        run_info = dict(
            run_info,
            **{
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
            },
        )

    context = {
        "run": run_info,
        "run_id": run_id,
        "marks_released": marks_released,
        "attempt": attempt,
        "attempt_history": attempt_history,
        "attempt_summary": attempt_summary,
    }
    if run_info.get("mode") == "mark":
        report_path = run_dir / run_info.get("report", "report.json")
        run_status = str(run_info.get("status") or "").strip().lower()
        if run_status not in {"pending", "failed", "error"} and report_path.exists():
            context["report"] = _ensure_check_stats(
                json.loads(report_path.read_text(encoding="utf-8"))
            )
            review_flags = extract_review_flags_from_report(context["report"])
            context["run"] = dict(
                run_info,
                threat_flagged=bool(review_flags.get("threat_flagged")),
                threat_count=int(review_flags.get("threat_count") or 0),
                llm_error_flagged=bool(review_flags.get("llm_error_flagged")),
                llm_error_message=review_flags.get("llm_error_message"),
                llm_error_messages=list(review_flags.get("llm_error_messages") or []),
                status=(
                    "llm_error"
                    if review_flags.get("llm_error_flagged")
                    and run_status in {"", "ok", "completed", "complete", "success", "succeeded"}
                    else run_info.get("status")
                ),
            )
            context["threat_file_contents"] = _load_threat_file_contents(
                context["report"].get("findings", []), run_dir
            )
        context["detail_view"] = _build_submission_detail_view(context["run"], context.get("report"))
        return render_template("marking/run_detail.html", **context)

    assignment_id = run_info.get("assignment_id", "")
    if assignment_id:
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))
    return redirect(url_for("runs.runs"))


@runs_bp.route("/runs/<run_id>/rerun", methods=["POST"])
@teacher_or_admin_required
def run_submission_rerun(run_id: str):
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        if _is_async_job_request():
            return jsonify({"error": "Submission not found."}), 404
        flash("Rerun failed: submission not found.", "error")
        return redirect(url_for("runs.runs"))

    run_info = load_run_info(run_dir) or {}
    if run_info.get("mode") != "mark":
        assignment_id = str(run_info.get("assignment_id") or "").strip()
        if _is_async_job_request():
            return jsonify({"error": "Use the assignment submission rerun action for batch submissions."}), 400
        flash("Rerun failed: use the assignment submission rerun action for batch submissions.", "error")
        if assignment_id:
            return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))
        return redirect(url_for("runs.runs"))

    try:
        return _queue_mark_submission_rerun(
            run_dir,
            run_info,
            view_url=url_for("runs.run_detail", run_id=run_id),
            refresh_url=url_for("runs.run_detail", run_id=run_id),
        )
    except Exception as exc:
        if _is_async_job_request():
            return jsonify({"error": str(exc)}), 400
        flash(f"Rerun failed: {exc}", "error")
        return redirect(url_for("runs.run_detail", run_id=run_id))


def _run_override_job(
    *,
    run_id: str,
    run_dir: Path,
    run_info: Mapping[str, Any],
    upload_zip: Path,
    pipeline: AssessmentPipeline,
    profile: str,
    meta_dict: dict[str, Any],
) -> dict[str, str]:
    extracted = run_dir / "uploaded_extract"
    extracted.mkdir(parents=True, exist_ok=True)
    safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
    submission_root = find_submission_root(extracted)
    pipeline.run(
        submission_path=submission_root,
        workspace_path=run_dir,
        profile=profile,
        metadata=meta_dict,
        skip_threat_scan=True,
    )
    updated = dict(run_info)
    updated["threat_override"] = True
    updated["threat_override_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_run_info(run_dir, updated)
    return {"run_id": run_id}


@runs_bp.route("/runs/<run_id>/override-threat", methods=["POST"])
@teacher_or_admin_required
def override_threat(run_id: str):
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return jsonify({"error": "Run not found"}), 404

    run_info = load_run_info(run_dir)
    if not run_info:
        return jsonify({"error": "Run info not found"}), 404
    if run_info.get("mode") != "mark":
        return jsonify({"error": "Override is only supported for single-mark runs"}), 400

    original_filename = run_info.get("original_filename", "")
    upload_zip = run_dir / original_filename
    if not upload_zip.exists():
        zips = list(run_dir.glob("*.zip"))
        if not zips:
            return jsonify({"error": "Original submission ZIP not found - cannot reprocess"}), 404
        upload_zip = zips[0]

    profile = run_info.get("profile", "frontend")
    scoring_mode_str = run_info.get("scoring_mode", "static_plus_llm")
    try:
        scoring_mode = ScoringMode(scoring_mode_str)
    except ValueError:
        scoring_mode = ScoringMode("static_plus_llm")

    pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
    meta_dict = dict(run_info)
    job_id = job_manager.submit_job(
        "threat_override",
        lambda: _run_override_job(
            run_id=run_id,
            run_dir=run_dir,
            run_info=run_info,
            upload_zip=upload_zip,
            pipeline=pipeline,
            profile=profile,
            meta_dict=meta_dict,
        ),
    )
    return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202


@runs_bp.route("/runs/<run_id>/artifacts/<path:relpath>")
@login_required
def run_artifact(run_id: str, relpath: str):
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return "Run not found", 404
    allowed_roots = {"artifacts", "runs", "reports", "evaluation", "submission"}
    rel_parts = Path(relpath).parts
    if not rel_parts or rel_parts[0] not in allowed_roots:
        return "Not allowed", 403
    candidate = (run_dir / Path(relpath)).resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except Exception:
        return "Not allowed", 403
    if not candidate.exists() or not candidate.is_file():
        return "File not found", 404
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    as_download = candidate.suffix.lower() not in image_exts
    return send_file(candidate, as_attachment=as_download, download_name=candidate.name)


def _write_run_index_mark(run_dir: Path, run_info: dict, report_path: Path) -> None:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return

    meta = report.get("metadata", {}) or {}
    submission_meta = meta.get("submission_metadata") or {}
    ident = meta.get("student_identity", {}) or {}
    review_flags = extract_review_flags_from_report(report)

    sub_entry = {
        "submission_id": meta.get("submission_name"),
        "student_name": ident.get("name_normalized") or ident.get("name_raw"),
        "student_id": submission_meta.get("student_id") or ident.get("student_id") or run_info.get("student_id"),
        "assignment_id": submission_meta.get("assignment_id") or run_info.get("assignment_id"),
        "original_filename": submission_meta.get("original_filename")
        or meta.get("original_filename")
        or run_info.get("original_filename"),
        "upload_timestamp": submission_meta.get("timestamp") or run_info.get("created_at"),
        "attempt_id": submission_meta.get("attempt_id") or run_info.get("attempt_id"),
        "attempt_number": submission_meta.get("attempt_number") or run_info.get("attempt_number"),
        "source_type": submission_meta.get("source_type") or run_info.get("source_type"),
        "validity_status": submission_meta.get("validity_status") or run_info.get("validity_status"),
        "is_active": submission_meta.get("is_active")
        if submission_meta.get("is_active") is not None
        else run_info.get("is_active"),
        "threat_count": int(review_flags.get("threat_count") or 0),
        "threat_flagged": bool(review_flags.get("threat_flagged")),
        "llm_error_flagged": bool(review_flags.get("llm_error_flagged")),
        "llm_error_message": review_flags.get("llm_error_message"),
        "llm_error_messages": list(review_flags.get("llm_error_messages") or []),
    }

    index = {
        "run_id": run_info.get("id"),
        "mode": run_info.get("mode"),
        "profile": run_info.get("profile"),
        "created_at": run_info.get("created_at"),
        "overall": report.get("scores", {}).get("overall"),
        "status": "llm_error" if review_flags.get("llm_error_flagged") else "ok",
        "attempt_id": run_info.get("attempt_id"),
        "attempt_number": run_info.get("attempt_number"),
        "source_type": run_info.get("source_type"),
        "validity_status": run_info.get("validity_status"),
        "is_active": run_info.get("is_active"),
        "submissions": [sub_entry],
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
