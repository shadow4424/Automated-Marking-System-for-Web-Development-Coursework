from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from ams.analytics.assignment_analytics import generate_assignment_analytics, generate_student_assignment_analytics
from ams.core.database import init_db
from ams.io.web_storage import save_run_info
from ams.web.routes_student import _deterministic_student_feedback
from ams.webui import create_app
from tests.webui.conftest import (
    _make_report,
    _rich_teaching_insight_context,
    _use_temp_db,
    _valid_llm_teacher_summary,
    _write_batch_run,
    _write_failed_mark_run,
    _write_invalid_batch_run,
    _write_json,
    _write_mark_run,
    authenticate_client,
)


def test_assignment_analytics_uses_all_active_submissions_for_assignment(tmp_path: Path, monkeypatch) -> None:
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-090000_mark_frontend_old",
        created_at="2026-03-19T09:00:00Z",
        score=0.2,
    )
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-100000_mark_frontend_new",
        created_at="2026-03-19T10:00:00Z",
        score=0.9,
    )
    _write_batch_run(
        tmp_path,
        assignment_id="assignment1",
        run_id="20260319-110000_batch_frontend_live",
        created_at="2026-03-19T11:00:00Z",
        students=[("student2", 0.4), ("student3", 1.0)],
    )
    _write_mark_run(
        tmp_path,
        assignment_id="assignment2",
        student_id="student9",
        run_id="20260319-120000_mark_frontend_other",
        created_at="2026-03-19T12:00:00Z",
        score=0.1,
    )

    monkeypatch.setattr(
        "ams.analytics.assignment_analytics.get_assignment",
        lambda assignment_id: {"assignmentID": assignment_id, "profile": "frontend"},
    )

    app = Flask(__name__)
    app.config["AMS_RUNS_ROOT"] = tmp_path

    with app.app_context():
        analytics = generate_assignment_analytics("assignment1", app=app)

    assert analytics["assignment_id"] == "assignment1"
    assert analytics["submission_count"] == 3
    assert analytics["overall"]["total"] == 3
    assert analytics["overall"]["buckets"]["Full marks (100%)"] == 1
    assert analytics["overall"]["buckets"]["Partial (1-50%)"] == 1
    assert analytics["overall"]["buckets"]["Good partial (51-99%)"] == 1
    assert analytics["overall"]["mean"] == pytest.approx(0.7666666666666667)
    assert {entry["student_id"] for entry in analytics["needs_attention"]} == {"student2"}

def test_assignment_analytics_generates_interactive_graph_payloads_from_latest_active_submissions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-090000_mark_frontend_old",
        created_at="2026-03-19T09:00:00Z",
        score=0.2,
    )
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-100000_mark_frontend_new",
        created_at="2026-03-19T10:00:00Z",
        score=0.9,
    )
    _write_batch_run(
        tmp_path,
        assignment_id="assignment1",
        run_id="20260319-110000_batch_frontend_live",
        created_at="2026-03-19T11:00:00Z",
        students=[("student2", 0.4), ("student3", 1.0)],
    )

    monkeypatch.setattr(
        "ams.analytics.assignment_analytics.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "profile": "frontend",
            "assigned_students": ["student1", "student2", "student3", "student4"],
        },
    )

    app = Flask(__name__)
    app.config["AMS_RUNS_ROOT"] = tmp_path

    with app.app_context():
        analytics = generate_assignment_analytics("assignment1", app=app)

    graphs = analytics["interactive_graphs"]
    histogram_bins = {entry["label"]: entry for entry in graphs["mark_distribution_histogram"]["bins"]}
    assert graphs["mark_distribution_histogram"]["bin_width"] == 10
    assert histogram_bins["20-30%"]["count"] == 0
    assert histogram_bins["90-100%"]["student_ids"] == ["student1", "student3"]
    assert graphs["static_functional_scatter_plot"]["supported"] is True
    assert graphs["static_functional_scatter_plot"]["cohort_count"] == 3
    assert len(graphs["static_functional_scatter_plot"]["points"]) == 3
    assert graphs["missing_incomplete_submission_coverage_chart"]["stages"][0]["count"] == 4
    assert "student4" in graphs["missing_incomplete_submission_coverage_chart"]["stages"][2]["student_ids"]
    component_rows = {row["component"]: row for row in graphs["component_performance_distribution"]["components"]}
    assert component_rows["html"]["segments"][3]["count"] == 2
    assert component_rows["html"]["segments"][2]["count"] == 1
    context = analytics["teaching_insight_context"]
    assert context["average_score"] == pytest.approx(76.67, abs=0.01)
    assert context["dominant_score_band"] is None
    assert "requirement_coverage_summary" in context
    assert "component_performance_summary" in context
    assert "top_failing_rules" in context
    assert "major_rule_categories" in context
    assert "confidence_mix" in context
    assert "runtime_skip_count" in context
    assert "static_vs_behavioural_mismatch" in context
    assert "high_priority_flagged_submissions" in context

