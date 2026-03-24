from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from ams.analytics.assignment_analytics import generate_assignment_analytics
from ams.io.web_storage import save_run_info
from ams.webui import create_app
from tests.webui.conftest import authenticate_client


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_report(student_id: str, assignment_id: str, score: float) -> dict:
    def requirement_status(value: float) -> str:
        if value >= 1.0:
            return "PASS"
        if value <= 0.0:
            return "FAIL"
        return "PARTIAL"

    return {
        "scores": {
            "overall": score,
            "by_component": {
                "html": {"score": score},
                "css": {"score": score},
                "js": {"score": score},
            },
        },
        "findings": [],
        "score_evidence": {
            "requirements": [
                {
                    "requirement_id": "HTML.REQ.STRUCTURE",
                    "component": "html",
                    "description": "Required HTML structure",
                    "stage": "static",
                    "aggregation_mode": "WEIGHTED_AVERAGE",
                    "score": score,
                    "status": requirement_status(score),
                    "weight": 1.0,
                    "required": True,
                },
                {
                    "requirement_id": "FRONTEND.BROWSER.PAGE_LOAD",
                    "component": "html",
                    "description": "Browser page load succeeds",
                    "stage": "browser",
                    "aggregation_mode": "WEIGHTED_AVERAGE",
                    "score": score,
                    "status": requirement_status(score),
                    "weight": 1.0,
                    "required": True,
                },
            ],
            "confidence": {"level": "high", "reasons": ["All enabled evidence available."]},
            "review": {"recommended": score < 0.5},
        },
        "metadata": {
            "submission_metadata": {
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": f"{student_id}_{assignment_id}.zip",
                "timestamp": "2026-03-19T12:00:00Z",
            },
            "student_identity": {
                "student_id": student_id,
                "name_normalized": student_id,
            },
        },
    }


def _write_mark_run(
    runs_root: Path,
    *,
    assignment_id: str,
    student_id: str,
    run_id: str,
    created_at: str,
    score: float,
) -> None:
    run_dir = runs_root / assignment_id / student_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(run_dir / "report.json", _make_report(student_id, assignment_id, score))
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "mark",
            "profile": "frontend",
            "created_at": created_at,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": f"{student_id}_{assignment_id}.zip",
            "status": "completed",
        },
    )


def _write_batch_run(
    runs_root: Path,
    *,
    assignment_id: str,
    run_id: str,
    created_at: str,
    students: list[tuple[str, float]],
) -> None:
    run_dir = runs_root / assignment_id / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for student_id, score in students:
        submission_id = f"{student_id}_{assignment_id}"
        report_path = run_dir / "runs" / submission_id / "report.json"
        _write_json(report_path, _make_report(student_id, assignment_id, score))
        records.append(
            {
                "id": submission_id,
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": f"{submission_id}.zip",
                "upload_timestamp": created_at,
                "overall": score,
                "components": {"html": score, "css": score, "js": score, "php": None, "sql": None},
                "status": "ok",
                "report_path": str(report_path),
            }
        )

    _write_json(
        run_dir / "batch_summary.json",
        {
            "records": records,
            "summary": {
                "total_submissions": len(records),
                "succeeded": len(records),
                "failed": 0,
                "profile": "frontend",
            },
        },
    )
    _write_json(
        run_dir / "run_index.json",
        {
            "run_id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": created_at,
            "overall": None,
            "status": "ok",
            "submissions": [
                {
                    "submission_id": record["id"],
                    "student_name": None,
                    "student_id": record["student_id"],
                    "assignment_id": record["assignment_id"],
                    "original_filename": record["original_filename"],
                    "upload_timestamp": record["upload_timestamp"],
                }
                for record in records
            ],
        },
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": created_at,
            "assignment_id": assignment_id,
            "status": "completed",
            "summary": "batch_summary.json",
        },
    )


