"""Teacher assignment management and analytics routes."""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from ams.analytics import generate_assignment_analytics
from ams.core.db import (
    assignment_teacher_ids,
    create_assignment,
    delete_assignment,
    get_assignment,
    get_user,
    list_users,
    release_marks,
    update_assignment_students,
    update_assignment_teachers,
    withhold_marks,
)
from ams.io.web_storage import get_runs_root, purge_assignment_storage
from ams.web.auth import get_current_user, teacher_or_admin_required
from ams.web.routes_teacher import (
    _build_assignment_run_rows,
    _build_assignment_submission_groups,
    _build_llm_error_resolution_rows,
    _build_threat_resolution_rows,
    _filtered_needs_attention_rows,
    _format_freshness_label,
    _teacher_user_lookup,
    _user_can_access_assignment,
)
from ams.web.routes_teacher_helpers import (
    _csv_response,
    _filtered_top_rule_rows,
    _json_response,
    _llm_summary_enabled,
    _maybe_enhance_teaching_insights,
    _normalize_teaching_insights,
    _pdf_response,
    _txt_response,
)
from ams.web.route_helpers import load_accessible_assignment_or_json, load_accessible_assignment_or_redirect

logger = logging.getLogger(__name__)

assignment_mgmt_bp = Blueprint("assignment_mgmt", __name__, url_prefix="/teacher")