def test_assignment_analytics_export_uses_fresh_assignment_scoped_data(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "generated_at": "2026-03-20T01:14:00Z",
            "needs_attention": [
                {
                    "student_id": "student1",
                    "submission_id": "student1_assignment1",
                    "severity": "high",
                    "overall": 0.4,
                    "grade": "poor",
                    "confidence": "medium",
                    "evaluation_state": "partially_evaluated",
                    "reason": "browser runtime issue",
                    "reason_detail": "Page load failed",
                    "flags": ["browser issue"],
                    "matched_rule_ids": ["BROWSER.PAGE_LOAD_FAIL"],
                    "manual_review_recommended": True,
                    "review_note": "Check the browser report before moderation.",
                    "sort_overall": 0.4,
                    "sort_grade": 2,
                }
            ],
            "top_failing_rules": [
                {
                    "rule_id": "BROWSER.PAGE_LOAD_FAIL",
                    "label": "Browser page load failed",
                    "component": "html",
                    "severity": "FAIL",
                    "students_affected": 1,
                    "percent": 100,
                    "score_impact": "Fail-level issue",
                    "examples": ["student1"],
                    "messages": ["Browser could not load the page."],
                }
            ],
        },
    )

    response = client.get("/teacher/assignment/assignment1/analytics/export/needs-attention.csv")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"student_id,submission_id,severity,score_percent" in response.data
    assert b"student1,student1_assignment1,high,40.0" in response.data