def _write_invalid_batch_run(
    runs_root: Path,
    *,
    run_assignment_id: str,
    submission_assignment_id: str,
    run_id: str,
    created_at: str,
    students: list[str],
) -> None:
    run_dir = runs_root / run_assignment_id / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for student_id in students:
        submission_id = f"{student_id}_{submission_assignment_id}"
        records.append(
            {
                "id": submission_id,
                "student_id": student_id,
                "assignment_id": submission_assignment_id,
                "original_filename": f"{submission_id}.zip",
                "upload_timestamp": created_at,
                "overall": 0.0,
                "components": {"html": None, "css": None, "js": None, "php": None, "sql": None},
                "status": "invalid_assignment_id",
                "invalid": True,
                "validation_error": (
                    f"Assignment ID '{submission_assignment_id}' does not match "
                    f"the expected assignment '{run_assignment_id}'"
                ),
                "report_path": None,
            }
        )

    _write_json(
        run_dir / "batch_summary.json",
        {
            "records": records,
            "summary": {
                "total_submissions": len(records),
                "succeeded": 0,
                "failed": 0,
                "invalid": len(records),
                "profile": "frontend",
            },
        },
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": created_at,
            "assignment_id": run_assignment_id,
            "status": "completed",
            "summary": "batch_summary.json",
        },
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


def test_assignment_analytics_route_refreshes_on_open_and_reflects_release_state(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    assignment_states = [
        {
            "assignmentID": "assignment1",
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
        },
        {
            "assignmentID": "assignment1",
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": True,
            "assigned_students": ["student1", "student2"],
        },
    ]
    generator_calls = {"count": 0}

    def fake_assignment(_: str) -> dict:
        index = 0 if generator_calls["count"] == 0 else 1
        return assignment_states[index]

    def fake_generate(_: str, *, app=None) -> dict:
        generator_calls["count"] += 1
        return {
            "generated_at": f"2026-03-19T12:00:0{generator_calls['count']}Z",
            "submission_count": 1,
            "overall": {
                "total": 1,
                "mean": 0.75,
                "median": 0.75,
                "min": 0.75,
                "max": 0.75,
                "buckets": {
                    "No attempt (0%)": 0,
                    "Partial (1-50%)": 0,
                    "Good partial (51-99%)": 1,
                    "Full marks (100%)": 0,
                },
            },
            "coverage": {
                "assigned_students": 2,
                "active_in_scope": 1,
                "active_students": 1,
                "missing_assigned": 1,
                "missing_students": ["student2"],
                "fully_evaluated": 1,
                "partially_evaluated": 0,
                "not_analysable": 0,
                "inactive_or_superseded": 0,
                "coverage_percent": 50,
            },
            "reliability": {
                "fully_evaluated": 1,
                "partially_evaluated": 0,
                "not_analysable": 0,
                "runtime_skipped": 0,
                "browser_limited": 0,
                "manual_review": 0,
                "confidence": {"high": 1, "medium": 0, "low": 0},
            },
            "components": {},
            "needs_attention": [],
            "signals": [],
            "top_failing_rules": [],
            "requirement_coverage": [],
            "score_composition": [],
            "teaching_insights": ["HTML requirements are currently the strongest area."],
        }

    monkeypatch.setattr("ams.web.routes_teacher.get_assignment", fake_assignment)
    monkeypatch.setattr("ams.web.routes_teacher.generate_assignment_analytics", fake_generate)

    first = client.get("/teacher/assignment/assignment1/analytics")
    second = client.get("/teacher/assignment/assignment1/analytics")

    assert first.status_code == 200
    assert second.status_code == 200
    assert generator_calls["count"] == 2
    assert b"Marks withheld" in first.data
    assert b"Marks released" in second.data
    assert b"Cohort analytics for assignment1." in second.data
    assert b"Teaching insight summary" in second.data
    assert b"Export review queue" in second.data
    assert b"Regenerate Analytics" not in second.data
    assert not list(tmp_path.rglob("*analytics*.json"))


def test_assignment_analytics_export_uses_fresh_assignment_scoped_data(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
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
        "ams.web.routes_teacher.get_assignment",
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
        "ams.web.routes_teacher.get_user",
        lambda student_id: {"userID": student_id, "firstName": "Test", "lastName": "Student", "email": "s@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_teacher.list_users", lambda role=None: [])
    monkeypatch.setattr("ams.web.routes_teacher.get_runs_root", lambda app: tmp_path)
    monkeypatch.setattr("ams.web.routes_teacher.list_runs", lambda runs_root: [])

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
        "ams.web.routes_teacher.get_assignment",
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
        "ams.web.routes_teacher.get_user",
        lambda student_id: {
            "userID": student_id,
            "firstName": student_id,
            "lastName": "",
            "email": f"{student_id}@example.com",
        },
    )
    monkeypatch.setattr("ams.web.routes_teacher.list_users", lambda role=None: [])

    response = client.get("/teacher/assignment/assignment1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No submissions yet" not in body
    assert "student1" in body
    assert "student2" in body


def test_teaching_insights_json_falls_back_to_deterministic_copy_when_llm_output_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "AMS_RUNS_ROOT": tmp_path,
            "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True,
        }
    )
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": {
                "assignment_id": "assignment1",
                "assigned_students": 1,
                "active_in_scope": 1,
            },
        },
    )

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "insights": [
                            {
                                "insight_type": "coverage",
                                "priority": "low",
                                "text": "All 99 assigned students currently have an active submission in scope.",
                                "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                            }
                        ]
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_teacher.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "deterministic"
    assert payload["insights"][0]["text"] == "All assigned students currently have an active submission in scope."


def test_teaching_insights_json_uses_llm_wording_by_default_when_provider_succeeds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "AMS_RUNS_ROOT": tmp_path,
            "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True,
        }
    )
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": {
                "assignment_id": "assignment1",
                "assigned_students": 1,
                "active_in_scope": 1,
            },
        },
    )

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "insights": [
                            {
                                "text": "Every assigned student currently has an active submission in scope."
                            }
                        ]
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_teacher.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "llm"
    assert payload["insights"][0]["text"] == "Every assigned student currently has an active submission in scope."


