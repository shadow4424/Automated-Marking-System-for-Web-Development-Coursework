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

    monkeypatch.setattr("ams.web.routes_assignment_mgmt.get_assignment", fake_assignment)
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.generate_assignment_analytics", fake_generate)

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
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": _rich_teaching_insight_context(),
        },
    )

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "summary_mode": "llm_teacher_insight",
                        "headline": "The cohort is broadly engaging with the assignment but backend and runtime completion remain fragile.",
                        "insights": [
                            {
                                "priority": "high",
                                "type": "weakness",
                                "title": "Backend work is the weakest area",
                                "text": "Required SQL/database behaviour is the weakest area, which suggests the cohort is struggling to complete the assignment once it depends on backend or data-backed behaviour.",
                                "evidence_keys": ["weakest_requirement"]
                            }
                        ]
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "deterministic"
    assert payload["summary_mode"] == "deterministic"
    assert payload["headline"] == ""
    assert payload["validation_status"] == "rejected"
    assert payload["fallback_reason_code"] == "too_few_insights"
    assert "rejected during validation" in payload["fallback_reason"]
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
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Coursework 1",
            "profile": "frontend",
            "marks_released": False,
            "assigned_students": ["student1"],
        },
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.generate_assignment_analytics",
        lambda *_args, **_kwargs: {
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": _rich_teaching_insight_context(),
        },
    )

    class FakeProvider:
        model_name = "fake"

        def complete(self, prompt, **kwargs):
            captured["prompt"] = json.loads(prompt)
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "summary_mode": "llm_teacher_insight",
                        "headline": "The cohort is attempting the assignment, but attainment drops where runtime confidence and backend completion become more important.",
                        "insights": [
                            {
                                "priority": "high",
                                "type": "pattern",
                                "title": "Partial attainment is the dominant pattern",
                                "text": "Most submissions appear to be progressing into partial attainment rather than failing outright, which suggests incomplete implementation is a bigger issue than non-attempt.",
                                "evidence_keys": ["score_band_distribution", "dominant_score_band"]
                            },
                            {
                                "priority": "medium",
                                "type": "strength",
                                "title": "JavaScript is comparatively stronger",
                                "text": "Required JavaScript behaviour stands out as the strongest requirement area, so the cohort looks more secure on this layer than on the rest of the stack.",
                                "evidence_keys": ["strongest_requirement", "requirement_coverage_summary"]
                            },
                            {
                                "priority": "high",
                                "type": "weakness",
                                "title": "Backend completion is the main weakness",
                                "text": "Required SQL/database behaviour remains the weakest requirement area, which points to difficulty completing the assignment once it depends on backend or data-backed execution.",
                                "evidence_keys": ["weakest_requirement", "component_performance_summary"]
                            },
                            {
                                "priority": "high",
                                "type": "anomaly",
                                "title": "Reliability limits are affecting interpretation",
                                "text": "Runtime checks being skipped across much of the cohort means lower-confidence outcomes should be interpreted cautiously, especially where static progress looks better than behavioural evidence.",
                                "evidence_keys": ["manual_review", "partially_evaluated", "major_limitations", "runtime_skip_count", "static_vs_behavioural_mismatch"]
                            },
                            {
                                "priority": "high",
                                "type": "recommendation",
                                "title": "Review medium-confidence partial work before release",
                                "text": "Manual review should prioritise submissions where static progress is reasonable but behavioural evidence is weaker, and teaching follow-up should focus on responsive CSS and SQL/database completion.",
                                "evidence_keys": ["manual_review", "top_failing_rule", "weakest_requirement", "static_vs_behavioural_mismatch"]
                            }
                        ]
                    }
                ),
                success=True,
                error=None,
            )

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "llm"
    assert payload["summary_mode"] == "llm_teacher_insight"
    assert payload["headline"].startswith("The cohort is attempting the assignment")
    assert payload["insights"][0]["title"] == "Partial attainment is the dominant pattern"
    assert payload["insights"][0]["type"] == "pattern"
    assert payload["insights"][0]["evidence_keys"] == ["score_band_distribution", "dominant_score_band"]
    assert captured["prompt"]["assignment_analytics"]["average_score"] == 63.4
    assert "runtime_skip_count" in captured["prompt"]["assignment_analytics"]
    assert "Do not paraphrase the deterministic summary" in str(captured["system_prompt"])
    assert "Provide 4 to 6 insights" in str(captured["system_prompt"])