def test_assignment_detail_has_single_analytics_entry_point(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "description": "Build a site",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
            "due_date": "",
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_user",
        lambda student_id: {"userID": student_id, "firstName": "Test", "lastName": "Student", "email": "s@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", lambda role=None: [])
    monkeypatch.setattr("ams.web.routes_teacher.get_runs_root", lambda app: tmp_path)
    monkeypatch.setattr("ams.web.routes_teacher.list_runs", lambda runs_root, only_active=True: [])

    response = client.get("/teacher/assignment/assignment1")

    assert response.status_code == 200
    assert response.data.count(b">Analytics<") == 1
    assert b"View Analytics" not in response.data
    assert b"Regenerate Analytics" not in response.data

def test_assignment_detail_keeps_valid_submissions_visible_when_invalid_batch_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-090000_mark_frontend_student1",
        created_at="2026-03-19T09:00:00Z",
        score=0.67,
    )
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student2",
        run_id="20260319-091500_mark_frontend_student2",
        created_at="2026-03-19T09:15:00Z",
        score=0.51,
    )
    _write_invalid_batch_run(
        tmp_path,
        run_assignment_id="Assignment1",
        submission_assignment_id="assignment1",
        run_id="20260320-100000_batch_frontend_invalid",
        created_at="2026-03-20T10:00:00Z",
        students=["student1", "student2"],
    )

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "description": "Build a site",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
            "due_date": "",
            "teacherID": "admin123",
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_user",
        lambda student_id: {
            "userID": student_id,
            "firstName": student_id,
            "lastName": "",
            "email": f"{student_id}@example.com",
        },
    )
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", lambda role=None: [])

    response = client.get("/teacher/assignment/assignment1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No submissions yet" not in body
    assert "student1" in body
    assert "student2" in body

def test_assignment_analytics_page_renders_interactive_graph_sections(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "generated_at": "2026-03-20T01:14:00Z",
            "submission_count": 2,
            "overall": {
                "total": 2,
                "mean": 0.75,
                "median": 0.75,
                "min": 0.5,
                "max": 1.0,
                "buckets": {
                    "No attempt (0%)": 0,
                    "Partial (1-50%)": 1,
                    "Good partial (51-99%)": 0,
                    "Full marks (100%)": 1,
                },
            },
            "coverage": {
                "assigned_students": 2,
                "assigned_student_ids": ["student1", "student2"],
                "active_in_scope": 2,
                "active_students": 2,
                "active_student_ids": ["student1", "student2"],
                "missing_assigned": 0,
                "missing_students": [],
                "fully_evaluated": 1,
                "partially_evaluated": 1,
                "not_analysable": 0,
                "inactive_or_superseded": 0,
                "inactive_or_superseded_students": [],
                "coverage_percent": 100,
            },
            "reliability": {
                "fully_evaluated": 1,
                "fully_evaluated_submissions": 1,
                "partially_evaluated": 1,
                "partially_evaluated_submissions": 1,
                "not_analysable": 0,
                "not_analysable_submissions": 0,
                "manual_review": 1,
                "manual_review_submissions": 1,
                "runtime_skipped": 1,
                "runtime_issue_submissions": 0,
                "browser_skipped": 0,
                "browser_issue_submissions": 0,
                "limitation_incidents": 1,
                "limitation_categories": 1,
                "limitation_breakdown": [{"id": "runtime_skipped", "label": "Runtime checks skipped", "incident_count": 1}],
                "confidence": {"high": 1, "medium": 1, "low": 0},
            },
            "components": [],
            "needs_attention": [
                {
                    "student_id": "student2",
                    "submission_id": "student2_assignment1",
                    "severity": "medium",
                    "overall": 0.5,
                    "grade": "partial",
                    "confidence": "medium",
                    "evaluation_state": "partially_evaluated",
                    "reason": "reduced evaluation confidence",
                    "reason_detail": "Runtime checks were skipped.",
                    "flags": ["runtime checks skipped"],
                    "matched_rule_ids": ["HTML.REQ.FAIL"],
                    "manual_review_recommended": True,
                    "review_note": "Check the report before release.",
                    "run_id": "run-2",
                    "source_mode": "mark",
                    "sort_overall": 0.5,
                    "sort_grade": 3,
                }
            ],
            "signals": [],
            "top_failing_rules": [
                {
                    "rule_id": "HTML.REQ.FAIL",
                    "label": "HTML requirement failed",
                    "component": "html",
                    "severity": "FAIL",
                    "students_affected": 1,
                    "submissions_affected": 1,
                    "percent": 50,
                    "incident_count": 1,
                    "fail_incidents": 1,
                    "warning_incidents": 0,
                    "impact_type": "fail_level",
                    "score_impact": "Fail-level issue",
                    "affected_students": ["student2"],
                    "messages": ["Required HTML block missing."],
                }
            ],
            "requirement_coverage": [
                {
                    "component": "html",
                    "title": "Required HTML structure",
                    "rule_count": 2,
                    "students_met": 1,
                    "students_partial": 0,
                    "students_unmet": 1,
                    "students_not_evaluable": 0,
                    "met_percent": 50,
                    "met_students": ["student1"],
                    "partial_students": [],
                    "unmet_students": ["student2"],
                    "not_evaluable_students": [],
                }
            ],
            "score_composition": [],
            "teaching_insights": [],
            "interactive_graphs": {
                "student_index": {
                    "student1": {
                        "student_id": "student1",
                        "submission_id": "student1_assignment1",
                        "score_percent": 100,
                        "grade": "full marks",
                        "confidence": "high",
                        "evaluation_state": "fully_evaluated",
                        "severity": "low",
                        "manual_review_recommended": False,
                        "reason": "other",
                        "reason_detail": "No issues.",
                        "flags": [],
                        "matched_rule_ids": [],
                        "matched_rule_labels": [],
                        "run_id": "run-1",
                        "source_mode": "mark",
                    },
                    "student2": {
                        "student_id": "student2",
                        "submission_id": "student2_assignment1",
                        "score_percent": 50,
                        "grade": "partial",
                        "confidence": "medium",
                        "evaluation_state": "partially_evaluated",
                        "severity": "medium",
                        "manual_review_recommended": True,
                        "reason": "reduced evaluation confidence",
                        "reason_detail": "Runtime checks were skipped.",
                        "flags": ["runtime checks skipped"],
                        "matched_rule_ids": ["HTML.REQ.FAIL"],
                        "matched_rule_labels": ["HTML requirement failed"],
                        "run_id": "run-2",
                        "source_mode": "mark",
                    },
                },
                "mark_distribution_histogram": {
                    "total_students": 2,
                    "unscored_submissions": 0,
                    "reference_lines": {"mean_percent": 75, "median_percent": 75, "pass_threshold_percent": 50},
                    "bins": [{"label": "50-59%", "count": 1, "percent": 50, "student_ids": ["student2"]}],
                },
                "component_performance_distribution": {
                    "components": [
                        {
                            "component": "html",
                            "title": "Required HTML structure",
                            "average_percent": 75,
                            "segments": [
                                {"label": "Score 0", "count": 0, "percent": 0, "student_ids": []},
                                {"label": "Score 0.5", "count": 1, "percent": 50, "student_ids": ["student2"]},
                                {"label": "Score 1", "count": 1, "percent": 50, "student_ids": ["student1"]},
                            ],
                        }
                    ]
                },
                "requirement_coverage_matrix": {
                    "rows": [
                        {
                            "component": "html",
                            "title": "Required HTML structure",
                            "rule_count": 2,
                            "cells": [
                                {"label": "Met", "count": 1, "percent": 50, "student_ids": ["student1"]},
                                {"label": "Partial", "count": 0, "percent": 0, "student_ids": []},
                                {"label": "Unmet", "count": 1, "percent": 50, "student_ids": ["student2"]},
                                {"label": "Not evaluable", "count": 0, "percent": 0, "student_ids": []},
                            ],
                        }
                    ]
                },
                "confidence_reliability_breakdown": {
                    "groups": [
                        {
                            "label": "Evaluation state",
                            "segments": [
                                {"id": "fully_evaluated", "label": "Fully evaluated", "count": 1, "percent": 50, "student_ids": ["student1"]},
                                {"id": "partially_evaluated", "label": "Partially evaluated", "count": 1, "percent": 50, "student_ids": ["student2"]},
                            ],
                        }
                    ],
                    "limitation_rows": [
                        {"id": "runtime_skipped", "label": "Runtime checks skipped or unavailable", "count": 1, "percent": 50, "student_ids": ["student2"]}
                    ],
                },
                "static_functional_scatter_plot": {
                    "supported": True,
                    "unsupported_reason": "",
                    "cohort_count": 2,
                    "reference_lines": {
                        "static_mean_percent": 75,
                        "behavioural_mean_percent": 70,
                        "show_mean_lines": False,
                        "show_balance_diagonal": True,
                    },
                    "points": [
                        {
                            "student_id": "student1",
                            "student_name": "student1",
                            "student_ids": ["student1"],
                            "overall_mark_percent": 100,
                            "static_score_percent": 96,
                            "behavioural_score_percent": 94,
                            "manual_review_recommended": False,
                            "confidence": "high",
                            "severity": "low",
                            "primary_issue": "No issues.",
                        },
                        {
                            "student_id": "student2",
                            "student_name": "student2",
                            "student_ids": ["student2"],
                            "overall_mark_percent": 50,
                            "static_score_percent": 72,
                            "behavioural_score_percent": 38,
                            "manual_review_recommended": True,
                            "confidence": "medium",
                            "severity": "medium",
                            "primary_issue": "Runtime checks were skipped.",
                        },
                    ]
                },
                "missing_incomplete_submission_coverage_chart": {
                    "stages": [
                        {"id": "assigned_students", "label": "Assigned students", "count": 2, "student_ids": ["student1", "student2"]},
                        {"id": "active_in_scope", "label": "Active submissions in scope", "count": 2, "student_ids": ["student1", "student2"]},
                    ]
                },
            },
        },
    )

    response = client.get("/teacher/assignment/assignment1/analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Assignment Mark Distribution Histogram" in body
    assert "Static / Code Quality vs Behavioural / Functional Score" in body
    assert "Review Queue Scatter Plot" not in body
    assert "Top Failing Rules Chart" not in body
    assert "Requirement Coverage" in body
    assert "Confidence and Reliability" in body
    assert "Requirement Coverage Matrix" not in body
    assert "Confidence and Reliability Breakdown" not in body
    assert "Component Performance Distribution" not in body
    assert "Missing / Incomplete Submission Coverage Chart" not in body
    assert "Confidence mix" in body
    assert "Top limitation categories" in body
    assert "Scoring Sources and Confidence Effects" in body
    assert "Coverage Detail" not in body
    assert "Score Distribution" not in body

def test_assignment_analytics_rule_export_respects_rule_filters(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "top_failing_rules": [
                {
                    "rule_id": "HTML.REQ.FAIL",
                    "label": "HTML requirement failed",
                    "component": "html",
                    "severity": "FAIL",
                    "students_affected": 2,
                    "submissions_affected": 2,
                    "percent": 100,
                    "incident_count": 2,
                    "fail_incidents": 2,
                    "warning_incidents": 0,
                    "impact_type": "fail_level",
                    "score_impact": "Fail-level issue",
                    "examples": ["student1"],
                    "messages": ["Required HTML block missing."],
                },
                {
                    "rule_id": "CSS.REQ.WARN",
                    "label": "CSS requirement warning",
                    "component": "css",
                    "severity": "WARN",
                    "students_affected": 1,
                    "submissions_affected": 1,
                    "percent": 50,
                    "incident_count": 1,
                    "fail_incidents": 0,
                    "warning_incidents": 1,
                    "impact_type": "warning_level",
                    "score_impact": "Warning-level issue",
                    "examples": ["student2"],
                    "messages": ["Layout selector missing."],
                },
            ]
        },
    )

    response = client.get("/teacher/assignment/assignment1/analytics/export/rules.csv?severity=FAIL&component=html")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"HTML.REQ.FAIL" in response.data
    assert b"CSS.REQ.WARN" not in response.data

def test_assignment_analytics_pdf_export_downloads_attachment(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "needs_attention": [
                {
                    "student_id": "student1",
                    "submission_id": "student1_assignment1",
                    "severity": "high",
                    "overall": 0.42,
                    "grade": "poor",
                    "confidence": "low",
                    "evaluation_state": "partially_evaluated",
                    "reason": "reduced evaluation confidence",
                    "reason_detail": "Browser checks skipped",
                    "flags": ["browser issue"],
                    "matched_rule_ids": ["HTML.REQ.FAIL"],
                    "limitation_details": ["Timeout detected"],
                    "evidence_excerpt": "Runtime check timed out.",
                    "manual_review_recommended": True,
                    "review_note": "Review manually.",
                }
            ]
        },
    )

    response = client.get("/teacher/assignment/assignment1/analytics/export/needs-attention/pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == 'attachment; filename="assignment1_needs_attention.pdf"'
    assert response.data.startswith(b"%PDF-")
