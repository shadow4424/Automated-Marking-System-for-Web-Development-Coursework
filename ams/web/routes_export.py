from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Mapping

from flask import Blueprint, Response, current_app, flash, redirect, send_file, url_for

from ams.io.export_report import (
    build_export_report,
    export_csv_zip,
    export_json as _export_json,
    export_pdf as _export_pdf,
    export_txt,
    validate_export_report,
)
from ams.io.web_storage import allowed_download, find_run_by_id, get_runs_root, load_run_info
from ams.web.auth import get_current_user, login_required

export_bp = Blueprint("export", __name__)
ALLOWED_DOWNLOADS = {
    "report.json",
    "summary.txt",
    "batch_summary.json",
    "batch_summary.csv",
    "batch_reports.zip",
}


def _export_report_content(report: dict, run_id: str, fmt: str) -> tuple:
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


@export_bp.route("/run/<run_id>/export/<format>")
@login_required
def individual_submission_export(run_id: str, format: str):
    if format not in ("csv", "txt", "pdf", "json"):
        return "Invalid format", 400

    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return "Run not found", 404
    report_path = run_dir / "report.json"
    if not report_path.exists():
        return "Report not found", 404

    run_info = load_run_info(run_dir) or {}
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
    base_name = f"report_{profile}_{run_id}"

    try:
        content, mimetype, ext = _export_report_content(report, run_id=run_id, fmt=format)
    except ValueError as exc:
        current_app.logger.warning("Export failed for run %s: %s", run_id, exc)
        return "Report data insufficient for export", 422
    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{base_name}.{ext}"'},
    )


@export_bp.route("/run/<run_id>/bundle")
@login_required
def download_bundle(run_id: str):
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return "Run not found", 404

    run_info = load_run_info(run_dir) or {}
    profile = run_info.get("profile", "")
    mode = run_info.get("mode", "mark")
    artifact_image_exts: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
    zip_buffer = BytesIO()

    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            top_level_files = ["report.json", "summary.txt"]
            if mode == "batch":
                top_level_files.extend(["batch_summary.json", "batch_summary.csv"])

            for filename in top_level_files:
                file_path = run_dir / filename
                if file_path.is_file():
                    try:
                        zf.write(file_path, arcname=filename)
                    except Exception:
                        pass

            submission_dir = run_dir / "submission"
            if submission_dir.is_dir():
                for file_path in submission_dir.rglob("*"):
                    if file_path.is_file():
                        try:
                            zf.write(file_path, arcname=file_path.relative_to(run_dir))
                        except Exception:
                            pass

            artifacts_dir = run_dir / "artifacts"
            if artifacts_dir.is_dir():
                for file_path in artifacts_dir.rglob("*"):
                    if file_path.is_file() and file_path.suffix.lower() in artifact_image_exts:
                        try:
                            zf.write(file_path, arcname=file_path.relative_to(run_dir))
                        except Exception:
                            pass

        zip_buffer.seek(0)
        dl_name = f"run_{profile}_{run_id}.zip"
        return send_file(
            zip_buffer,
            mimetype="application/zip",
            as_attachment=True,
            download_name=dl_name,
        )
    except Exception as exc:
        current_app.logger.error("Error creating bundle for run %s: %s", run_id, exc)
        return "Error creating bundle", 500


@export_bp.route("/download/<run_id>/<filename>")
@login_required
def download(run_id: str, filename: str):
    if not allowed_download(filename, allowed=ALLOWED_DOWNLOADS):
        return "Not allowed", 403
    runs_root = get_runs_root(current_app)
    run_dir = find_run_by_id(runs_root, run_id)
    if run_dir is None:
        return "Run not found", 404

    target = _resolve_download_path(run_dir, filename)
    try:
        target.resolve().relative_to(run_dir.resolve())
    except Exception:
        return "Not allowed", 403
    if not target.exists() or not target.is_file():
        return "File not found", 404

    run_info = load_run_info(run_dir) or {}
    profile = run_info.get("profile", "")
    dl_name = filename
    if filename.startswith("report"):
        dl_name = f"report_{profile}_{run_id}.json"
    elif filename.startswith("summary"):
        dl_name = f"summary_{profile}_{run_id}.txt"
    elif filename.startswith("batch_summary"):
        suffix = ".csv" if filename.endswith(".csv") else ".json"
        dl_name = f"batch_summary_{profile}_{run_id}{suffix}"
    elif filename.startswith("batch_reports"):
        dl_name = f"batch_reports_{profile}_{run_id}.zip"
    return send_file(target, as_attachment=True, download_name=dl_name)


def _resolve_download_path(run_dir: Path, filename: str) -> Path:
    candidate = run_dir / filename
    if candidate.exists():
        return candidate.resolve()

    evaluation_dir = run_dir / "evaluation"
    candidate = evaluation_dir / filename
    if candidate.exists():
        return candidate.resolve()

    base_name = filename.rsplit(".", 1)[0]
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    for file_path in run_dir.glob(f"{base_name}*.{ext}"):
        if file_path.is_file():
            return file_path.resolve()
    return (run_dir / filename).resolve()


def _build_batch_readme(run_id: str, profile: str, batch_summary: Mapping[str, object]) -> str:
    lines = [
        "Automated Marking System - Batch Reports",
        "",
        f"Run ID: {run_id}",
        f"Profile: {profile}",
        "",
        "Contents:",
        f"- {run_id}/batch_summary.json",
        f"- {run_id}/batch_summary.csv",
        f"- {run_id}/evaluation/ (if present)",
        f"- {run_id}/submissions/<submission_id>/report.json",
    ]
    return "\n".join(lines) + "\n"