@assignment_mgmt_bp.route("/create-assignment", methods=["GET", "POST"])
@teacher_or_admin_required
# Create a new assignment.
def create_assignment_route():
    if request.method == "GET":
        students = list_users(role="student")
        teachers = list_users(role="teacher")
        return render_template("teacher/create_assignment.html", students=students, teachers=teachers)

    user = get_current_user()
    assignment_id = request.form.get("assignment_id", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    profile = request.form.get("profile", "frontend_interactive").strip()
    due_date = request.form.get("due_date", "").strip()
    selected_students = request.form.getlist("students")
    valid_teacher_ids = {teacher["userID"] for teacher in list_users(role="teacher")}
    selected_teachers = [
        teacher_id
        for teacher_id in request.form.getlist("teachers")
        if teacher_id in valid_teacher_ids and teacher_id != user["userID"]
    ]

    if not assignment_id or not title:
        flash("Assignment ID and Title are required.", "error")
        return redirect(url_for("teacher_dashboard.dashboard"))

    ok = create_assignment(
        assignment_id=assignment_id,
        teacher_id=user["userID"],
        title=title,
        description=description,
        profile=profile,
        assigned_students=selected_students,
        assigned_teachers=selected_teachers,
        due_date=due_date,
    )
    if ok:
        flash(f"Assignment '{assignment_id}' created successfully.", "success")
    else:
        flash(f"Assignment ID '{assignment_id}' already exists.", "error")
    return redirect(url_for("teacher_dashboard.dashboard"))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/students", methods=["POST"])
@teacher_or_admin_required
# Update the student list for one assignment.
def update_students(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    selected = request.form.getlist("students")
    update_assignment_students(assignment_id, selected)
    flash(f"Student list updated for '{assignment_id}'.", "success")
    return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/teachers", methods=["POST"])
@teacher_or_admin_required
# Update the teacher list for one assignment.
def update_teachers(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    valid_teacher_ids = {teacher["userID"] for teacher in list_users(role="teacher")}
    selected_teachers = [
        teacher_id
        for teacher_id in request.form.getlist("teachers")
        if teacher_id in valid_teacher_ids and teacher_id != assignment.get("teacherID")
    ]
    update_assignment_teachers(assignment_id, selected_teachers)
    flash(f"Teaching team updated for '{assignment_id}'.", "success")
    return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))


@assignment_mgmt_bp.route("/assignment/<assignment_id>")
@teacher_or_admin_required
# Show the detail page for one assignment.
def assignment_detail(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    student_details = []
    for sid in assignment.get("assigned_students", []):
        user = get_user(sid)
        if user:
            student_details.append(user)
        else:
            student_details.append({"userID": sid, "firstName": sid, "lastName": "", "email": ""})

    teacher_details = [_teacher_user_lookup(teacher_id) for teacher_id in assignment_teacher_ids(assignment)]
    all_students = list_users(role="student")
    all_teachers = [
        teacher
        for teacher in list_users(role="teacher")
        if teacher["userID"] != assignment.get("teacherID")
    ]
    assignment_runs = _build_assignment_run_rows(assignment_id)
    submission_groups = _build_assignment_submission_groups(assignment_runs)
    threat_rows = _build_threat_resolution_rows(assignment_runs)
    llm_error_rows = _build_llm_error_resolution_rows(assignment_runs)

    return render_template(
        "teacher/assignment_detail.html",
        assignment=assignment,
        student_details=student_details,
        teacher_details=teacher_details,
        all_teachers=all_teachers,
        all_students=all_students,
        runs=assignment_runs,
        submission_groups=submission_groups,
        threat_rows=threat_rows,
        llm_error_rows=llm_error_rows,
        has_unresolved_threats=bool(threat_rows),
        has_unresolved_llm_errors=bool(llm_error_rows),
        has_release_blockers=bool(threat_rows or llm_error_rows),
    )


@assignment_mgmt_bp.route("/assignment/<assignment_id>/release", methods=["POST"])
@teacher_or_admin_required
# Release marks for one assignment.
def release(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    assignment_runs = _build_assignment_run_rows(assignment_id)
    threat_rows = _build_threat_resolution_rows(assignment_runs)
    llm_error_rows = _build_llm_error_resolution_rows(assignment_runs)
    if threat_rows or llm_error_rows:
        flash(
            "Grades cannot be released while flagged submissions remain. "
            "Resolve all threat-detected or LLM-error submissions first.",
            "error",
        )
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))
    release_marks(assignment_id)
    flash(f"Marks released for '{assignment_id}'.", "success")
    return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/withhold", methods=["POST"])
@teacher_or_admin_required
# Withhold marks for one assignment.
def withhold(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    withhold_marks(assignment_id)
    flash(f"Marks withheld for '{assignment_id}'.", "info")
    return redirect(url_for("teacher_dashboard.dashboard"))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/analytics")
@teacher_or_admin_required
# Show the assignment analytics page.
def view_analytics(assignment_id: str):
    """Render fresh analytics for a single assignment."""
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics generation failed for %s: %s", assignment_id, exc)
        flash(f"Analytics generation failed: {exc}", "error")
        return redirect(url_for("assignment_mgmt.assignment_detail", assignment_id=assignment_id))

    coverage = dict(analytics.get("coverage", {}) or {})
    assigned_count = int(coverage.get("assigned_students") or len(assignment.get("assigned_students", []) or []))
    submitted_count = int(
        coverage.get("active_in_scope")
        or analytics.get("submission_count")
        or analytics.get("overall", {}).get("total")
        or 0
    )
    missing_count = int(coverage.get("missing_assigned") or max(assigned_count - submitted_count, 0))
    coverage_percent = int(
        coverage.get("coverage_percent") or (round((submitted_count / assigned_count) * 100) if assigned_count else 0)
    )
    updated_label, updated_exact = _format_freshness_label(analytics.get("generated_at"))
    teaching_insights = _normalize_teaching_insights(analytics.get("teaching_insights"))
    teaching_summary_source = "deterministic"

    return render_template(
        "teacher/assignment_analytics.html",
        assignment=assignment,
        analytics=analytics,
        teaching_insights=teaching_insights,
        teaching_summary_source=teaching_summary_source,
        assigned_count=assigned_count,
        submitted_count=submitted_count,
        missing_count=missing_count,
        coverage_percent=coverage_percent,
        updated_label=updated_label,
        updated_exact=updated_exact,
        llm_summary_enabled=_llm_summary_enabled(),
    )


@assignment_mgmt_bp.route("/assignment/<assignment_id>/analytics/teaching-insights.json")
@teacher_or_admin_required
# Return the teaching insights JSON payload.
def teaching_insights_json(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_json(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Teaching insight generation failed for %s: %s", assignment_id, exc)
        return jsonify({"error": str(exc)}), 500

    summary, source, meta = _maybe_enhance_teaching_insights(analytics)
    payload = {
        "assignment_id": assignment_id,
        "source": source,
        "summary_mode": summary.get("summary_mode", "deterministic"),
        "headline": summary.get("headline", ""),
        "insights": summary.get("insights", []),
    }
    payload.update(meta)
    return jsonify(payload)


@assignment_mgmt_bp.route("/assignment/<assignment_id>/analytics/export/<export_kind>.csv")
@teacher_or_admin_required
# Export assignment analytics rows as CSV.
def export_analytics_csv(assignment_id: str, export_kind: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics export failed for %s: %s", assignment_id, exc)
        flash(f"Analytics export failed: {exc}", "error")
        return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))

    if export_kind == "needs-attention":
        rows = _filtered_needs_attention_rows(analytics, request.args)
        export_rows = [
            {
                "student_id": row.get("student_id", ""),
                "submission_id": row.get("submission_id", ""),
                "severity": row.get("severity", ""),
                "score_percent": round(float(row.get("overall", 0) or 0) * 100, 2) if row.get("overall") is not None else "",
                "grade": row.get("grade", ""),
                "confidence": row.get("confidence", ""),
                "evaluation_state": row.get("evaluation_state", ""),
                "reason": row.get("reason", ""),
                "reason_detail": row.get("reason_detail", ""),
                "flags": "; ".join(row.get("flags", []) or []),
                "related_rule_ids": "; ".join(row.get("matched_rule_ids", []) or []),
                "limitation_details": "; ".join(row.get("limitation_details", []) or []),
                "evidence_excerpt": row.get("evidence_excerpt", ""),
                "manual_review_recommended": "yes" if row.get("manual_review_recommended") else "no",
                "review_note": row.get("review_note", ""),
            }
            for row in rows
        ]
        return _csv_response(
            f"{assignment_id}_needs_attention.csv",
            [
                "student_id",
                "submission_id",
                "severity",
                "score_percent",
                "grade",
                "confidence",
                "evaluation_state",
                "reason",
                "reason_detail",
                "flags",
                "related_rule_ids",
                "limitation_details",
                "evidence_excerpt",
                "manual_review_recommended",
                "review_note",
            ],
            export_rows,
        )

    if export_kind == "rules":
        rows = _filtered_top_rule_rows(analytics, request.args)
        export_rows = [
            {
                "rule_id": row.get("rule_id", ""),
                "label": row.get("label", ""),
                "component": row.get("component", ""),
                "severity": row.get("severity", ""),
                "students_affected": row.get("students_affected", 0),
                "percent_of_active_submissions": round(float(row.get("percent", 0) or 0), 2),
                "incident_count": row.get("incident_count", 0),
                "fail_incidents": row.get("fail_incidents", 0),
                "warning_incidents": row.get("warning_incidents", 0),
                "impact_type": row.get("impact_type", ""),
                "score_impact": row.get("score_impact", ""),
                "example_students": "; ".join(row.get("examples", []) or []),
                "messages": "; ".join(row.get("messages", []) or []),
            }
            for row in rows
        ]
        return _csv_response(
            f"{assignment_id}_top_failing_rules.csv",
            [
                "rule_id",
                "label",
                "component",
                "severity",
                "students_affected",
                "percent_of_active_submissions",
                "incident_count",
                "fail_incidents",
                "warning_incidents",
                "impact_type",
                "score_impact",
                "example_students",
                "messages",
            ],
            export_rows,
        )

    flash("Unknown analytics export.", "error")
    return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/analytics/export/<export_kind>/<format>")
@teacher_or_admin_required
# Export assignment analytics in the requested format.
def export_analytics(assignment_id: str, export_kind: str, format: str):
    """Export analytics in various formats (csv, json, txt, pdf)."""
    if format not in ("csv", "json", "txt", "pdf"):
        flash("Invalid export format.", "error")
        return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))

    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    try:
        analytics = generate_assignment_analytics(assignment_id, app=current_app)
    except Exception as exc:
        logger.warning("Analytics export failed for %s: %s", assignment_id, exc)
        flash(f"Analytics export failed: {exc}", "error")
        return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))

    if export_kind == "needs-attention":
        rows = _filtered_needs_attention_rows(analytics, request.args)
        fieldnames = [
            "student_id",
            "submission_id",
            "severity",
            "score_percent",
            "grade",
            "confidence",
            "evaluation_state",
            "reason",
            "reason_detail",
            "flags",
            "related_rule_ids",
            "limitation_details",
            "evidence_excerpt",
            "manual_review_recommended",
            "review_note",
        ]
        export_rows = [
            {
                "student_id": row.get("student_id", ""),
                "submission_id": row.get("submission_id", ""),
                "severity": row.get("severity", ""),
                "score_percent": round(float(row.get("overall", 0) or 0) * 100, 2) if row.get("overall") is not None else "",
                "grade": row.get("grade", ""),
                "confidence": row.get("confidence", ""),
                "evaluation_state": row.get("evaluation_state", ""),
                "reason": row.get("reason", ""),
                "reason_detail": row.get("reason_detail", ""),
                "flags": "; ".join(row.get("flags", []) or []),
                "related_rule_ids": "; ".join(row.get("matched_rule_ids", []) or []),
                "limitation_details": "; ".join(row.get("limitation_details", []) or []),
                "evidence_excerpt": row.get("evidence_excerpt", ""),
                "manual_review_recommended": "yes" if row.get("manual_review_recommended") else "no",
                "review_note": row.get("review_note", ""),
            }
            for row in rows
        ]
        title = f"Review Queue - {assignment_id}"
        base_name = f"{assignment_id}_needs_attention"
    elif export_kind == "rules":
        rows = _filtered_top_rule_rows(analytics, request.args)
        fieldnames = [
            "rule_id",
            "label",
            "component",
            "severity",
            "students_affected",
            "percent_of_active_submissions",
            "incident_count",
            "fail_incidents",
            "warning_incidents",
            "impact_type",
            "score_impact",
            "example_students",
            "messages",
        ]
        export_rows = [
            {
                "rule_id": row.get("rule_id", ""),
                "label": row.get("label", ""),
                "component": row.get("component", ""),
                "severity": row.get("severity", ""),
                "students_affected": row.get("students_affected", 0),
                "percent_of_active_submissions": round(float(row.get("percent", 0) or 0), 2),
                "incident_count": row.get("incident_count", 0),
                "fail_incidents": row.get("fail_incidents", 0),
                "warning_incidents": row.get("warning_incidents", 0),
                "impact_type": row.get("impact_type", ""),
                "score_impact": row.get("score_impact", ""),
                "example_students": "; ".join(row.get("examples", []) or []),
                "messages": "; ".join(row.get("messages", []) or []),
            }
            for row in rows
        ]
        title = f"Rule Summary - {assignment_id}"
        base_name = f"{assignment_id}_top_failing_rules"
    else:
        flash("Unknown analytics export type.", "error")
        return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))

    if format == "csv":
        return _csv_response(f"{base_name}.csv", fieldnames, export_rows)
    if format == "json":
        return _json_response(f"{base_name}.json", export_rows)
    if format == "txt":
        return _txt_response(f"{base_name}.txt", title, fieldnames, export_rows)
    if format == "pdf":
        return _pdf_response(f"{base_name}.pdf", title, fieldnames, export_rows)

    flash("Invalid export format.", "error")
    return redirect(url_for("assignment_mgmt.view_analytics", assignment_id=assignment_id))


@assignment_mgmt_bp.route("/assignment/<assignment_id>/delete", methods=["POST"])
@teacher_or_admin_required
# Delete one assignment.
def delete_assignment_route(assignment_id: str):
    assignment, error_response = load_accessible_assignment_or_redirect(
        assignment_id,
        access_checker=_user_can_access_assignment,
        loader=get_assignment,
    )
    if error_response is not None:
        return error_response

    if delete_assignment(assignment_id):
        removed_count = purge_assignment_storage(get_runs_root(current_app), assignment_id)
        flash(
            f"Assignment '{assignment_id}' deleted and {removed_count} stored run artefact(s) removed.",
            "success",
        )
    else:
        flash(f"Could not delete assignment '{assignment_id}'.", "error")
    return redirect(url_for("teacher_dashboard.dashboard"))
