from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from ams.analytics.assignment_analytics import generate_assignment_analytics, generate_student_assignment_analytics
from ams.core.db import init_db
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


def test_student_assignment_analytics_payload_is_student_safe_and_highlights_current_submission(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": True,
            "assigned_students": ["student1", "student2", "student3"],
        },
    )

    app = Flask(__name__)
    app.config["AMS_RUNS_ROOT"] = tmp_path

    with app.app_context():
        analytics = generate_student_assignment_analytics("assignment1", "student1", app=app)

    serialized = json.dumps(analytics, sort_keys=True)
    assert analytics["student"]["overall_percent"] == 90
    assert any(bin_row["is_current_student"] for bin_row in analytics["graphs"]["histogram"]["bins"])
    assert any(point["is_current_student"] for point in analytics["graphs"]["scatter"]["points"])
    assert analytics["graphs"]["histogram"]["summary_stats"]["mean_percent"] is not None
    assert analytics["graphs"]["scatter"]["reference_lines"]["show_mean_lines"] is True
    assert all("static_score_percent" in point for point in analytics["graphs"]["scatter"]["points"])
    assert "student1" not in serialized
    assert "student2" not in serialized
    assert "student3" not in serialized
    assert "assigned_student_ids" not in serialized
    assert "run_id" not in serialized
    assert "submission_id" not in serialized

def test_deterministic_student_feedback_has_default_items_without_llm() -> None:
    feedback = _deterministic_student_feedback(
        {
            "student": {"summary_line": "Your submission shows a partial attempt with stronger frontend than backend attainment."},
            "personal_insights": [],
            "strengths": [{"title": "HTML structure", "detail": "Your HTML structure requirements are one of the stronger parts of this submission."}],
            "improvements": [{"title": "SQL file presence", "detail": "Add the required SQL file so the database stage can be assessed properly."}],
            "needs_attention": [{"title": "Low confidence", "text": "Runtime checks timed out, which reduced confidence in this result."}],
        }
    )

    assert feedback["summary_mode"] == "deterministic"
    assert feedback["headline"]
    assert len(feedback["feedback"]) == 3
    assert {item["type"] for item in feedback["feedback"]} == {"strength", "action", "confidence"}

