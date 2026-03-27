from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests as _requests
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from ams.core.attempts import (
    create_attempt,
    create_attempt_storage_dir,
    filter_attempts_for_root,
    get_attempt_by_run_reference,
    get_student_assignment_summary,
    list_attempts,
    recompute_active_attempt,
    sync_attempts_from_storage,
    update_attempt,
    utc_now_iso,
)
from ams.core.config import GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_OAUTH_CALLBACK, ScoringMode
from ams.core.db import PREVIEW_STUDENT_ID, get_assignment, list_assignments, list_assignments_for_student
from ams.core.job_manager import job_manager
from ams.core.pipeline import AssessmentPipeline
from ams.core.profiles import get_visible_profile_specs
from ams.io.metadata import MetadataValidator, SubmissionMetadata
from ams.io.web_storage import (
    create_run_dir,
    extract_review_flags_from_report,
    find_run_by_id,
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
from ams.web.auth import login_required
from ams.web.routes_dashboard import _assignment_submission_locked, _submission_lock_message
from ams.web.validators import validate_is_zipfile
from ams.web.view_helpers import *

logger = logging.getLogger(__name__)
marking_bp = Blueprint('marking', __name__)
MAX_UPLOAD_MB = 25
PROFILE_CHOICES = tuple(get_visible_profile_specs().keys())


def _write_run_index_mark(*args, **kwargs):
    from ams.web.routes_runs import _write_run_index_mark as writer
    return writer(*args, **kwargs)


def _submission_identity(student_id: str | None, assignment_id: str | None) -> tuple[str, str] | None:
    student_val = (student_id or "").strip()
    assignment_val = (assignment_id or "").strip()
    if not student_val or not assignment_val:
        return None
    return assignment_val, student_val

def _replace_existing_submissions(
    runs_root: Path,
    submissions: list[tuple[str, str]],
    *,
    current_run_id: str,
) -> None:
    # Submission attempts are immutable. This compatibility shim intentionally does nothing.
    return


def _build_submission_detail_view(
    run: Mapping[str, object],
    report: Mapping[str, object] | None,
) -> dict[str, Any]:
    report_data = dict(report or {})
    score_evidence = dict(report_data.get("score_evidence", {}) or {})
    confidence = dict(score_evidence.get("confidence", {}) or {})
    review = dict(score_evidence.get("review", {}) or {})
    role_mapping = dict(score_evidence.get("role_mapping", {}) or {})
    environment = dict(report_data.get("environment", {}) or {})
    component_scores = dict(((report_data.get("scores", {}) or {}).get("by_component", {}) or {}))
    requirement_results = [
        dict(item)
        for item in list(score_evidence.get("requirements", []) or [])
        if isinstance(item, Mapping)
    ]
    raw_findings = [
        _normalize_raw_finding(finding)
        for finding in list(report_data.get("findings", []) or [])
        if isinstance(finding, Mapping)
    ]
    diagnostics = [
        _normalize_raw_finding(finding)
        for finding in list(report_data.get("diagnostics", []) or [])
        if isinstance(finding, Mapping)
    ]
    checks = [
        dict(item)
        for item in list(report_data.get("checks", []) or [])
        if isinstance(item, Mapping)
    ]
    behavioural_evidence = [
        dict(item)
        for item in list(report_data.get("behavioural_evidence", []) or [])
        if isinstance(item, Mapping)
    ]
    browser_evidence = [
        dict(item)
        for item in list(report_data.get("browser_evidence", []) or [])
        if isinstance(item, Mapping)
    ]

    findings_by_key: dict[str, list[dict[str, Any]]] = {}
    for finding in raw_findings:
        findings_by_key.setdefault(_finding_group_key(finding), []).append(finding)
    checks_by_id = {
        str(check.get("check_id") or "").strip(): check
        for check in checks
        if str(check.get("check_id") or "").strip()
    }

    browser_capture_failed = any(item["id"] == "BROWSER.CAPTURE_FAIL" for item in diagnostics)
    browser_reliable = (
        bool(environment.get("browser_available", True))
        and bool(environment.get("browser_tests_run", True))
        and not browser_capture_failed
    )

    valid_ux_findings: list[dict[str, Any]] = []
    raw_ux_findings: list[dict[str, Any]] = []
    for finding in raw_findings:
        evidence = finding.get("evidence")
        if not isinstance(evidence, Mapping):
            continue
        ux_review = evidence.get("ux_review")
        if not isinstance(ux_review, Mapping):
            continue
        raw_ux_findings.append(finding)
        if str(ux_review.get("status") or "").strip().upper() == "NOT_EVALUATED":
            continue
        if browser_reliable:
            valid_ux_findings.append(finding)
    hidden_ux_keys = {_finding_group_key(finding) for finding in raw_ux_findings} if not browser_reliable else set()

    evidence_items: list[dict[str, Any]] = []
    matched_keys: set[str] = set()

    for requirement in requirement_results:
        requirement_id = str(requirement.get("requirement_id") or "").strip()
        if not requirement_id:
            continue
        matched_keys.add(requirement_id)
        component = str(requirement.get("component") or "").strip().lower()
        stage = str(requirement.get("stage") or "").strip().lower()
        status = _normalize_status(requirement.get("status"), fallback="UNKNOWN")
        check = checks_by_id.get(requirement_id)
        related_findings = list(findings_by_key.get(requirement_id, []))
        detail = _first_non_empty(
            list((check or {}).get("messages", []) or [])
            + [
                requirement.get("skipped_reason"),
                (requirement.get("evidence") or {}).get("reason")
                if isinstance(requirement.get("evidence"), Mapping)
                else "",
            ]
        )
        evidence_items.append(
            {
                "kind": "requirement",
                "type_label": "Requirement",
                "title": _first_non_empty([requirement.get("description"), _humanize_identifier(requirement_id)]),
                "secondary_id": requirement_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": component,
                "component_label": _component_label(component),
                "component_filter": _component_filter_value(component, stage=stage),
                "stage": stage,
                "stage_label": _stage_label(stage),
                "detail": detail,
                "required": bool(requirement.get("required", True)),
                "score_display": requirement.get("score"),
                "requirement": requirement,
                "check": check,
                "raw_findings": related_findings,
                "search_text": " ".join(
                    [
                        requirement_id,
                        str(requirement.get("description") or ""),
                        component,
                        stage,
                        status,
                        detail,
                    ]
                    + [str(item.get("title") or "") for item in related_findings]
                ).lower(),
            }
        )

    for check_id, check in checks_by_id.items():
        if check_id in matched_keys or check_id in hidden_ux_keys:
            continue
        related_findings = list(findings_by_key.get(check_id, []))
        first_finding = related_findings[0] if related_findings else {}
        stage = str(first_finding.get("stage") or "").strip().lower()
        component = str(check.get("component") or first_finding.get("component") or "").strip().lower()
        status = _normalize_status(check.get("status"), fallback="UNKNOWN")
        detail = _first_non_empty(list(check.get("messages", []) or []) + [first_finding.get("message")])
        evidence_items.append(
            {
                "kind": "check",
                "type_label": "Check",
                "title": _humanize_identifier(check_id),
                "secondary_id": check_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": component,
                "component_label": _component_label(component),
                "component_filter": _component_filter_value(component, stage=stage),
                "stage": stage,
                "stage_label": _stage_label(stage),
                "detail": detail or _describe_identifier(check_id),
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": check,
                "raw_findings": related_findings,
                "search_text": " ".join(
                    [
                        check_id,
                        _humanize_identifier(check_id),
                        component,
                        stage,
                        status,
                        detail or "",
                    ]
                ).lower(),
            }
        )

    threat_findings = [finding for finding in raw_findings if finding["status"] == "THREAT"]
    for index, finding in enumerate(threat_findings, start=1):
        evidence_items.append(
            {
                "kind": "threat",
                "type_label": "Security",
                "title": finding["title"],
                "secondary_id": finding["id"],
                "status": "FAIL",
                "badge_label": "Threat",
                "tone": "danger",
                "component": "security",
                "component_label": "Security",
                "component_filter": "other",
                "stage": "",
                "stage_label": "Security",
                "detail": finding["message"],
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [finding],
                "search_text": f"{finding['id']} {finding['title']} {finding['message']} security threat".lower(),
                "_sort_index": index,
            }
        )

    for finding in valid_ux_findings:
        evidence = dict(finding.get("evidence", {}) or {})
        ux_review = dict(evidence.get("ux_review", {}) or {})
        page_name = str(evidence.get("page") or ux_review.get("page") or finding["title"]).strip()
        feedback = _first_non_empty(
            [ux_review.get("feedback"), ux_review.get("improvement_recommendation"), finding.get("message")]
        )
        evidence_items.append(
            {
                "kind": "ux",
                "type_label": "UX review",
                "title": page_name or "UX review",
                "secondary_id": finding["id"],
                "status": "PASS" if str(ux_review.get("status") or "").strip().upper() == "PASS" else "WARN",
                "badge_label": str(ux_review.get("status") or "Review").replace("_", " ").title(),
                "tone": "success" if str(ux_review.get("status") or "").strip().upper() == "PASS" else "warning",
                "component": "browser",
                "component_label": "Browser",
                "component_filter": "browser",
                "stage": "browser",
                "stage_label": "Browser",
                "detail": feedback,
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [finding],
                "search_text": f"{finding['id']} {page_name} {feedback} ux browser".lower(),
            }
        )

    for bev in behavioural_evidence:
        test_id = str(bev.get("test_id") or "").strip()
        if not test_id:
            continue
        bev_status = str(bev.get("status") or "").strip().lower()
        status_map = {
            "pass": "PASS",
            "fail": "FAIL",
            "timeout": "FAIL",
            "skipped": "SKIPPED",
            "error": "FAIL",
        }
        status = status_map.get(bev_status, "SKIPPED")
        duration = bev.get("duration_ms")
        detail_parts = []
        if bev.get("stderr"):
            detail_parts.append(str(bev["stderr"])[:300])
        if duration is not None:
            detail_parts.append(f"Duration: {duration} ms")
        detail = " - ".join(detail_parts) if detail_parts else ""
        evidence_items.append(
            {
                "kind": "behavioural",
                "type_label": "Behavioural",
                "title": _humanize_identifier(test_id),
                "secondary_id": test_id,
                "status": status,
                "badge_label": status,
                "tone": _status_tone(status),
                "component": str(bev.get("component") or "").strip().lower(),
                "component_label": "Behavioural",
                "component_filter": "behavioural",
                "stage": "runtime",
                "stage_label": "Behavioural",
                "detail": detail,
                "required": False,
                "score_display": None,
                "requirement": None,
                "check": None,
                "raw_findings": [],
                "search_text": " ".join(
                    [
                        test_id,
                        _humanize_identifier(test_id),
                        str(bev.get("component") or ""),
                        bev_status,
                        detail,
                    ]
                ).lower(),
                "_bev": bev,
            }
        )

    evidence_items.sort(
        key=lambda item: (
            _DETAIL_STATUS_PRIORITY.get(item["status"], 99),
            0 if item["kind"] == "requirement" else (1 if item["kind"] == "threat" else 2),
            _DETAIL_COMPONENT_ORDER.index(item["component_filter"])
            if item["component_filter"] in _DETAIL_COMPONENT_ORDER
            else len(_DETAIL_COMPONENT_ORDER),
            str(item["title"]).lower(),
            str(item.get("_sort_index") or 0),
        )
    )

    component_cards = []
    for component, data in component_scores.items():
        summary = dict(data.get("requirement_summary", {}) or {})
        score_value = data.get("score")
        if score_value == "SKIPPED" and int(summary.get("requirement_count", 0) or 0) == 0:
            continue
        numeric_score = _coerce_float(score_value)
        if numeric_score is not None:
            tone = "success" if numeric_score >= 0.7 else ("warning" if numeric_score > 0 else "danger")
            score_label = f"{int(round(numeric_score * 100))}%"
        else:
            tone = "muted"
            score_label = str(score_value or "N/A")
        component_cards.append(
            {
                "key": component,
                "label": _component_label(component),
                "score_label": score_label,
                "tone": tone,
                "summary": summary,
                "detail": (
                    f"{int(summary.get('met', 0) or 0)} met, "
                    f"{int(summary.get('partial', 0) or 0)} partial, "
                    f"{int(summary.get('failed', 0) or 0)} failed"
                    + (
                        f", {int(summary.get('skipped', 0) or 0)} skipped"
                        if int(summary.get("skipped", 0) or 0)
                        else ""
                    )
                    + "."
                ),
            }
        )
    component_cards.sort(
        key=lambda item: _DETAIL_COMPONENT_ORDER.index(item["key"])
        if item["key"] in _DETAIL_COMPONENT_ORDER
        else len(_DETAIL_COMPONENT_ORDER)
    )

    student_issues = [
        item
        for item in evidence_items
        if (
            (item["kind"] == "requirement" and item["required"] and item["status"] in {"FAIL", "PARTIAL"})
            or (item["kind"] == "check" and item["status"] in {"FAIL", "WARN"})
            or item["kind"] == "threat"
        )
    ]
    priority_findings = [
        item
        for item in evidence_items
        if item["kind"] == "requirement" and item["required"] and item["status"] in {"FAIL", "PARTIAL"}
    ]

    limitations: list[dict[str, Any]] = []

    def _push_limitation(title: str, detail: str = "", *, tone: str = "warning", secondary_id: str = "") -> None:
        key = (title.strip().lower(), secondary_id.strip().lower())
        if not title or any((item.get("_key") == key) for item in limitations):
            return
        limitations.append(
            {
                "_key": key,
                "title": title,
                "detail": detail or title,
                "tone": tone,
                "secondary_id": secondary_id,
            }
        )

    run_status = str(run.get("status") or "").strip().lower()
    if run_status == "pending":
        _push_limitation(
            "Rerun is still in progress",
            "This submission is being reprocessed in the background, so the current result is incomplete.",
        )
    elif run_status in {"failed", "error"}:
        _push_limitation(
            "The latest rerun failed",
            "The most recent attempt to reprocess this submission did not complete successfully.",
            tone="danger",
        )

    if bool(run.get("llm_error_flagged")):
        _push_limitation(
            "LLM-assisted review failed",
            str(run.get("llm_error_message") or "An LLM-assisted review step failed, so manual review is required."),
            tone="warning",
            secondary_id="llm",
        )

    if environment and not environment.get("php_available", True):
        _push_limitation(
            "PHP runtime was unavailable",
            "Server-side runtime checks could not be completed because PHP was unavailable in this run.",
            secondary_id="php_unavailable",
        )
    if environment and not environment.get("behavioural_tests_run", True):
        _push_limitation(
            "Runtime checks were unavailable",
            "Behavioural runtime stages did not complete, so dynamic server-side evidence is incomplete.",
            secondary_id="runtime_unavailable",
        )
    if environment and not environment.get("browser_available", True):
        _push_limitation(
            "Browser environment was unavailable",
            "Browser-based checks could not be completed in this run.",
            secondary_id="browser_unavailable",
        )
    if environment and not environment.get("browser_tests_run", True):
        _push_limitation(
            "Browser checks were unavailable",
            "UI and browser-driven evidence was incomplete because the browser stage did not complete.",
            secondary_id="browser_tests_unavailable",
        )

    for flag in list(confidence.get("flags", []) or []):
        text = _CONFIDENCE_FLAG_TEXT.get(str(flag))
        if text:
            _push_limitation(text.rstrip("."), text, secondary_id=str(flag))

    for finding in diagnostics:
        if finding["id"] == "BROWSER.CAPTURE_FAIL":
            _push_limitation(
                "Browser screenshot capture failed",
                finding["message"],
                secondary_id=finding["id"],
            )

    if raw_ux_findings and not browser_reliable:
        _push_limitation(
            "UX review is hidden for this run",
            "Browser capture was not reliable in this assessment result, so UX screenshots and review summaries are withheld to avoid contradictory evidence.",
            secondary_id="ux_hidden",
        )

    limitations = limitations[:6]

    stage_status = [
        {"label": "Static checks", "value": "Completed"},
        {
            "label": "Runtime checks",
            "value": "Completed" if environment.get("behavioural_tests_run", True) else "Unavailable",
        },
        {
            "label": "Browser checks",
            "value": "Completed" if environment.get("browser_tests_run", True) else "Unavailable",
        },
    ]

    role_context = []
    for role, paths in sorted((role_mapping.get("roles", {}) or {}).items()):
        clean_paths = [str(path) for path in list(paths or []) if str(path).strip()]
        if clean_paths:
            role_context.append(
                {
                    "title": role.replace("_", " ").title(),
                    "detail": ", ".join(_clean_path(path) for path in clean_paths[:4]),
                }
            )

    decision = _build_decision_summary(run, report_data, confidence, review, limitations, student_issues)

    skipped_requirements = [
        item
        for item in evidence_items
        if item["kind"] == "requirement" and item["status"] == "SKIPPED" and item["required"]
    ]

    return {
        "decision": decision,
        "student_issues": student_issues[:4],
        "limitations": limitations,
        "component_cards": component_cards,
        "assessment_context": {
            "roles": role_context,
            "stages": stage_status,
            "profile": str(score_evidence.get("profile") or run.get("profile") or "Unknown"),
            "evaluation_state": ("Partially evaluated" if limitations else "Fully evaluated")
            if report_data
            else "Awaiting result",
        },
        "priority_findings": priority_findings[:6],
        "evidence_items": evidence_items,
        "debug": {
            "skipped_requirements": skipped_requirements,
            "diagnostics": diagnostics,
            "behavioural_evidence": behavioural_evidence,
            "browser_evidence": browser_evidence,
            "environment": environment,
            "llm_feedback": report_data.get("llm_feedback"),
        },
    }

@marking_bp.route("/mark", methods=["GET", "POST"])
@login_required
def mark():
    def _mark_assignment_context(include_released: bool = False) -> tuple[str, str, str, list[dict], bool]:
        user_role = session.get("user_role", "")
        view_as_role = session.get("view_as_role")
        effective_role = view_as_role or user_role
        user_id = session.get("user_id", "")
        is_preview = False

        if effective_role == "student":
            now = datetime.now().strftime("%Y-%m-%dT%H:%M")
            if user_role == "student":
                assignments = [
                    assignment
                    for assignment in list_assignments_for_student(user_id)
                    if not assignment.get("due_date") or assignment["due_date"] >= now
                ]
            else:
                is_preview = True
                assignments = [
                    assignment
                    for assignment in list_assignments()
                    if not assignment.get("due_date") or assignment["due_date"] >= now
                ]
        else:
            assignments = list_assignments() if user_role == "admin" else list_assignments(teacher_id=user_id)

        if not include_released:
            assignments = [
                assignment
                for assignment in assignments
                if not _assignment_submission_locked(assignment)
            ]
        return user_role, effective_role, user_id, assignments, is_preview

    def _render_mark_page(status_code: int = 200, selected_assignment_id: str = ""):
        github_connected = bool(session.get("github_token"))
        github_user = session.get("github_user", "")
        user_role, effective_role, user_id, assignment_options, is_preview = _mark_assignment_context()
        effective_student_id = PREVIEW_STUDENT_ID if is_preview else user_id
        student_assignments = assignment_options if effective_role == "student" else []
        teacher_assignments = assignment_options if effective_role != "student" else []
        return (
            render_template(
                "mark.html",
                profiles=PROFILE_CHOICES,
                github_connected=github_connected,
                github_user=github_user,
                user_role=user_role,
                effective_role=effective_role,
                user_id=effective_student_id,
                student_assignments=student_assignments,
                assignments=teacher_assignments,
                is_preview=is_preview,
                selected_assignment_id=selected_assignment_id,
            ),
            status_code,
        )

    if request.method == "GET":
        return _render_mark_page(selected_assignment_id=request.args.get("assignment_id", "").strip())

    # ── Sandbox enforcement ──────────────────────────────────────
    selected_assignment_id = request.form.get("assignment_id", "").strip()
    user_role, effective_role, user_id, assignment_options, is_preview = _mark_assignment_context(include_released=True)
    assignment_map = {
        str(assignment.get("assignmentID") or "").strip(): assignment
        for assignment in assignment_options
    }

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
        return _render_mark_page(status_code=503, selected_assignment_id=selected_assignment_id)

    # GitHub state (needed for error paths and template rendering)
    github_connected = bool(session.get("github_token"))
    github_user = session.get("github_user", "")

    file = request.files.get("submission")
    github_repo = request.form.get("github_repo", "").strip()
    github_branch = request.form.get("github_branch", "").strip()
    student_id = request.form.get("student_id", "").strip()
    assignment_id = request.form.get("assignment_id", "").strip()
    scoring_mode_str = request.form.get("scoring_mode", "static_plus_llm").strip()

    # ── Determine submission source (ZIP upload vs GitHub) ──
    using_github = bool(github_repo)
    tmp_zip_path: Path | None = None
    run_dir: Path | None = None
    attempt_id = ""
    attempt_number = 0

    if using_github:
        # ------ GitHub submission path ------
        github_token = session.get("github_token")
        if not github_token:
            flash("Please link your GitHub account first.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        # Validate repo format (owner/repo)
        if "/" not in github_repo or github_repo.count("/") != 1:
            flash("Invalid GitHub repository format. Use owner/repo.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        branch_suffix = f"_{github_branch}" if github_branch else ""
        original_filename = f"{github_repo.replace('/', '_')}{branch_suffix}.zip"

    else:
        # ------ ZIP upload path ------
        if not file or not file.filename:
            flash("Please upload a .zip file or select a GitHub repository.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        if not validate_file_type(file.filename):
            flash("Invalid file type. Please upload a .zip file.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        original_filename = MetadataValidator.sanitize_filename(file.filename)

    # ── Strict ZIP content validation (magic-byte check) ─────
    # Validate and convert scoring mode
    try:
        scoring_mode = ScoringMode(scoring_mode_str)
    except ValueError:
        flash(f"Invalid scoring mode: {scoring_mode_str}", "error")
        return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

    valid_assignment, assignment_error = MetadataValidator.validate_assignment_id(assignment_id)
    if not valid_assignment:
        flash(f"Invalid Assignment ID: {assignment_error}", "error")
        return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

    # Sanitize identifiers
    assignment_id = MetadataValidator.sanitize_identifier(assignment_id)
    selected_assignment_id = assignment_id
    assignment = assignment_map.get(assignment_id)
    if assignment is None:
        flash("Select a valid assignment from the list.", "error")
        return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
    if _assignment_submission_locked(assignment):
        flash(_submission_lock_message(), "error")
        return _render_mark_page(status_code=403, selected_assignment_id=selected_assignment_id)

    if effective_role == "student":
        student_id = PREVIEW_STUDENT_ID if is_preview else user_id
    else:
        student_id = request.form.get("student_id", "").strip()

    valid_student, student_error = MetadataValidator.validate_student_id(student_id)
    if not valid_student:
        flash(f"Invalid Student ID: {student_error}", "error")
        return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

    student_id = MetadataValidator.sanitize_identifier(student_id)
    profile = str(assignment.get("profile") or "frontend_interactive").strip()
    original_filename = MetadataValidator.sanitize_filename(original_filename)

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

    runs_root = get_runs_root(current_app)
    created_at = utc_now_iso()
    source_type = (
        "student_github_submission"
        if using_github and effective_role == "student"
        else "teacher_github_submission"
        if using_github
        else "student_zip_upload"
        if effective_role == "student"
        else "teacher_upload"
    )
    source_ref = github_repo if using_github else ""
    attempt = create_attempt(
        assignment_id=assignment_id,
        student_id=student_id,
        source_type=source_type,
        source_actor_user_id=str(user_id or ""),
        original_filename=original_filename,
        source_ref=source_ref,
        created_at=created_at,
        submitted_at=created_at,
    )
    attempt_id = str(attempt.get("id") or "")
    attempt_number = int(attempt.get("attempt_number") or 0)
    run_id = str(attempt.get("run_id") or attempt_id)
    run_dir = create_attempt_storage_dir(runs_root, assignment_id, student_id, attempt_number, attempt_id)
    update_attempt(attempt_id, run_dir=str(run_dir))

    initial_run_info = {
        "id": run_id,
        "mode": "mark",
        "profile": profile,
        "scoring_mode": scoring_mode.value,
        "created_at": created_at,
        "student_id": student_id,
        "assignment_id": assignment_id,
        "original_filename": original_filename,
        "source": "github" if using_github else "upload",
        "source_type": source_type,
        "source_actor_user_id": str(user_id or ""),
        "source_ref": source_ref,
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "ingestion_status": "pending",
        "pipeline_status": "pending",
        "validity_status": "pending",
        "status": "pending",
    }
    if using_github:
        initial_run_info["github_repo"] = github_repo
    save_run_info(run_dir, initial_run_info)

    try:
        if using_github:
            try:
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
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                    for chunk in gh_resp.iter_content(chunk_size=8192):
                        tmp_file.write(chunk)
                    tmp_zip_path = Path(tmp_file.name)
            except _requests.RequestException as exc:
                error_message = f"Failed to download repository from GitHub: {exc}"
                logger.warning("GitHub zipball download failed for %s: %s", github_repo, exc)
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error=error_message,
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    ingestion_status="failed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message=error_message,
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                flash(error_message, "error")
                return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
                file.save(tmp_file.name)
                tmp_zip_path = Path(tmp_file.name)

        # Validate file size
        valid_size, size_error = validate_file_size(tmp_zip_path, MAX_UPLOAD_MB)
        if not valid_size:
            error_message = size_error or "File size exceeds maximum limit."
            failed_info = dict(
                initial_run_info,
                status="failed",
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error=error_message,
            )
            save_run_info(run_dir, failed_info)
            update_attempt(
                attempt_id,
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error_message=error_message,
            )
            recompute_active_attempt(runs_root, assignment_id, student_id)
            flash(error_message)
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        if not validate_is_zipfile(tmp_zip_path):
            failed_info = dict(
                initial_run_info,
                status="failed",
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error="The uploaded file is not a valid ZIP archive.",
            )
            save_run_info(run_dir, failed_info)
            update_attempt(
                attempt_id,
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error_message="The uploaded file is not a valid ZIP archive.",
            )
            recompute_active_attempt(runs_root, assignment_id, student_id)
            flash("The uploaded file is not a valid ZIP archive.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        upload_zip = run_dir / original_filename
        shutil.copy2(tmp_zip_path, upload_zip)
        update_attempt(attempt_id, ingestion_status="stored")

        # Extract for processing
        extracted = run_dir / "uploaded_extract"
        extracted.mkdir(parents=True, exist_ok=True)
        try:
            safe_extract_zip(upload_zip, extracted, max_size_mb=MAX_UPLOAD_MB)
        except Exception as exc:
            failed_info = dict(
                initial_run_info,
                status="failed",
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error=str(exc),
            )
            save_run_info(run_dir, failed_info)
            update_attempt(
                attempt_id,
                ingestion_status="failed",
                pipeline_status="failed",
                validity_status="invalid",
                error_message=str(exc),
            )
            recompute_active_attempt(runs_root, assignment_id, student_id)
            flash(f"Failed to extract ZIP archive: {exc}", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        # ── Find true root of submission (bypassing macOS folders or zip wrappers)
        submission_root = find_submission_root(extracted)
        update_attempt(attempt_id, ingestion_status="completed")

        # ── Zero-content guard ───────────────────────────────────────
        # Reject the submission instantly if there are zero relevant web files
        SUPPORTED_EXTENSIONS = {".html", ".css", ".js", ".php", ".sql"}
        has_web_files = any(
            f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS 
            for f in submission_root.rglob("*")
        )

        if not has_web_files:
            error_message = "No web development files (HTML, CSS, JS, PHP, SQL) were found in this repository. Please select the correct repository."
            failed_info = dict(
                initial_run_info,
                status="failed",
                ingestion_status="completed",
                pipeline_status="failed",
                validity_status="invalid",
                error=error_message,
            )
            save_run_info(run_dir, failed_info)
            update_attempt(
                attempt_id,
                ingestion_status="completed",
                pipeline_status="failed",
                validity_status="invalid",
                error_message=error_message,
            )
            recompute_active_attempt(runs_root, assignment_id, student_id)
            flash("No web development files (HTML, CSS, JS, PHP, SQL) were found in this repository. Please select the correct repository.", "error")
            return _render_mark_page(status_code=400, selected_assignment_id=selected_assignment_id)

        pipeline = AssessmentPipeline(scoring_mode=scoring_mode)

        # Pass metadata to pipeline via context
        current_app.logger.debug(
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
        meta_dict.update(
            {
                "attempt_id": attempt_id,
                "attempt_number": attempt_number,
                "source_type": source_type,
                "source_actor_user_id": str(user_id or ""),
                "created_at": created_at,
                "submitted_at": created_at,
            }
        )

        def _run_mark_job() -> dict:
            """Executed in the thread pool."""
            try:
                report_path = pipeline.run(
                    submission_path=submission_root,
                    workspace_path=run_dir,
                    profile=profile,
                    metadata=meta_dict,
                )
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
                review_flags = extract_review_flags_from_report(report_data)
                score_evidence = report_data.get("score_evidence", {}) or {}
                confidence = (
                    str((score_evidence.get("confidence", {}) or {}).get("level") or "")
                    or str((report_data.get("summary", {}) or {}).get("confidence") or "")
                )
                manual_review_required = bool((score_evidence.get("review", {}) or {}).get("recommended"))
                llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
                pipeline_status = "failed" if llm_error_flagged else "completed"
                validity_status = "invalid" if llm_error_flagged else "valid"
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
                    "source_type": source_type,
                    "source_actor_user_id": str(user_id or ""),
                    "source_ref": source_ref,
                    "attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                    "ingestion_status": "completed",
                    "pipeline_status": pipeline_status,
                    "validity_status": validity_status,
                    "confidence": confidence,
                    "manual_review_required": manual_review_required,
                    "status": "llm_error" if llm_error_flagged else "completed",
                    "threat_flagged": bool(review_flags.get("threat_flagged")),
                    "threat_count": int(review_flags.get("threat_count") or 0),
                    "llm_error_flagged": llm_error_flagged,
                    "llm_error_message": review_flags.get("llm_error_message"),
                    "llm_error_messages": list(review_flags.get("llm_error_messages") or []),
                }
                if using_github:
                    run_info["github_repo"] = github_repo
                save_run_info(run_dir, run_info)
                _write_run_index_mark(run_dir, run_info, report_path)
                update_attempt(
                    attempt_id,
                    run_dir=str(run_dir),
                    report_path=str(report_path),
                    ingestion_status="completed",
                    pipeline_status=pipeline_status,
                    validity_status=validity_status,
                    overall_score=(report_data.get("scores", {}) or {}).get("overall"),
                    confidence=confidence,
                    manual_review_required=manual_review_required,
                    error_message=str(review_flags.get("llm_error_message") or ""),
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                return {"run_id": run_id}
            except Exception as exc:
                failed_info = dict(
                    initial_run_info,
                    status="failed",
                    ingestion_status="completed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error=str(exc),
                )
                save_run_info(run_dir, failed_info)
                update_attempt(
                    attempt_id,
                    run_dir=str(run_dir),
                    ingestion_status="completed",
                    pipeline_status="failed",
                    validity_status="invalid",
                    error_message=str(exc),
                )
                recompute_active_attempt(runs_root, assignment_id, student_id)
                raise

        job_id = job_manager.submit_job("single_mark", _run_mark_job)
        return jsonify({"job_id": job_id, "status": "accepted", "run_id": run_id}), 202
    finally:
        # Clean up temporary file
        try:
            if tmp_zip_path is not None:
                tmp_zip_path.unlink()
        except Exception:
            pass

def _mark_assignment_context(include_released: bool = False) -> tuple[str, str, str, list[dict], bool]:
    user_role = session.get("user_role", "")
    view_as_role = session.get("view_as_role")
    effective_role = view_as_role or user_role
    user_id = session.get("user_id", "")
    is_preview = False

    if effective_role == "student":
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        if user_role == "student":
            assignments = [
                assignment
                for assignment in list_assignments_for_student(user_id)
                if not assignment.get("due_date") or assignment["due_date"] >= now
            ]
        else:
            is_preview = True
            assignments = [
                assignment
                for assignment in list_assignments()
                if not assignment.get("due_date") or assignment["due_date"] >= now
            ]
    else:
        assignments = list_assignments() if user_role == "admin" else list_assignments(teacher_id=user_id)

    if not include_released:
        assignments = [
            assignment
            for assignment in assignments
            if not _assignment_submission_locked(assignment)
        ]
    return user_role, effective_role, user_id, assignments, is_preview

def _render_mark_page(status_code: int = 200, selected_assignment_id: str = ""):
    github_connected = bool(session.get("github_token"))
    github_user = session.get("github_user", "")
    user_role, effective_role, user_id, assignment_options, is_preview = _mark_assignment_context()
    effective_student_id = PREVIEW_STUDENT_ID if is_preview else user_id
    student_assignments = assignment_options if effective_role == "student" else []
    teacher_assignments = assignment_options if effective_role != "student" else []
    return (
        render_template(
            "mark.html",
            profiles=PROFILE_CHOICES,
            github_connected=github_connected,
            github_user=github_user,
            user_role=user_role,
            effective_role=effective_role,
            user_id=effective_student_id,
            student_assignments=student_assignments,
            assignments=teacher_assignments,
            is_preview=is_preview,
            selected_assignment_id=selected_assignment_id,
        ),
        status_code,
    )

def _safe_delete_within_run(run_dir: Path, candidate: Path | str | None) -> None:
    if not candidate:
        return
    path = Path(candidate)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    try:
        path.relative_to(run_dir.resolve())
    except Exception:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        try:
            path.unlink()
        except FileNotFoundError:
            pass

def _rerun_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)

def _build_rerun_metadata(
    *,
    student_id: object,
    assignment_id: object,
    original_filename: object,
    timestamp: object,
    source: object = "",
    github_repo: object = "",
) -> dict:
    uploader_extra: dict[str, str] = {}
    if str(source or "").strip():
        uploader_extra["source"] = str(source)
    if str(github_repo or "").strip():
        uploader_extra["github_repo"] = str(github_repo)
    metadata = SubmissionMetadata(
        student_id=MetadataValidator.sanitize_identifier(str(student_id or "unknown")),
        assignment_id=MetadataValidator.sanitize_identifier(str(assignment_id or "unknown_assignment")),
        timestamp=_rerun_timestamp(timestamp),
        original_filename=MetadataValidator.sanitize_filename(str(original_filename or "submission.zip")),
        uploader_metadata=uploader_extra,
    )
    return metadata.to_dict()

def _build_pipeline(run_info: Mapping[str, object]) -> AssessmentPipeline:
    scoring_mode_str = str(run_info.get("scoring_mode") or "static_plus_llm")
    try:
        scoring_mode = ScoringMode(scoring_mode_str)
    except ValueError:
        scoring_mode = ScoringMode("static_plus_llm")
    return AssessmentPipeline(scoring_mode=scoring_mode)

def _clear_rerun_outputs(workspace_path: Path) -> None:
    for filename in ("report.json", "report.html", "summary.txt"):
        _safe_delete_within_run(workspace_path, workspace_path / filename)
    for dirname in ("artifacts", "evaluation", "reports"):
        _safe_delete_within_run(workspace_path, workspace_path / dirname)

def _prepare_source_tree(source_root: Path, staging_root: Path) -> Path:
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    shutil.copytree(source_root, staging_root, dirs_exist_ok=True)
    return find_submission_root(staging_root)

def _prepare_zip_source(zip_path: Path, extract_root: Path) -> Path:
    if extract_root.exists():
        shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir(parents=True, exist_ok=True)
    safe_extract_zip(zip_path, extract_root, max_size_mb=MAX_UPLOAD_MB)
    return find_submission_root(extract_root)


def _runs_root_for_run_dir(run_dir: Path) -> Path:
    try:
        return run_dir.parents[2]
    except IndexError:
        try:
            return get_runs_root(current_app)
        except RuntimeError:
            return run_dir.parent

def _resolve_mark_rerun_source(run_dir: Path, run_info: Mapping[str, object]) -> Path:
    uploaded_extract = run_dir / "uploaded_extract"
    if uploaded_extract.exists():
        return find_submission_root(uploaded_extract)

    original_filename = str(run_info.get("original_filename") or "").strip()
    if original_filename:
        upload_zip = run_dir / original_filename
        if upload_zip.exists():
            return _prepare_zip_source(upload_zip, run_dir / "rerun_source")

    zip_candidates = list(run_dir.glob("*.zip"))
    if zip_candidates:
        return _prepare_zip_source(zip_candidates[0], run_dir / "rerun_source")

    submission_root = run_dir / "submission"
    if submission_root.exists():
        return _prepare_source_tree(submission_root, run_dir / "rerun_source")

    raise FileNotFoundError("Stored submission content is unavailable.")

def _rerun_mark_submission(run_dir: Path, run_info: Mapping[str, object]) -> dict:
    submission_source = _resolve_mark_rerun_source(run_dir, run_info)
    metadata = _build_rerun_metadata(
        student_id=run_info.get("student_id"),
        assignment_id=run_info.get("assignment_id"),
        original_filename=run_info.get("original_filename"),
        timestamp=run_info.get("created_at"),
        source=run_info.get("source"),
        github_repo=run_info.get("github_repo"),
    )
    _clear_rerun_outputs(run_dir)
    try:
        report_path = _build_pipeline(run_info).run(
            submission_path=submission_source,
            workspace_path=run_dir,
            profile=str(run_info.get("profile") or "frontend"),
            metadata=metadata,
            skip_threat_scan=True,
        )
    except Exception as exc:
        failed_run_info = dict(run_info)
        failed_run_info["status"] = "failed"
        failed_run_info["error"] = str(exc)
        failed_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        failed_run_info.pop("rerun_pending", None)
        save_run_info(run_dir, failed_run_info)
        raise
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    review_flags = extract_review_flags_from_report(report_data)
    llm_error_flagged = bool(review_flags.get("llm_error_flagged"))
    updated_run_info = dict(run_info)
    updated_run_info["report"] = report_path.name
    updated_run_info["summary"] = "summary.txt"
    updated_run_info["status"] = "llm_error" if llm_error_flagged else "completed"
    updated_run_info["pipeline_status"] = "failed" if llm_error_flagged else "completed"
    updated_run_info["validity_status"] = "invalid" if llm_error_flagged else "valid"
    updated_run_info["threat_flagged"] = bool(review_flags.get("threat_flagged"))
    if review_flags.get("threat_count"):
        updated_run_info["threat_count"] = int(review_flags.get("threat_count") or 0)
    else:
        updated_run_info.pop("threat_count", None)
    updated_run_info["llm_error_flagged"] = llm_error_flagged
    updated_run_info["llm_error_message"] = review_flags.get("llm_error_message")
    updated_run_info["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
    updated_run_info["last_rerun_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    updated_run_info.pop("rerun_pending", None)
    updated_run_info.pop("error", None)
    updated_run_info.pop("threat_override", None)
    updated_run_info.pop("threat_override_at", None)
    save_run_info(run_dir, updated_run_info)
    _write_run_index_mark(run_dir, updated_run_info, report_path)
    runs_root = _runs_root_for_run_dir(run_dir)
    assignment_id = str(updated_run_info.get("assignment_id") or "")
    student_id = str(updated_run_info.get("student_id") or "")
    if assignment_id and student_id:
        sync_attempts_from_storage(runs_root)
        recompute_active_attempt(runs_root, assignment_id, student_id)
    return updated_run_info

def _is_async_job_request() -> bool:
    return request.headers.get("X-AMS-Async") == "1"

def _build_rerun_job_response(
    *,
    job_id: str,
    run_id: str,
    label: str,
    assignment_id: str,
    view_url: str,
    refresh_url: str,
):
    payload = {
        "job_id": job_id,
        "status": "accepted",
        "run_id": run_id,
        "assignment_id": assignment_id,
        "label": label,
        "view_url": view_url,
        "refresh_url": refresh_url,
    }
    if _is_async_job_request():
        return jsonify(payload), 202
    flash(f"{label} queued. The submission will update when background processing finishes.", "success")
    return redirect(refresh_url)

def _queue_mark_submission_rerun(
    run_dir: Path,
    run_info: Mapping[str, object],
    *,
    view_url: str,
    refresh_url: str,
):
    current_status = str(run_info.get("status") or "").strip().lower()
    if current_status == "pending":
        raise RuntimeError("Submission is already queued for rerun.")

    queued_run_info = dict(run_info)
    if "threat_flagged" not in queued_run_info or "llm_error_flagged" not in queued_run_info:
        report_path = run_dir / str(run_info.get("report") or "report.json")
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                review_flags = extract_review_flags_from_report(report)
                queued_run_info["threat_flagged"] = bool(review_flags.get("threat_flagged"))
                queued_run_info["llm_error_flagged"] = bool(review_flags.get("llm_error_flagged"))
                queued_run_info["llm_error_message"] = review_flags.get("llm_error_message")
                queued_run_info["llm_error_messages"] = list(review_flags.get("llm_error_messages") or [])
            except Exception:
                pass
    queued_run_info["status"] = "pending"
    queued_run_info["rerun_pending"] = True
    queued_run_info["last_rerun_requested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    queued_run_info.pop("error", None)
    save_run_info(run_dir, queued_run_info)

    def _run_mark_rerun_job() -> dict:
        updated = _rerun_mark_submission(run_dir, queued_run_info)
        return {
            "run_id": str(updated.get("id") or run_dir.name),
            "assignment_id": str(updated.get("assignment_id") or ""),
            "view_url": view_url,
            "refresh_url": refresh_url,
        }

    job_id = job_manager.submit_job("submission_rerun", _run_mark_rerun_job)
    assignment_id = str(queued_run_info.get("assignment_id") or "")
    label = f"Rerun: {queued_run_info.get('student_id') or run_dir.name}"
    return _build_rerun_job_response(
        job_id=job_id,
        run_id=str(queued_run_info.get("id") or run_dir.name),
        label=label,
        assignment_id=assignment_id,
        view_url=view_url,
        refresh_url=refresh_url,
    )