def test_assignment_analytics_page_renders_deterministic_summary_and_manual_llm_trigger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_app(
        {
            "TESTING": True,
            "AMS_RUNS_ROOT": tmp_path,
            "AMS_ENABLE_ANALYTICS_LLM_SUMMARY": True,
        }
    )
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "generated_at": "2026-03-20T01:14:00Z",
            "submission_count": 1,
            "overall": {
                "total": 1,
                "mean": 0.67,
                "median": 0.67,
                "min": 0.67,
                "max": 0.67,
                "buckets": {
                    "No attempt (0%)": 0,
                    "Partial (1-50%)": 0,
                    "Good partial (51-99%)": 1,
                    "Full marks (100%)": 0,
                },
            },
            "coverage": {
                "assigned_students": 1,
                "active_in_scope": 1,
                "active_students": 1,
                "missing_assigned": 0,
                "missing_students": [],
                "fully_evaluated": 1,
                "partially_evaluated": 0,
                "not_analysable": 0,
                "inactive_or_superseded": 0,
                "coverage_percent": 100,
            },
            "reliability": {
                "fully_evaluated": 1,
                "fully_evaluated_submissions": 1,
                "partially_evaluated": 0,
                "partially_evaluated_submissions": 0,
                "not_analysable": 0,
                "not_analysable_submissions": 0,
                "manual_review": 0,
                "manual_review_submissions": 0,
                "limitation_incidents": 0,
                "limitation_categories": 0,
                "limitation_breakdown": [],
                "confidence": {"high": 1, "medium": 0, "low": 0},
            },
            "components": [],
            "needs_attention": [],
            "signals": [],
            "top_failing_rules": [],
            "requirement_coverage": [],
            "score_composition": [],
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": {
                "assignment_id": "assignment1",
                "assigned_students": 1,
                "active_in_scope": 1,
            },
        },
    )

    provider_calls: list[bool] = []

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            provider_calls.append(True)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "insights": [
                            {
                                "text": "Every assigned student currently has an active submission in scope."
                            }
                        ]
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_teacher.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics")

    assert response.status_code == 200
    assert b"All assigned students currently have an active submission in scope." in response.data
    assert b"Deterministic wording" in response.data
    assert b"Generate LLM summary" in response.data
    assert provider_calls == []


def test_assignment_analytics_page_renders_interactive_graph_sections(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
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
    assert "Requirement Coverage Matrix" in body
    assert "Confidence and Reliability Breakdown" in body
    assert "Component Performance Distribution" in body
    assert "Missing / Incomplete Submission Coverage Chart" in body
    assert "Score Distribution" not in body


def test_assignment_analytics_rule_export_respects_rule_filters(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_teacher.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1", "student2"],
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.generate_assignment_analytics",
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