def test_student_assignment_analytics_route_renders_private_personal_dashboard(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path, "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True})
    client = app.test_client()
    authenticate_client(client, role="student")

    monkeypatch.setattr(
        "ams.web.auth.get_user",
        lambda user_id: {
            "userID": "student1",
            "role": "student",
            "firstName": "Test",
            "lastName": "Student",
            "email": "student1@example.com",
        },
    )

    assignment_payload = {
        "assignmentID": "assignment1",
        "title": "Coursework 1",
        "profile": "frontend",
        "marks_released": True,
        "assigned_students": ["student1", "student2", "student3"],
    }
    monkeypatch.setattr("ams.web.routes_student.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))
    monkeypatch.setattr("ams.analytics.assignment_analytics.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))

    response = client.get("/student/assignment/assignment1/analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Your performance" in body
    assert "Personal insight summary" in body
    assert "Cohort context" in body
    assert "Personalised feedback" in body
    assert "Deterministic wording" in body
    assert "Generate personalised feedback" in body
    assert "Deterministic insights appear above by default" not in body
    assert "Generate personalised feedback to receive a focused improvement summary grounded in your own result and anonymous cohort aggregates." not in body
    assert "Anonymous cohort distributions are shown below. Your own result is highlighted clearly." not in body
    assert "student2" not in body
    assert "student3" not in body
    assert "assigned students in scope" not in body
    assert "Export review queue" not in body
    assert "Teaching insight summary" not in body

def test_student_assignment_analytics_route_shows_active_attempt_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _write_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260319-100000_mark_frontend_valid",
        created_at="2026-03-19T10:00:00Z",
        score=0.9,
    )
    _write_failed_mark_run(
        tmp_path,
        assignment_id="assignment1",
        student_id="student1",
        run_id="20260320-100000_mark_frontend_invalid",
        created_at="2026-03-20T10:00:00Z",
    )

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path, "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True})
    client = app.test_client()
    authenticate_client(client, role="student")

    monkeypatch.setattr(
        "ams.web.auth.get_user",
        lambda user_id: {
            "userID": "student1",
            "role": "student",
            "firstName": "Test",
            "lastName": "Student",
            "email": "student1@example.com",
        },
    )

    assignment_payload = {
        "assignmentID": "assignment1",
        "title": "Coursework 1",
        "profile": "frontend",
        "marks_released": True,
        "assigned_students": ["student1"],
    }
    monkeypatch.setattr("ams.web.routes_student.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))
    monkeypatch.setattr("ams.analytics.assignment_analytics.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))

    response = client.get("/student/assignment/assignment1/analytics")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Attempt 1" in body
    assert "Active result" in body
    assert "Latest submission was invalid, so the previous valid submission remains active." in body

def test_student_personalised_feedback_endpoint_uses_safe_context_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path, "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True})
    client = app.test_client()
    authenticate_client(client, role="student")

    monkeypatch.setattr(
        "ams.web.auth.get_user",
        lambda user_id: {
            "userID": "student1",
            "role": "student",
            "firstName": "Test",
            "lastName": "Student",
            "email": "student1@example.com",
        },
    )

    assignment_payload = {
        "assignmentID": "assignment1",
        "title": "Coursework 1",
        "profile": "frontend",
        "marks_released": True,
        "assigned_students": ["student1", "student2", "student3"],
    }
    monkeypatch.setattr("ams.web.routes_student.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))
    monkeypatch.setattr("ams.analytics.assignment_analytics.get_assignment", lambda assignment_id: dict(assignment_payload, assignmentID=assignment_id))

    captured: dict[str, object] = {}

    class FakeProvider:
        model_name = "fake"

        def complete(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "summary_mode": "llm_student_feedback",
                        "headline": "Your strongest progress is in frontend structure, but backend completion and runtime confidence still need attention.",
                        "feedback": [
                            {
                                "type": "strength",
                                "title": "Frontend progress is stronger",
                                "text": "Your strongest assessed area is currently your frontend structure and code-quality work, which sits at or above the cohort median.",
                                "evidence_keys": ["strongest_component", "cohort_median_percent"],
                            },
                            {
                                "type": "weakness",
                                "title": "Backend completion is the main gap",
                                "text": "Your weakest assessed area is backend-oriented work, so the next improvement step is to complete the missing database or runtime-backed behaviour.",
                                "evidence_keys": ["weakest_component", "improvements"],
                            },
                            {
                                "type": "confidence",
                                "title": "Interpret lower-confidence evidence carefully",
                                "text": "Confidence reflects how complete the automated evidence is, so any skipped or limited runtime evidence should be interpreted cautiously.",
                                "evidence_keys": ["confidence", "confidence_explanation"],
                            }
                        ],
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_student.get_llm_provider", lambda: FakeProvider())

    response = client.get("/student/assignment/assignment1/analytics/personal-feedback.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "llm"
    assert payload["summary_mode"] == "llm_student_feedback"
    assert payload["feedback"][0]["title"] == "Frontend progress is stronger"
    assert "student2" not in str(captured["prompt"])
    assert "student3" not in str(captured["prompt"])
    assert "other student's identity" in str(captured["system_prompt"])

def test_student_assignment_analytics_route_blocks_unassigned_or_unreleased_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client, role="student")

    monkeypatch.setattr(
        "ams.web.auth.get_user",
        lambda user_id: {
            "userID": "student1",
            "role": "student",
            "firstName": "Test",
            "lastName": "Student",
            "email": "student1@example.com",
        },
    )

    hidden_assignment = {
        "assignmentID": "assignment1",
        "title": "Coursework 1",
        "profile": "frontend",
        "marks_released": False,
        "assigned_students": ["student2"],
    }
    monkeypatch.setattr("ams.web.routes_student.get_assignment", lambda assignment_id: dict(hidden_assignment, assignmentID=assignment_id))

    response = client.get("/student/assignment/assignment1/analytics")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/student/coursework")
