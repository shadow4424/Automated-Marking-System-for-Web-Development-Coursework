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
