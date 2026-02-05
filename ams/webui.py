from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ams.core.pipeline import AssessmentPipeline
from ams.core.profiles import PROFILES
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.io.web_storage import (
    allowed_download,
    create_run_dir,
    find_submission_root,
    get_runs_root,
    list_runs,
    load_metadata,
    load_run_info,
    safe_extract_zip,
    save_metadata,
    save_run_info,
    store_submission_with_metadata,
    validate_file_size,
    validate_file_type,
)
from ams.tools.batch import run_batch
from ams.analytics import build_teacher_analytics
from ams.io.submission import SubmissionProcessor

MAX_UPLOAD_MB = 25
ALLOWED_DOWNLOADS = {
    "report.json",
    "summary.txt",
    "batch_summary.json",
    "batch_summary.csv",
    "findings_frequency.csv",
    "failure_reasons_frequency.csv",
    "score_buckets.csv",
    "component_means.csv",
    "batch_analytics.json",
    "batch_analytics_",
    "component_breakdown_",
    "needs_attention_",
    "batch_reports",
    "runtime_health_",
    "score_distribution",
    "component_readiness",
    "needs_attention_top_reasons",
    "evaluation_summary",
    "evaluation_results",
}


def create_app(config: Mapping[str, object] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024  # security: limit upload size
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = "replace-this-secret"
    app.secret_key = app.config["SECRET_KEY"]
    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:
    @app.route("/")
    def home():
        return render_template("home.html")

    @app.route("/mark", methods=["GET", "POST"])
    def mark():
        if request.method == "GET":
            return render_template("mark.html", profiles=PROFILES.keys())
        
        # Get form data
        file = request.files.get("submission")
        profile = request.form.get("profile", "frontend")
        student_id = request.form.get("student_id", "").strip()
        assignment_id = request.form.get("assignment_id", "").strip()
        
        # Validate file
        if not file or not file.filename:
            flash("Please upload a .zip file.")
            return render_template("mark.html", profiles=PROFILES.keys()), 400
        
        if not validate_file_type(file.filename):
            flash("Invalid file type. Please upload a .zip file.")
            return render_template("mark.html", profiles=PROFILES.keys()), 400
        
        # Validate metadata
        valid_student, student_error = MetadataValidator.validate_student_id(student_id)
        if not valid_student:
            flash(f"Invalid Student ID: {student_error}")
            return render_template("mark.html", profiles=PROFILES.keys()), 400
        
        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            flash(f"Invalid Assignment ID: {assignment_error}")
            return render_template("mark.html", profiles=PROFILES.keys()), 400
        
        # Sanitize identifiers
        student_id = MetadataValidator.sanitize_identifier(student_id)
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        original_filename = MetadataValidator.sanitize_filename(file.filename)
        
        # Create metadata
        metadata = SubmissionMetadata(
            student_id=student_id,
            assignment_id=assignment_id,
            timestamp=datetime.now(timezone.utc),
            original_filename=original_filename,
            uploader_metadata={
                "ip_address": request.remote_addr or "unknown",
                "user_agent": request.headers.get("User-Agent", "unknown")[:200],
            },
        )
        
        runs_root = get_runs_root(app)
        
        # Save uploaded file temporarily
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            file.save(tmp_file.name)
            tmp_zip_path = Path(tmp_file.name)
        
        try:
            # Validate file size
            valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
            if not valid_size:
                flash(size_error or "File size exceeds maximum limit.")
                return render_template("mark.html", profiles=PROFILES.keys()), 400
            
            # Store submission with metadata
            run_id, run_dir = store_submission_with_metadata(
                runs_root=runs_root,
                mode="mark",
                profile=profile,
                metadata=metadata,
                zip_file=tmp_zip_path,
                versioned=True,
            )
            
            # Extract for processing
            upload_zip = run_dir / original_filename
            extracted = run_dir / "uploaded_extract"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            
            pipeline = AssessmentPipeline()
            submission_root = find_submission_root(extracted)
            
            # Pass metadata to pipeline via context
            app.logger.debug(
                "mark run extract complete",
                extra={
                    "upload_zip": str(upload_zip),
                    "extracted": str(extracted),
                    "submission_root": str(submission_root),
                    "profile": profile,
                    "student_id": student_id,
                    "assignment_id": assignment_id,
                },
            )
            
            report_path = pipeline.run(
                submission_path=submission_root,
                workspace_path=run_dir,
                profile=profile,
                metadata=metadata.to_dict(),
            )
            
            run_info = {
                "id": run_id,
                "mode": "mark",
                "profile": profile,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "report": report_path.name,
                "summary": "summary.txt",
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": original_filename,
            }
            save_run_info(run_dir, run_info)
            _write_run_index_mark(run_dir, run_info, report_path)
            return redirect(url_for("run_detail", run_id=run_id))
        finally:
            # Clean up temporary file
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass

    @app.route("/batch", methods=["GET", "POST"])
    def batch():
        if request.method == "GET":
            return render_template("batch.html", profiles=PROFILES.keys())
        
        # Get form data
        file = request.files.get("submission")
        profile = request.form.get("profile", "frontend")
        assignment_id = request.form.get("assignment_id", "").strip()
        
        # Validate file
        if not file or not file.filename:
            flash("Please upload a .zip file.")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        if not validate_file_type(file.filename):
            flash("Invalid file type. Please upload a .zip file.")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        # Validate assignment ID
        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            flash(f"Invalid Assignment ID: {assignment_error}")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        # Sanitize
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        original_filename = MetadataValidator.sanitize_filename(file.filename)
        
        runs_root = get_runs_root(app)
        
        # Save uploaded file temporarily
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            file.save(tmp_file.name)
            tmp_zip_path = Path(tmp_file.name)
        
        try:
            # Validate file size
            valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
            if not valid_size:
                flash(size_error or "File size exceeds maximum limit.")
                return render_template("batch.html", profiles=PROFILES.keys()), 400
            
            # Create batch metadata (assignment-level)
            batch_metadata = SubmissionMetadata(
                student_id="batch",  # Special identifier for batch runs
                assignment_id=assignment_id,
                timestamp=datetime.now(timezone.utc),
                original_filename=original_filename,
                uploader_metadata={
                    "ip_address": request.remote_addr or "unknown",
                    "user_agent": request.headers.get("User-Agent", "unknown")[:200],
                },
            )
            
            run_id, run_dir = create_run_dir(
                runs_root=runs_root,
                mode="batch",
                profile=profile,
                metadata=batch_metadata,
            )
            
            # Store batch zip
            upload_zip = run_dir / original_filename
            import shutil
            shutil.copy2(tmp_zip_path, upload_zip)
            
            # Save batch metadata
            save_metadata(run_dir, batch_metadata)
            
            extracted = run_dir / "batch_inputs"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            
            # Run batch with metadata context
            summary = run_batch(
                submissions_dir=extracted,
                out_root=run_dir,
                profile=profile,
                keep_individual_runs=True,
                assignment_id=assignment_id,
            )
            
            run_info = {
                "id": run_id,
                "mode": "batch",
                "profile": profile,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "summary": "batch_summary.json",
                "batch_summary": summary,
                "assignment_id": assignment_id,
                "original_filename": original_filename,
            }
            _write_batch_analytics(run_dir, profile, run_id)
            _write_batch_reports_zip(run_dir, profile, run_id)
            _write_run_index_batch(run_dir, run_info)
            save_run_info(run_dir, run_info)
            return redirect(url_for("run_detail", run_id=run_id))
        finally:
            # Clean up temporary file
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass

    @app.route("/runs")
    def runs():
        runs_root = get_runs_root(app)
        all_runs = list_runs(runs_root)
        mode_filter = request.args.get("mode") or ""
        profile_filter = request.args.get("profile") or ""
        query = request.args.get("q") or ""

        def _match(run: dict) -> bool:
            if mode_filter and run.get("mode") != mode_filter:
                return False
            if profile_filter and run.get("profile") != profile_filter:
                return False
            if query and query.lower() not in run.get("id", "").lower():
                subs = run.get("submissions", []) or []
                hit = False
                for sub in subs:
                    for key in ["submission_id", "student_name", "student_id", "original_filename"]:
                        val = sub.get(key)
                        if isinstance(val, str) and query.lower() in val.lower():
                            hit = True
                            break
                    if hit:
                        break
                if not hit:
                    return False
            return True

        filtered = [r for r in all_runs if _match(r)]
        return render_template(
            "runs.html",
            runs=filtered,
            mode_filter=mode_filter,
            profile_filter=profile_filter,
            query=query,
        )

    @app.route("/runs/<run_id>")
    def run_detail(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = runs_root / run_id
        run_info = load_run_info(run_dir)
        if run_info is None:
            return "Run not found", 404
        context = {"run": run_info, "run_id": run_id}
        if run_info.get("mode") == "mark":
            report_path = run_dir / run_info.get("report", "report.json")
            if report_path.exists():
                context["report"] = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            summary_path = run_dir / run_info.get("summary", "batch_summary.json")
            if summary_path.exists():
                context["batch"] = json.loads(summary_path.read_text(encoding="utf-8"))
            analytics_path = run_dir / "analytics" / _analytics_filenames(run_id)["json"]
            if analytics_path.exists():
                context["analytics"] = json.loads(analytics_path.read_text(encoding="utf-8"))
        return render_template("run_detail.html", **context)

    @app.route("/batch/<run_id>/analytics")
    def batch_analytics(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = runs_root / run_id
        run_info = load_run_info(run_dir)
        if run_info is None:
            return "Run not found", 404
        analytics_info = _ensure_batch_analytics(run_dir, run_id)
        if not analytics_info:
            return "Analytics not found", 404
        analytics_path = analytics_info["paths"]["json"]
        analytics = json.loads(analytics_path.read_text(encoding="utf-8"))
        batch_summary_path = run_dir / run_info.get("summary", "batch_summary.json")
        batch_summary = json.loads(batch_summary_path.read_text(encoding="utf-8")) if batch_summary_path.exists() else {}
        return render_template(
            "batch_analytics.html",
            run=run_info,
            analytics=analytics,
            batch_summary=batch_summary,
            run_id=run_id,
        )

    @app.route("/runs/<run_id>/artifacts/<path:relpath>")
    def run_artifact(run_id: str, relpath: str):
        runs_root = get_runs_root(app)
        run_dir = runs_root / run_id
        if not run_dir.exists():
            return "Run not found", 404
        allowed_roots = {"artifacts", "analytics", "runs", "reports", "evaluation"}
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
        return send_file(candidate, as_attachment=True, download_name=candidate.name)

    @app.route("/batch/<run_id>/submissions/<submission_id>/report.json")
    def batch_submission_report(run_id: str, submission_id: str):
        runs_root = get_runs_root(app)
        run_dir = runs_root / run_id
        report_path = (run_dir / "runs" / submission_id / "report.json").resolve()
        try:
            report_path.relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        if not report_path.exists():
            return "Report not found", 404
        run_info = load_run_info(run_dir) or {}
        profile = run_info.get("profile", "")
        dl_name = f"report_{submission_id}_{profile}_{run_id}.json"
        return send_file(report_path, as_attachment=True, download_name=dl_name)

    @app.route("/download/<run_id>/<filename>")
    def download(run_id: str, filename: str):
        if not allowed_download(filename, allowed=ALLOWED_DOWNLOADS):
            return "Not allowed", 403
        runs_root = get_runs_root(app)
        if filename.startswith(("batch_analytics_", "component_breakdown_", "needs_attention_")):
            _ensure_batch_analytics(runs_root / run_id, run_id)
        target = _resolve_download_path(runs_root, run_id, filename)
        try:
            target.relative_to(runs_root.resolve())
        except Exception:
            return "Not allowed", 403
        if not target.exists() or not target.is_file():
            return "File not found", 404
        run_info = load_run_info(runs_root / run_id) or {}
        profile = run_info.get("profile", "")
        mode = run_info.get("mode", "batch")
        dl_name = filename
        if filename.startswith("report"):
            dl_name = f"report_{profile}_{run_id}.json"
        elif filename.startswith("summary"):
            dl_name = f"summary_{profile}_{run_id}.txt"
        elif filename.startswith("batch_summary"):
            suffix = ".csv" if filename.endswith(".csv") else ".json"
            dl_name = f"batch_summary_{profile}_{run_id}{suffix}"
        elif filename.startswith("batch_analytics"):
            suffix = ".csv" if filename.endswith(".csv") else ".json"
            dl_name = f"batch_analytics_{profile}_{run_id}{suffix}"
        elif filename.startswith("component_means") or filename.startswith("component_breakdown"):
            dl_name = f"component_breakdown_{profile}_{run_id}.csv"
        elif filename.startswith("needs_attention"):
            dl_name = f"needs_attention_{profile}_{run_id}.csv"
        elif filename.startswith("batch_reports"):
            dl_name = f"batch_reports_{profile}_{run_id}.zip"
        elif filename.startswith("findings_frequency"):
            dl_name = f"findings_frequency_{profile}_{run_id}.csv"
        elif filename.startswith("failure_reasons_frequency"):
            dl_name = f"failure_reasons_{profile}_{run_id}.csv"
        elif filename.startswith("score_buckets"):
            dl_name = f"score_buckets_{profile}_{run_id}.csv"
        elif filename.startswith("component_means"):
            dl_name = f"component_means_{profile}_{run_id}.csv"
        return send_file(target, as_attachment=True, download_name=dl_name)


app = create_app()


def _analytics_filenames(run_id: str) -> dict[str, str]:
    return {
        "json": f"batch_analytics_{run_id}.json",
        "csv": f"batch_analytics_{run_id}.csv",
        "component": f"component_breakdown_{run_id}.csv",
        "needs": f"needs_attention_{run_id}.csv",
        "runtime": f"runtime_health_{run_id}.csv",
    }


def _ensure_batch_analytics(run_dir: Path, run_id: str, force: bool = False) -> dict | None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return None
    analytics_dir = run_dir / "analytics"
    analytics_dir.mkdir(parents=True, exist_ok=True)
    names = _analytics_filenames(run_id)
    analytics_json = analytics_dir / names["json"]
    analytics_csv = analytics_dir / names["csv"]
    comp_csv = analytics_dir / names["component"]
    needs_csv = analytics_dir / names["needs"]
    if not force and analytics_json.exists() and analytics_csv.exists() and comp_csv.exists() and needs_csv.exists():
        return {
            "analytics": json.loads(analytics_json.read_text(encoding="utf-8")),
            "paths": {"json": analytics_json, "csv": analytics_csv, "component": comp_csv, "needs": needs_csv},
        }
    batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Enrich records with findings/environment for analytics v2
    records = batch_summary.get("records", []) or []
    for rec in records:
        rpath = rec.get("report_path")
        if rpath and Path(rpath).exists():
            try:
                rep = json.loads(Path(rpath).read_text(encoding="utf-8"))
                rec["findings"] = rep.get("findings", []) or []
                rec["environment"] = rep.get("environment", {}) or {}
            except Exception:
                rec["findings"] = []
        else:
            rec["findings"] = []
    batch_summary["records"] = records

    analytics = build_teacher_analytics(batch_summary)
    analytics_json.write_text(json.dumps(analytics, indent=2), encoding="utf-8")

    records = batch_summary.get("records", []) or []
    with analytics_csv.open("w", newline="", encoding="utf-8") as fh:
        fh.write("submission_id,overall,html,css,js,php,sql,status,primary_reason\n")
        for rec in sorted(records, key=lambda r: r.get("id", "")):
            comps = rec.get("components", {}) or {}
            fh.write(
                "{id},{overall},{html},{css},{js},{php},{sql},{status},{reason}\n".format(
                    id=rec.get("id", ""),
                    overall=rec.get("overall", ""),
                    html=comps.get("html", ""),
                    css=comps.get("css", ""),
                    js=comps.get("js", ""),
                    php=comps.get("php", ""),
                    sql=comps.get("sql", ""),
                    status=rec.get("status", ""),
                    reason=rec.get("primary_reason", ""),
                )
            )

    comps = analytics.get("components", {}) or {}
    with comp_csv.open("w", newline="", encoding="utf-8") as fh:
        fh.write("component,average,pct_zero,pct_full\n")
        for comp_name in ["html", "css", "js", "php", "sql"]:
            comp = comps.get(comp_name, {}) or {}
            fh.write(
                "{name},{avg},{zero},{full}\n".format(
                    name=comp_name,
                    avg=comp.get("average", ""),
                    zero=comp.get("pct_zero", ""),
                    full=comp.get("pct_full", ""),
                )
            )

    needs = analytics.get("needs_attention", []) or []
    with needs_csv.open("w", newline="", encoding="utf-8") as fh:
        fh.write("submission_id,overall,status,flags,reason\n")
        for entry in needs:
            fh.write(
                "{id},{overall},{status},{flags},{reason}\n".format(
                    id=entry.get("submission_id", ""),
                    overall=entry.get("overall", ""),
                    status=entry.get("status", ""),
                    flags=";".join(entry.get("flags", [])),
                    reason=entry.get("reason", ""),
                )
            )

    runtime_csv = analytics_dir / names["runtime"]
    runtime = analytics.get("runtime_health", {}) or {}
    with runtime_csv.open("w", newline="", encoding="utf-8") as fh:
        fh.write("behavioural_pass,behavioural_fail,behavioural_timeout,behavioural_skipped,browser_pass,browser_fail,browser_timeout,console_error_pct\n")
        fh.write(
            "{bp},{bf},{bt},{bs},{rp},{rf},{rt},{cpct}\n".format(
                bp=runtime.get("behavioural", {}).get("pass", 0),
                bf=runtime.get("behavioural", {}).get("fail", 0),
                bt=runtime.get("behavioural", {}).get("timeout", 0),
                bs=runtime.get("behavioural", {}).get("skipped", 0),
                rp=runtime.get("browser", {}).get("pass", 0),
                rf=runtime.get("browser", {}).get("fail", 0),
                rt=runtime.get("browser", {}).get("timeout", 0),
                cpct=runtime.get("console_error_pct", 0),
            )
        )

    return {
        "analytics": analytics,
        "paths": {"json": analytics_json, "csv": analytics_csv, "component": comp_csv, "needs": needs_csv, "runtime": runtime_csv},
    }


def _resolve_download_path(runs_root: Path, run_id: str, filename: str) -> Path:
    run_dir = runs_root / run_id
    candidate = run_dir / filename
    if candidate.exists():
        return candidate.resolve()
    analytics_dir = run_dir / "analytics"
    candidate = analytics_dir / filename
    if candidate.exists():
        return candidate.resolve()
    evaluation_dir = run_dir / "evaluation"
    return (evaluation_dir / filename).resolve()


def _build_batch_readme(run_id: str, profile: str, batch_summary: Mapping[str, object]) -> str:
    summary = batch_summary.get("summary", {}) or {}
    total = summary.get("total_submissions", "")
    succeeded = summary.get("succeeded", "")
    failed = summary.get("failed", "")
    lines = [
        "Automated Marking System - Batch Reports",
        "",
        f"Run ID: {run_id}",
        f"Profile: {profile}",
        f"Total submissions: {total}",
        f"Succeeded: {succeeded}",
        f"Failed: {failed}",
        "",
        "Contents:",
        f"- {run_id}/batch_summary.json",
        f"- {run_id}/analytics/",
        f"- {run_id}/evaluation/ (if present)",
        f"- {run_id}/submissions/<submission_id>/report.json",
    ]
    return "\n".join(lines) + "\n"


def _write_batch_analytics(run_dir: Path, profile: str, run_id: str) -> None:
    _ensure_batch_analytics(run_dir, run_id, force=True)


def _write_batch_reports_zip(run_dir: Path, profile: str, run_id: str) -> None:
    summary_path = run_dir / "batch_summary.json"
    if not summary_path.exists():
        return
    batch_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = batch_summary.get("records", []) or []
    _ensure_batch_analytics(run_dir, run_id)
    zip_path = run_dir / f"batch_reports_{profile}_{run_id}.zip"
    import zipfile

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(summary_path, f"{run_id}/batch_summary.json")
        analytics_dir = run_dir / "analytics"
        if analytics_dir.exists():
            for file in sorted(analytics_dir.rglob("*")):
                if file.is_file():
                    arc = f"{run_id}/analytics/{file.relative_to(analytics_dir).as_posix()}"
                    zf.write(file, arc)
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


def _write_run_index_mark(run_dir: Path, run_info: dict, report_path: Path) -> None:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return
    
    # Load metadata from file if available
    metadata = load_metadata(run_dir)
    
    meta = report.get("metadata", {}) or {}
    submission_meta = meta.get("submission_metadata") or {}
    ident = meta.get("student_identity", {}) or {}
    
    sub_entry = {
        "submission_id": meta.get("submission_name"),
        "student_name": ident.get("name_normalized") or ident.get("name_raw"),
        "student_id": submission_meta.get("student_id") or ident.get("student_id") or run_info.get("student_id"),
        "assignment_id": submission_meta.get("assignment_id") or run_info.get("assignment_id"),
        "original_filename": submission_meta.get("original_filename") or meta.get("original_filename") or run_info.get("original_filename"),
        "upload_timestamp": submission_meta.get("timestamp") or run_info.get("created_at"),
    }
    
    index = {
        "run_id": run_info.get("id"),
        "mode": run_info.get("mode"),
        "profile": run_info.get("profile"),
        "created_at": run_info.get("created_at"),
        "overall": report.get("scores", {}).get("overall"),
        "status": "ok",
        "submissions": [sub_entry],
    }
    (run_dir / "run_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")


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
        }
        rpath = rec.get("report_path")
        if rpath and Path(rpath).exists():
            try:
                rep = json.loads(Path(rpath).read_text(encoding="utf-8"))
                meta = rep.get("metadata", {}) or {}
                submission_meta = meta.get("submission_metadata") or {}
                ident = meta.get("student_identity", {}) or {}
                entry["student_name"] = ident.get("name_normalized") or ident.get("name_raw")
                entry["student_id"] = submission_meta.get("student_id") or ident.get("student_id") or entry["student_id"]
                entry["assignment_id"] = submission_meta.get("assignment_id") or entry["assignment_id"]
                entry["original_filename"] = submission_meta.get("original_filename") or meta.get("original_filename") or entry["original_filename"]
                entry["upload_timestamp"] = submission_meta.get("timestamp") or entry["upload_timestamp"]
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