def test_teaching_insights_json_accepts_valid_llm_summary_with_rounded_percentages(
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

    context = _rich_teaching_insight_context()
    context["average_score"] = 31.25

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
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": context,
        },
    )

    valid_payload = _valid_llm_teacher_summary()
    valid_payload["insights"][2]["text"] = "The overall average sits at 31.2%, which indicates the cohort is still struggling to convert attempts into complete backend-capable work."
    valid_payload["insights"][2]["evidence_keys"] = ["average_score", "weakest_requirement"]

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(content=json.dumps(valid_payload), success=True, error=None)

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "llm"
    assert payload["insights"][2]["text"].startswith("The overall average sits at 31.2%")

def test_teaching_insights_json_accepts_integer_percentage_wording_with_exact_counts(
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
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": _rich_teaching_insight_context(),
        },
    )

    valid_payload = _valid_llm_teacher_summary()
    valid_payload["insights"][3]["text"] = "Medium confidence applies to 3 out of 5 submissions (60%), so borderline partial work should be checked before release."
    valid_payload["insights"][3]["evidence_keys"] = ["confidence_mix", "manual_review"]

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(content=json.dumps(valid_payload), success=True, error=None)

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "llm"
    assert payload["insights"][3]["text"].startswith("Medium confidence applies to 3 out of 5 submissions (60%)")

def test_teaching_insights_json_rejects_fabricated_percentage_and_logs_reason(
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
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
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": _rich_teaching_insight_context(),
        },
    )

    invalid_payload = _valid_llm_teacher_summary()
    invalid_payload["insights"][3]["text"] = "Runtime-check limitations affect 83% of the cohort, so lower-confidence outcomes should be interpreted more cautiously than usual."

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(content=json.dumps(invalid_payload), success=True, error=None)

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    with caplog.at_level(logging.INFO):
        response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "deterministic"
    assert payload["validation_status"] == "rejected"
    assert payload["fallback_reason_code"] == "numeric_mismatch"
    assert "numeric validation failed" in payload["fallback_reason"]
    assert "numeric_mismatch" in caplog.text
    assert "83%" in caplog.text

def test_teaching_insights_json_rejects_unsupported_most_students_claim(
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
            "teaching_insights": [
                {
                    "insight_type": "coverage",
                    "priority": "low",
                    "text": "All assigned students currently have an active submission in scope.",
                    "supporting_metric_keys": ["assigned_students", "active_in_scope"],
                }
            ],
            "teaching_insight_context": _rich_teaching_insight_context(),
        },
    )

    invalid_payload = _valid_llm_teacher_summary()
    invalid_payload["insights"][3]["text"] = "Most students are low confidence, so release decisions should be delayed until the cohort is checked manually."
    invalid_payload["insights"][3]["evidence_keys"] = ["confidence_mix", "manual_review"]

    class FakeProvider:
        model_name = "fake"

        def complete(self, *args, **kwargs):
            return SimpleNamespace(content=json.dumps(invalid_payload), success=True, error=None)

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics/teaching-insights.json")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["source"] == "deterministic"
    assert payload["validation_status"] == "rejected"
    assert payload["fallback_reason_code"] == "unsupported_claim"
    assert "unsupported claim detected" in payload["fallback_reason"]

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

    monkeypatch.setattr("ams.web.routes_teacher_helpers.get_llm_provider", lambda: FakeProvider())

    response = client.get("/teacher/assignment/assignment1/analytics")

    assert response.status_code == 200
    assert b"All assigned students currently have an active submission in scope." in response.data
    assert b"Deterministic wording" in response.data
    assert b"Generate LLM summary" in response.data
    assert b"window.AMS_CHART_DATA =" in response.data
    assert b"js/pages/analytics-charts.js" in response.data
    assert provider_calls == []
