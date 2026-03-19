"""AMS Web Interface — primary Flask application for the marking system.

Provides routes for:
- Single-submission marking (``/mark``)
- Batch processing multiple submissions (``/batch``)
- Run history and report viewing (``/runs``, ``/runs/<run_id>``)
- Batch analytics dashboards (``/batch/<run_id>/analytics``)
- Artifact and report downloads

Start locally with: ``python -m flask --app ams.webui run --debug``
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Mapping

import requests as _requests
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ams.core.db import (
    init_db,
    get_assignment,
    list_assignments,
    list_assignments_for_student,
    PREVIEW_STUDENT_ID,
)
from ams.core.pipeline import AssessmentPipeline
from ams.core.config import (
    ScoringMode,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GITHUB_OAUTH_CALLBACK,
)
from ams.core.profiles import PROFILES
from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.io.web_storage import (
    allowed_download,
    create_run_dir,
    find_run_by_id,
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
from ams.web.helpers import validate_is_zipfile
from ams.tools.batch import run_batch
from ams.analytics import build_teacher_analytics
from ams.core.job_manager import job_manager

logger = logging.getLogger(__name__)

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


# --- Jinja helpers -----------------------------------------------------------

import re as _re

_PATH_RE = _re.compile(
    r"(?:[A-Za-z]:)?[\\/](?:[\w .~@#$%&()\-]+[\\/]){2,}[\w .~@#$%&()\-]+\.\w{1,10}$"
)


def _clean_path(value: object) -> str:
    """Jinja filter: shorten absolute file paths to ``submission/file.ext``.

    E.g. ``E:\\Users\\…\\submission\\index.php`` → ``submission/index.php``
    """
    s = str(value).replace("\\", "/")
    # Try to cut at a well-known folder boundary
    for marker in ("submission/", "artifacts/", "test_coursework/"):
        idx = s.find(marker)
        if idx != -1:
            return s[idx:]
    # Fallback: if it looks like a path, show only the last two segments
    if _PATH_RE.match(s):
        parts = s.rsplit("/", 2)
        return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
    return s


def _render_evidence_value(val: object) -> str:
    """Return an HTML-safe string for a single evidence value.

    * Paths are shortened via ``_clean_path``
    * Booleans become ✓ / ✗
    * Lists become comma-separated
    * Everything else is stringified
    """
    from markupsafe import Markup, escape

    if isinstance(val, bool):
        return Markup('<span class="text-success">✓</span>') if val else Markup('<span class="text-danger">✗</span>')
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, list):
        if not val:
            return "—"
        items = ", ".join(escape(_clean_path(v)) for v in val)
        return Markup(items)
    s = str(val)
    if _PATH_RE.match(s.replace("\\", "/")):
        return _clean_path(s)
    return s


def _ensure_check_stats(report: dict) -> dict:
    """Enrich a loaded report dict with aggregated check stats if missing.

    Backward-compatible: reports generated before the aggregation layer was
    added will be enriched on load so the template always has the data.
    """
    if "checks" not in report or "check_stats" not in report:
        findings = report.get("findings", [])
        checks, diagnostics = aggregate_findings_to_checks(findings)
        report["checks"] = [c.to_dict() for c in checks]
        report["check_stats"] = compute_check_stats(checks)
        report["diagnostics"] = diagnostics
    return report


def _load_threat_file_contents(findings: list, run_dir: Path) -> dict:
    """Load source file contents for threat-flagged findings.

    For each THREAT finding that references a file inside the submission
    directory, reads the file and records which line numbers triggered alerts.

    Returns a ``dict`` keyed by the file's path relative to ``submission/``:

    .. code-block:: python

        {
            "index.php": {
                "lines": ["<?php", "system($_GET['cmd']);", ...],
                "threat_lines": [2, ...],
            },
            ...
        }

    Files larger than 200 KB are skipped.  All paths are validated to stay
    within ``run_dir/submission/`` — no traversal is possible.
    """
    MAX_FILE_BYTES = 200 * 1024  # 200 KB per-file cap
    submission_dir = (run_dir / "submission").resolve()

    threat_findings = [
        f for f in findings
        if f.get("severity") == "THREAT"
        and isinstance(f.get("evidence"), dict)
        and f["evidence"].get("file")
    ]
    if not threat_findings:
        return {}

    def _to_rel(raw: str) -> str:
        """Convert an absolute or relative file reference to a path relative to submission/."""
        s = str(raw).replace("\\", "/")
        if "submission/" in s:
            idx = s.rfind("submission/")
            return s[idx + len("submission/"):]
        return Path(raw).name

    file_data: dict[str, dict] = {}

    # First pass — load unique files
    for finding in threat_findings:
        file_rel = _to_rel(finding["evidence"]["file"])
        if not file_rel or file_rel in file_data:
            continue
        candidate = (submission_dir / file_rel).resolve()
        try:
            candidate.relative_to(submission_dir)
        except ValueError:
            continue  # path traversal attempt — skip
        if not candidate.is_file():
            continue
        if candidate.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
            file_data[file_rel] = {"lines": content.splitlines(), "threat_lines": []}
        except Exception:
            pass

    # Second pass — mark threat lines
    for finding in threat_findings:
        file_rel = _to_rel(finding["evidence"]["file"])
        if file_rel not in file_data:
            continue
        try:
            ln = int(finding["evidence"]["line"])
            if ln not in file_data[file_rel]["threat_lines"]:
                file_data[file_rel]["threat_lines"].append(ln)
        except (TypeError, ValueError, KeyError):
            pass

    for key in file_data:
        file_data[key]["threat_lines"].sort()

    return file_data


def create_app(config: Mapping[str, object] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024  # security: limit upload size
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        import secrets
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.secret_key = app.config["SECRET_KEY"]
    
    # Cleanup old workspaces on startup (prevents disk bloat)
    try:
        from ams.io.workspace import cleanup_old_runs
        cleanup_old_runs()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Workspace cleanup failed: {e}")
    
    # Register Jinja filters
    app.jinja_env.filters["clean_path"] = _clean_path
    app.jinja_env.globals["render_evidence_value"] = _render_evidence_value

    # ── Sandbox status context processor ─────────────────────────────
    @app.context_processor
    def inject_sandbox_status():
        from ams.sandbox.config import get_sandbox_status
        ctx = {"sandbox_status": get_sandbox_status()}
        try:
            from ams.sandbox.forensics import list_retained_containers
            ctx["threat_containers"] = list_retained_containers()
        except Exception:
            ctx["threat_containers"] = []
        return ctx

    # ── RBAC: initialise database & register blueprints ───────────────
    init_db()

    from ams.web.auth import auth_bp, inject_user_context
    from ams.web.routes_admin import admin_bp
    from ams.web.routes_teacher import teacher_bp
    from ams.web.routes_student import student_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)

    app.context_processor(inject_user_context)

    @app.context_processor
    def inject_released_aids():
        """Expose released assignment IDs so the job widget can gate 'View' links."""
        if session.get("user_role") == "student" and session.get("user_id"):
            aids = [
                a["assignmentID"]
                for a in list_assignments_for_student(session["user_id"])
                if a.get("marks_released")
            ]
            return {"released_assignment_ids": aids}
        return {"released_assignment_ids": []}

    _register_routes(app)
    return app


def _register_routes(app: Flask) -> None:
    from ams.web.auth import login_required, teacher_or_admin_required, get_current_user

    @app.route("/")
    def home():
        if "user_id" in session and session.get("2fa_verified"):
            user = get_current_user()
            if user:
                if user["role"] == "admin":
                    return redirect(url_for("admin.dashboard"))
                elif user["role"] == "teacher":
                    return redirect(url_for("teacher.dashboard"))
                else:
                    return redirect(url_for("student.dashboard"))
        return redirect(url_for("auth.login"))

    @app.route("/mark", methods=["GET", "POST"])
    @login_required
    def mark():
        if request.method == "GET":
            github_connected = bool(session.get("github_token"))
            github_user = session.get("github_user", "")
            user_role = session.get("user_role", "")
            view_as_role = session.get("view_as_role")
            effective_role = view_as_role or user_role
            user_id = session.get("user_id", "")
            student_assignments = []
            is_preview = False

            # For students OR admins viewing as student, load available assignments
            if effective_role == "student":
                now = datetime.now().strftime("%Y-%m-%dT%H:%M")
                if user_role == "student":
                    # Real student - show their assigned assignments
                    student_assignments = [
                        a for a in list_assignments_for_student(user_id)
                        if not a.get("due_date") or a["due_date"] >= now
                    ]
                else:
                    # Admin viewing as student - use preview student, show all assignments
                    is_preview = True
                    student_assignments = [
                        a for a in list_assignments()
                        if not a.get("due_date") or a["due_date"] >= now
                    ]

            # For preview mode, use the dedicated preview student ID
            effective_student_id = PREVIEW_STUDENT_ID if is_preview else user_id

            return render_template(
                "mark.html",
                profiles=PROFILES.keys(),
                github_connected=github_connected,
                github_user=github_user,
                user_role=user_role,
                effective_role=effective_role,
                user_id=effective_student_id,
                student_assignments=student_assignments,
                is_preview=is_preview,
            )

        # ── Sandbox enforcement ──────────────────────────────────────
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
            return render_template("mark.html", profiles=PROFILES.keys()), 503

        # GitHub state (needed for error paths and template rendering)
        github_connected = bool(session.get("github_token"))
        github_user = session.get("github_user", "")

        # Get form data
        file = request.files.get("submission")
        github_repo = request.form.get("github_repo", "").strip()
        github_branch = request.form.get("github_branch", "").strip()
        profile = request.form.get("profile", "frontend")
        student_id = request.form.get("student_id", "").strip()
        assignment_id = request.form.get("assignment_id", "").strip()
        scoring_mode_str = request.form.get("scoring_mode", "static_plus_llm").strip()

        # ── Determine submission source (ZIP upload vs GitHub) ──
        using_github = bool(github_repo)
        tmp_zip_path: Path | None = None

        if using_github:
            # ------ GitHub submission path ------
            github_token = session.get("github_token")
            if not github_token:
                flash("Please link your GitHub account first.", "error")
                return render_template("mark.html", profiles=PROFILES.keys(), github_connected=False, github_user=github_user), 400

            # Validate repo format (owner/repo)
            if "/" not in github_repo or github_repo.count("/") != 1:
                flash("Invalid GitHub repository format. Use owner/repo.", "error")
                return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400

            try:
                # Branch-specific zipball (Gradescope-style)
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
                return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400

            # Save to a temporary ZIP file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                for chunk in gh_resp.iter_content(chunk_size=8192):
                    tmp_file.write(chunk)
                tmp_zip_path = Path(tmp_file.name)

            branch_suffix = f"_{github_branch}" if github_branch else ""
            original_filename = f"{github_repo.replace('/', '_')}{branch_suffix}.zip"

        else:
            # ------ ZIP upload path ------
            if not file or not file.filename:
                flash("Please upload a .zip file or select a GitHub repository.", "error")
                return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400

            if not validate_file_type(file.filename):
                flash("Invalid file type. Please upload a .zip file.", "error")
                return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                file.save(tmp_file.name)
                tmp_zip_path = Path(tmp_file.name)

            original_filename = MetadataValidator.sanitize_filename(file.filename)

        # ── Strict ZIP content validation (magic-byte check) ─────
        if not validate_is_zipfile(tmp_zip_path):
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass
            flash("The uploaded file is not a valid ZIP archive.", "error")
            return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400
        
        # Validate and convert scoring mode
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            flash(f"Invalid scoring mode: {scoring_mode_str}", "error")
            return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400
        
        # Validate metadata
        valid_student, student_error = MetadataValidator.validate_student_id(student_id)
        if not valid_student:
            flash(f"Invalid Student ID: {student_error}", "error")
            return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400
        
        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            flash(f"Invalid Assignment ID: {assignment_error}", "error")
            return render_template("mark.html", profiles=PROFILES.keys(), github_connected=github_connected, github_user=github_user), 400
        
        # Sanitize identifiers
        student_id = MetadataValidator.sanitize_identifier(student_id)
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        original_filename = MetadataValidator.sanitize_filename(original_filename)
        
        # Create metadata
        uploader_extra: dict = {
            "ip_address": request.remote_addr or "unknown",
            "user_agent": request.headers.get("User-Agent", "unknown")[:200],
        }
        if using_github:
            uploader_extra["source"] = "github"
            uploader_extra["github_repo"] = github_repo

        metadata = SubmissionMetadata(
            student_id=student_id,
            assignment_id=assignment_id,
            timestamp=datetime.now(timezone.utc),
            original_filename=original_filename,
            uploader_metadata=uploader_extra,
        )
        
        runs_root = get_runs_root(app)
        
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
            
            # ── Find true root of submission (bypassing macOS folders or zip wrappers)
            submission_root = find_submission_root(extracted)
            
            # ── Zero-content guard ───────────────────────────────────────
            # Reject the submission instantly if there are zero relevant web files
            SUPPORTED_EXTENSIONS = {".html", ".css", ".js", ".php", ".sql"}
            has_web_files = any(
                f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS 
                for f in submission_root.rglob("*")
            )

            if not has_web_files:
                try:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    if tmp_zip_path.exists():
                        tmp_zip_path.unlink()
                except Exception:
                    pass
                flash("No web development files (HTML, CSS, JS, PHP, SQL) were found in this repository. Please select the correct repository.", "error")
                return render_template(
                    "mark.html",
                    profiles=PROFILES.keys(),
                    github_connected=github_connected,
                    github_user=github_user,
                ), 400

            pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
            
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
                    "source": "github" if using_github else "upload",
                },
            )
            
            # ── Background execution ─────────────────────────────────
            # Heavy pipeline work is submitted to the thread pool so the
            # HTTP request returns immediately with a job ID.
            meta_dict = metadata.to_dict()

            # Write run_info immediately so the run appears in history
            initial_run_info = {
                "id": run_id,
                "mode": "mark",
                "profile": profile,
                "scoring_mode": scoring_mode.value,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": original_filename,
                "source": "github" if using_github else "upload",
                "status": "pending",
            }
            if using_github:
                initial_run_info["github_repo"] = github_repo
            save_run_info(run_dir, initial_run_info)

            def _run_mark_job() -> dict:
                """Executed in the thread pool."""
                try:
                    report_path = pipeline.run(
                        submission_path=submission_root,
                        workspace_path=run_dir,
                        profile=profile,
                        metadata=meta_dict,
                    )
                    run_info = {
                        "id": run_id,
                        "mode": "mark",
                        "profile": profile,
                        "scoring_mode": scoring_mode.value,
                        "created_at": initial_run_info["created_at"],
                        "report": report_path.name,
                        "summary": "summary.txt",
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": original_filename,
                        "source": "github" if using_github else "upload",
                        "status": "completed",
                    }
                    if using_github:
                        run_info["github_repo"] = github_repo
                    save_run_info(run_dir, run_info)
                    _write_run_index_mark(run_dir, run_info, report_path)
                    return {"run_id": run_id}
                except Exception as exc:
                    failed_info = dict(initial_run_info, status="failed", error=str(exc))
                    save_run_info(run_dir, failed_info)
                    raise

            job_id = job_manager.submit_job("single_mark", _run_mark_job)
            return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
        finally:
            # Clean up temporary file
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass

    @app.route("/batch", methods=["GET", "POST"])
    @teacher_or_admin_required
    def batch():
        if request.method == "GET":
            return render_template("batch.html", profiles=PROFILES.keys())

        # ── Sandbox enforcement ──────────────────────────────────────
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
            return render_template("batch.html", profiles=PROFILES.keys()), 503

        # Get form data
        file = request.files.get("submission")
        profile = request.form.get("profile", "frontend")
        assignment_id = request.form.get("assignment_id", "").strip()
        scoring_mode_str = request.form.get("scoring_mode", "static_plus_llm").strip()
        
        # Validate file
        if not file or not file.filename:
            flash("Please upload a .zip file.", "error")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        if not validate_file_type(file.filename):
            flash("Invalid file type. Please upload a .zip file.", "error")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        # Validate and convert scoring mode
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            flash(f"Invalid scoring mode: {scoring_mode_str}", "error")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        # Validate assignment ID
        valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
        if not valid_assignment:
            flash(f"Invalid Assignment ID: {assignment_error}", "error")
            return render_template("batch.html", profiles=PROFILES.keys()), 400
        
        # Sanitize
        assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
        original_filename = MetadataValidator.sanitize_filename(file.filename)
        
        runs_root = get_runs_root(app)
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            file.save(tmp_file.name)
            tmp_zip_path = Path(tmp_file.name)
        
        # ── Strict ZIP content validation (magic-byte check) ─────
        if not validate_is_zipfile(tmp_zip_path):
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass
            flash("The uploaded file is not a valid ZIP archive.", "error")
            return render_template("batch.html", profiles=PROFILES.keys()), 400

        try:
            # Validate file size
            valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
            if not valid_size:
                flash(size_error or "File size exceeds maximum limit.", "error")
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
            shutil.copy2(tmp_zip_path, upload_zip)
            
            # Save batch metadata
            save_metadata(run_dir, batch_metadata)
            
            extracted = run_dir / "batch_inputs"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
            
            # Run batch with metadata context — off the request thread
            initial_run_info = {
                "id": run_id,
                "mode": "batch",
                "profile": profile,
                "scoring_mode": scoring_mode.value,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "assignment_id": assignment_id,
                "original_filename": original_filename,
                "status": "pending",
            }
            save_run_info(run_dir, initial_run_info)

            def _run_batch_job() -> dict:
                """Executed in the thread pool."""
                try:
                    summary = run_batch(
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
                        "batch_summary": summary,
                        "assignment_id": assignment_id,
                        "original_filename": original_filename,
                        "status": "completed",
                    }
                    _write_batch_analytics(run_dir, profile, run_id)
                    _write_batch_reports_zip(run_dir, profile, run_id)
                    _write_run_index_batch(run_dir, run_info)
                    save_run_info(run_dir, run_info)
                    return {"run_id": run_id}
                except Exception as exc:
                    failed_info = dict(initial_run_info, status="failed", error=str(exc))
                    save_run_info(run_dir, failed_info)
                    raise

            job_id = job_manager.submit_job("batch_mark", _run_batch_job)
            return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
        finally:
            # Clean up temporary file
            try:
                tmp_zip_path.unlink()
            except Exception:
                pass

    # ── Job polling API ──────────────────────────────────────────────
    @app.route("/api/jobs/<job_id>")
    def job_status(job_id: str):
        """Return the current state of a background job as JSON."""
        status = job_manager.get_job_status(job_id)
        if status is None:
            return jsonify({"error": "Job not found"}), 404
        # Convert Path results to strings for JSON serialisation
        result = status.get("result")
        if isinstance(result, dict):
            status["result"] = {
                k: str(v) if hasattr(v, "__fspath__") else v
                for k, v in result.items()
            }
        elif hasattr(result, "__fspath__"):
            status["result"] = str(result)
        return jsonify(status)

    @app.route("/runs")
    @login_required
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

    @app.route("/runs/<run_id>/delete", methods=["POST"])
    @teacher_or_admin_required
    def delete_run(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            flash("Run not found.", "error")
            return redirect(url_for("runs"))
        shutil.rmtree(run_dir, ignore_errors=True)
        flash(f"Run '{run_id[:24]}…' deleted.", "success")
        return redirect(url_for("runs"))

    @app.route("/runs/<run_id>")
    @login_required
    def run_detail(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        run_info = load_run_info(run_dir)

        # ── RBAC: student access control ──────────────────────────────
        user = get_current_user()
        if user and user["role"] == "student":
            # Students can only view their own runs
            run_student_id = run_info.get("student_id", "")
            if run_student_id != user["userID"]:
                # Check batch submissions too
                batch_summary = run_info.get("batch_summary", [])
                found = False
                if isinstance(batch_summary, list):
                    for rec in batch_summary:
                        if rec.get("student_id") == user["userID"]:
                            found = True
                            break
                if not found:
                    flash("You do not have access to this submission.", "error")
                    return redirect(url_for("student.dashboard"))

        # Check marks_released status for grade visibility
        assignment_id = run_info.get("assignment_id", "")
        assignment = get_assignment(assignment_id) if assignment_id else None
        marks_released = assignment["marks_released"] if assignment else True  # default True for non-assignment runs

        context = {"run": run_info, "run_id": run_id, "marks_released": marks_released}
        if run_info.get("mode") == "mark":
            report_path = run_dir / run_info.get("report", "report.json")
            if report_path.exists():
                context["report"] = _ensure_check_stats(
                    json.loads(report_path.read_text(encoding="utf-8"))
                )
                context["threat_file_contents"] = _load_threat_file_contents(
                    context["report"].get("findings", []), run_dir
                )
        else:
            summary_path = run_dir / run_info.get("summary", "batch_summary.json")
            if summary_path.exists():
                context["batch"] = json.loads(summary_path.read_text(encoding="utf-8"))
            analytics_path = run_dir / "analytics" / _analytics_filenames(run_id)["json"]
            if analytics_path.exists():
                context["analytics"] = json.loads(analytics_path.read_text(encoding="utf-8"))
        return render_template("run_detail.html", **context)

    @app.route("/runs/<run_id>/override-threat", methods=["POST"])
    @teacher_or_admin_required
    def override_threat(run_id: str):
        """Re-run the marking pipeline for a threat-blocked submission, bypassing the threat scan.

        The original ZIP file is re-extracted and passed through the full
        assessment pipeline with ``skip_threat_scan=True``.  Returns a job ID
        that the client can poll via ``/api/jobs/<job_id>``.
        """
        runs_root = get_runs_root(app)
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
                return jsonify({"error": "Original submission ZIP not found — cannot reprocess"}), 404
            upload_zip = zips[0]

        profile = run_info.get("profile", "frontend")
        scoring_mode_str = run_info.get("scoring_mode", "static_plus_llm")
        try:
            scoring_mode = ScoringMode(scoring_mode_str)
        except ValueError:
            scoring_mode = ScoringMode("static_plus_llm")

        pipeline = AssessmentPipeline(scoring_mode=scoring_mode)
        meta_dict = dict(run_info)

        def _run_override_job() -> dict:
            """Re-extract and re-assess the submission with threat scan disabled."""
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
            # Persist override timestamp in run_info so the dashboard reflects it
            updated = dict(run_info)
            updated["threat_override"] = True
            updated["threat_override_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            save_run_info(run_dir, updated)
            return {"run_id": run_id}

        job_id = job_manager.submit_job("threat_override", _run_override_job)
        return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202

    @app.route("/batch/<run_id>/analytics")
    @login_required
    def batch_analytics(run_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        run_info = load_run_info(run_dir)

        # Students can only see analytics after marks are released
        user = get_current_user()
        assignment_id = run_info.get("assignment_id", "")
        assignment = get_assignment(assignment_id) if assignment_id else None
        marks_released = assignment["marks_released"] if assignment else True
        if user and user["role"] == "student" and not marks_released:
            flash("Analytics are not yet available for this assignment.", "warning")
            return redirect(url_for("student.dashboard"))

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
    @login_required
    def run_artifact(run_id: str, relpath: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        allowed_roots = {"artifacts", "analytics", "runs", "reports", "evaluation", "submission"}
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
        # Serve images inline for vision analysis screenshots; download others
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
        as_download = candidate.suffix.lower() not in image_exts
        return send_file(candidate, as_attachment=as_download, download_name=candidate.name)

    @app.route("/batch/<run_id>/submissions/<submission_id>/view")
    @login_required
    def batch_submission_view(run_id: str, submission_id: str):
        """View a batch submission's report in the browser (like a single submission)."""
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        
        submission_dir = run_dir / "runs" / submission_id
        report_path = submission_dir / "report.json"
        
        # Security check
        try:
            report_path.resolve().relative_to(run_dir.resolve())
        except Exception:
            return "Not allowed", 403
        
        if not report_path.exists():
            return "Report not found", 404
        
        run_info = load_run_info(run_dir) or {}
        report = _ensure_check_stats(
            json.loads(report_path.read_text(encoding="utf-8"))
        )

        # Create a pseudo run_info for this submission
        submission_run_info = {
            "mode": "mark",
            "profile": run_info.get("profile", "frontend"),
            "assignment_id": run_info.get("assignment_id", ""),
            "student_id": submission_id,
            "created_at": run_info.get("created_at", ""),
        }

        # submission_dir doubles as the "run_dir" for threat file loading
        # because batch sub-runs store their files under runs/<id>/submission/
        return render_template(
            "run_detail.html",
            run=submission_run_info,
            run_id=run_id,
            report=report,
            threat_file_contents=_load_threat_file_contents(
                report.get("findings", []), submission_dir
            ),
            batch_submission_id=submission_id,  # Flag to show back button
            back_url=url_for('run_detail', run_id=run_id),
        )

    @app.route("/batch/<run_id>/submissions/<submission_id>/report.json")
    @login_required
    def batch_submission_report(run_id: str, submission_id: str):
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
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

    @app.route("/run/<run_id>/bundle")
    @login_required
    def download_bundle(run_id: str):
        """Download grading-relevant artifacts for a run as a ZIP bundle.

        Included:
          - report.html, report.json, summary.txt (top-level reports)
          - submission/  (student code, full tree)
          - artifacts/   (screenshots only — .png, .jpg, .jpeg, .gif, .webp)
          - batch files  (batch_summary.*, findings_frequency.*, analytics)

        Excluded:
          - uploaded_extract/  (duplicate of submission)
          - *.zip              (original upload archive)
          - run_*.json, metadata.json  (system bookkeeping)
          - .trace / .log files inside artifacts
        """
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)

        if run_dir is None:
            return "Run not found", 404

        run_info = load_run_info(run_dir) or {}
        profile = run_info.get("profile", "")
        mode = run_info.get("mode", "mark")

        # Image extensions kept from artifacts/
        _ARTIFACT_IMAGE_EXTS: frozenset[str] = frozenset(
            {".png", ".jpg", ".jpeg", ".gif", ".webp"}
        )

        # Create ZIP in memory
        zip_buffer = BytesIO()

        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # ── 1. Top-level report files ──
                top_level_files = [
                    "report.json",
                    "summary.txt",
                ]

                # Add batch-specific top-level files
                if mode == "batch":
                    top_level_files.extend([
                        "batch_summary.json",
                        "batch_summary.csv",
                        "findings_frequency.csv",
                    ])

                for filename in top_level_files:
                    file_path = run_dir / filename
                    if file_path.is_file():
                        try:
                            zf.write(file_path, arcname=filename)
                        except Exception:
                            pass

                # ── 2. submission/ (full tree — student code) ──
                submission_dir = run_dir / "submission"
                if submission_dir.is_dir():
                    for fpath in submission_dir.rglob("*"):
                        if fpath.is_file():
                            try:
                                zf.write(fpath, arcname=fpath.relative_to(run_dir))
                            except Exception:
                                pass

                # ── 3. artifacts/ (images only, skip .trace / .log) ──
                artifacts_dir = run_dir / "artifacts"
                if artifacts_dir.is_dir():
                    for fpath in artifacts_dir.rglob("*"):
                        if fpath.is_file() and fpath.suffix.lower() in _ARTIFACT_IMAGE_EXTS:
                            try:
                                zf.write(fpath, arcname=fpath.relative_to(run_dir))
                            except Exception:
                                pass

                # ── 4. Batch analytics (if applicable) ──
                if mode == "batch":
                    analytics_suffixes = ["json", "csv", "component", "needs", "runtime"]
                    for suffix in analytics_suffixes:
                        glob_pattern = f"batch_analytics_{run_id}.*" if suffix == "json" else f"*_{suffix}_{run_id}.*"
                        for analytics_file in run_dir.glob(glob_pattern):
                            if analytics_file.is_file():
                                try:
                                    rel_path = analytics_file.relative_to(run_dir)
                                    zf.write(analytics_file, arcname=f"analytics/{rel_path}")
                                except Exception:
                                    pass

            # Prepare response
            zip_buffer.seek(0)
            dl_name = f"run_{profile}_{run_id}.zip"
            return send_file(
                zip_buffer,
                mimetype="application/zip",
                as_attachment=True,
                download_name=dl_name,
            )

        except Exception as e:
            app.logger.error(f"Error creating bundle for run {run_id}: {e}")
            return "Error creating bundle", 500

    @app.route("/download/<run_id>/<filename>")
    @login_required
    def download(run_id: str, filename: str):
        if not allowed_download(filename, allowed=ALLOWED_DOWNLOADS):
            return "Not allowed", 403
        runs_root = get_runs_root(app)
        run_dir = find_run_by_id(runs_root, run_id)
        if run_dir is None:
            return "Run not found", 404
        if filename.startswith(("batch_analytics_", "component_breakdown_", "needs_attention_")):
            _ensure_batch_analytics(run_dir, run_id)
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
        return send_file(target, as_attachment=True, download_name=dl_name)

    # ── Threats dashboard ────────────────────────────────────────────

    @app.route("/threats")
    @teacher_or_admin_required
    def threats():
        from ams.sandbox.forensics import list_retained_containers
        containers = list_retained_containers()
        return render_template("threats.html", containers=containers)

    @app.route("/threats/<container_name>/inspect")
    @teacher_or_admin_required
    def threat_inspect(container_name: str):
        from ams.sandbox.forensics import inspect_container
        info = inspect_container(container_name)
        if info is None:
            flash("Container not found or not inspectable.", "error")
            return redirect(url_for("threats"))
        return render_template(
            "threats.html",
            containers=[],
            inspected=info,
        )

    @app.route("/threats/<container_name>/cleanup", methods=["POST"])
    @teacher_or_admin_required
    def threat_cleanup(container_name: str):
        from ams.sandbox.forensics import cleanup_container
        ok = cleanup_container(container_name)
        if ok:
            flash(f"Container {container_name} removed.", "success")
        else:
            flash(f"Failed to remove container {container_name}.", "error")
        return redirect(url_for("threats"))

    # ── GitHub OAuth + API endpoints ────────────────────────────────

    @app.route("/api/github/login")
    def github_login():
        """Redirect the user to GitHub's OAuth authorization page."""
        import secrets as _secrets

        if not GITHUB_CLIENT_ID:
            flash("GitHub integration is not configured (missing Client ID).")
            return redirect(url_for("mark"))

        state = _secrets.token_urlsafe(32)
        session["github_oauth_state"] = state

        params = (
            f"client_id={GITHUB_CLIENT_ID}"
            f"&redirect_uri={GITHUB_OAUTH_CALLBACK}"
            f"&scope=repo"
            f"&state={state}"
        )
        return redirect(f"https://github.com/login/oauth/authorize?{params}")

    @app.route("/api/github/callback")
    def github_callback():
        """Handle the OAuth redirect from GitHub.

        Exchanges the temporary ``code`` for an ``access_token``, then stores
        the token and user info in the session.
        """
        code = request.args.get("code", "")
        state = request.args.get("state", "")

        # CSRF protection — validate state
        expected_state = session.pop("github_oauth_state", None)
        if not code or not state or state != expected_state:
            flash("GitHub authorization failed (invalid state). Please try again.")
            return redirect(url_for("mark"))

        # Exchange code for access_token
        try:
            token_resp = _requests.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": GITHUB_OAUTH_CALLBACK,
                },
                timeout=15,
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
        except _requests.RequestException as exc:
            logger.warning("GitHub OAuth token exchange failed: %s", exc)
            flash("Failed to connect to GitHub. Please try again.")
            return redirect(url_for("mark"))

        access_token = token_data.get("access_token")
        if not access_token:
            error_desc = token_data.get("error_description", "Unknown error")
            flash(f"GitHub authorization failed: {error_desc}")
            return redirect(url_for("mark"))

        # Fetch user info for display
        try:
            user_resp = _requests.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            user_resp.raise_for_status()
            user_info = user_resp.json()
        except _requests.RequestException:
            user_info = {}

        session["github_token"] = access_token
        session["github_user"] = user_info.get("login", "")
        session["github_avatar"] = user_info.get("avatar_url", "")

        flash(f"Connected to GitHub as {user_info.get('login', 'unknown')}.", "success")
        return redirect(url_for("mark"))

    @app.route("/api/github/disconnect", methods=["POST"])
    def github_disconnect():
        """Clear the GitHub OAuth token from the session."""
        session.pop("github_token", None)
        session.pop("github_user", None)
        session.pop("github_avatar", None)
        return jsonify({"status": "disconnected"})

    @app.route("/api/github/repos")
    def github_repos():
        """Return the authenticated user's GitHub repositories as JSON."""
        token = session.get("github_token")
        if not token:
            return jsonify({"error": "GitHub account not linked"}), 401

        try:
            resp = _requests.get(
                "https://api.github.com/user/repos",
                headers={"Authorization": f"Bearer {token}"},
                params={"sort": "updated", "per_page": 100},
                timeout=15,
            )
            resp.raise_for_status()
            repos = resp.json()
        except _requests.RequestException as exc:
            return jsonify({"error": f"Failed to fetch repositories: {exc}"}), 502

        return jsonify([
            {
                "full_name": r["full_name"],
                "name": r["name"],
                "private": r["private"],
                "updated_at": r.get("updated_at", ""),
                "description": r.get("description") or "",
                "default_branch": r.get("default_branch", "main"),
            }
            for r in repos
        ])

    @app.route("/api/github/repos/<owner>/<repo>/branches")
    def github_branches(owner: str, repo: str):
        """Return branches for a specific repository."""
        token = session.get("github_token")
        if not token:
            return jsonify({"error": "GitHub account not linked"}), 401

        full_name = f"{owner}/{repo}"

        # Fetch default branch name from the repo metadata
        default_branch = "main"
        try:
            repo_resp = _requests.get(
                f"https://api.github.com/repos/{full_name}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            repo_resp.raise_for_status()
            default_branch = repo_resp.json().get("default_branch", "main")
        except _requests.RequestException:
            pass  # fall back to 'main'

        try:
            resp = _requests.get(
                f"https://api.github.com/repos/{full_name}/branches",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 100},
                timeout=15,
            )
            resp.raise_for_status()
            branches = resp.json()
        except _requests.RequestException as exc:
            return jsonify({"error": f"Failed to fetch branches: {exc}"}), 502

        return jsonify([
            {
                "name": b["name"],
                "is_default": b["name"] == default_branch,
            }
            for b in branches
        ])


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


def _resolve_download_path(run_dir: Path, filename: str) -> Path:
    """Resolve the path to a downloadable file within a run directory."""
    # Direct match
    candidate = run_dir / filename
    if candidate.exists():
        return candidate.resolve()
    
    # Check analytics directory
    analytics_dir = run_dir / "analytics"
    candidate = analytics_dir / filename
    if candidate.exists():
        return candidate.resolve()
    
    # Check evaluation directory
    evaluation_dir = run_dir / "evaluation"
    candidate = evaluation_dir / filename
    if candidate.exists():
        return candidate.resolve()
    
    # For files like "batch_reports.zip", search for matching prefix pattern
    base_name = filename.rsplit(".", 1)[0]  # e.g., "batch_reports"
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    
    # Search run_dir for files starting with the base name
    for f in run_dir.glob(f"{base_name}*.{ext}"):
        if f.is_file():
            return f.resolve()
    
    # Search analytics dir
    for f in analytics_dir.glob(f"{base_name}*.{ext}"):
        if f.is_file():
            return f.resolve()
    
    # Fallback to original path
    return (run_dir / filename).resolve()


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
