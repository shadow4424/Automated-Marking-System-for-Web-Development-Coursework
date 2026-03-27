"""AMS Web Interface application factory."""
from __future__ import annotations

import logging
import secrets
from typing import Mapping

from flask import Flask

from ams.core.attempts import sync_attempts_from_storage
from ams.core.db import init_db
from ams.io.web_storage import get_runs_root
from ams.web.routes_batch import batch_bp
from ams.web.routes_dashboard import dashboard_bp
from ams.web.routes_export import export_bp
from ams.web.routes_github import github_bp
from ams.web.routes_marking import marking_bp
from ams.web.routes_runs import runs_bp
from ams.web.view_helpers import _clean_path, _format_submission_datetime, _render_evidence_value

logger = logging.getLogger(__name__)
MAX_UPLOAD_MB = 25


def create_app(config: Mapping[str, object] | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.secret_key = app.config["SECRET_KEY"]

    if app.config.get("AMS_ENABLE_STARTUP_RUN_CLEANUP", False):
        try:
            from ams.io.workspace import WorkspaceManager

            max_age_hours = app.config.get("AMS_STARTUP_RUN_MAX_AGE_HOURS")
            WorkspaceManager(get_runs_root(app)).cleanup_old_runs(
                max_age_hours=int(max_age_hours) if max_age_hours is not None else None
            )
        except Exception as exc:
            logger.warning("Workspace cleanup failed: %s", exc)

    app.jinja_env.filters["clean_path"] = _clean_path
    app.jinja_env.filters["format_submission_datetime"] = _format_submission_datetime
    app.jinja_env.globals["render_evidence_value"] = _render_evidence_value

    init_db()
    try:
        sync_attempts_from_storage(get_runs_root(app))
    except Exception as exc:
        logger.warning("Attempt backfill failed during startup: %s", exc)

    from ams.web.auth import auth_bp, inject_user_context
    from ams.web.routes_account import account_bp
    from ams.web.routes_admin import admin_bp
    from ams.web.routes_student import student_bp
    from ams.web.routes_teacher import teacher_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(marking_bp)
    app.register_blueprint(batch_bp)
    app.register_blueprint(runs_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(github_bp)

    app.context_processor(inject_user_context)
    return app
