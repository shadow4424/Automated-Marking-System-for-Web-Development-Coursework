"""Shared fixtures for webui tests ? provides an authenticated test client."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from flask.testing import FlaskClient

from ams.core.database import init_db
from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.web.routes_batch import _write_run_index_batch
from ams.web.routes_runs import _write_run_index_mark
from ams.webui import create_app


def authenticate_client(client: FlaskClient, role: str = "admin") -> None:
    """Inject a fully-authenticated session into the Flask test client."""
    with client.session_transaction() as sess:
        if role == "admin":
            sess["user_id"] = "admin123"
        else:
            sess["user_id"] = f"_test_{role}"
        sess["user_role"] = role
        sess["2fa_verified"] = True


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()

def _client(tmp_path: Path):
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)
    return client, tmp_path

def _stub_assignment(monkeypatch, assignment_id: str, assigned_students: list[str]) -> None:
    assignment = {
        "assignmentID": assignment_id,
        "title": "Assignment 1",
        "description": "",
        "profile": "fullstack",
        "marks_released": False,
        "assigned_students": assigned_students,
        "assigned_teachers": [],
        "teacher_ids": ["admin123"],
        "due_date": "2026-03-27T14:00",
        "teacherID": "admin123",
    }
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.web.routes_batch.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.web.routes_runs.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.web.routes_dashboard.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", lambda role=None: [])

def _stub_assignment_options(monkeypatch, assignments: list[dict]) -> None:
    monkeypatch.setattr("ams.web.routes_marking.list_assignments", lambda teacher_id=None: assignments)
    monkeypatch.setattr("ams.web.routes_batch.list_assignments", lambda teacher_id=None: assignments)

def _stub_student_assignment_options(monkeypatch, assignments: list[dict]) -> None:
    monkeypatch.setattr("ams.web.routes_marking.list_assignments_for_student", lambda student_id: assignments)
    monkeypatch.setattr("ams.web.routes_dashboard.list_assignments_for_student", lambda student_id: assignments)

def _seed_batch_threat_run(tmp_path: Path, assignment_id: str = "assignment1", student_id: str = "student5") -> tuple[str, str, Path]:
    run_id = "20260323-030255_batch_fullstack_demo"
    submission_id = f"{student_id}_{assignment_id}"
    run_dir = tmp_path / assignment_id / "batch" / run_id
    submission_dir = run_dir / "runs" / submission_id
    report_path = submission_dir / "report.json"
    source_zip = run_dir / "batch_inputs" / "batch_submissions" / f"{submission_id}.zip"

    (submission_dir / "submission").mkdir(parents=True, exist_ok=True)
    (submission_dir / "submission" / "index.html").write_text("<!doctype html><html><body>safe</body></html>", encoding="utf-8")
    source_zip.parent.mkdir(parents=True, exist_ok=True)
    source_zip.write_bytes(_make_zip({"index.html": "<!doctype html><html><body>safe</body></html>"}))
    report_path.write_text(
        json.dumps(
            {
                "scores": {
                    "overall": 0.0,
                    "by_component": {
                        "html": {"score": 0.0},
                        "css": {"score": 0.0},
                        "js": {"score": 0.0},
                        "php": {"score": 0.0},
                        "sql": {"score": 0.0},
                    },
                },
                "findings": [{"id": "SANDBOX.THREAT.TEST", "severity": "THREAT"}],
                "metadata": {
                    "submission_metadata": {
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": source_zip.name,
                        "timestamp": "2026-03-23T03:05:59Z",
                    },
                    "student_identity": {"student_id": student_id, "name_normalized": student_id},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "batch_summary.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": submission_id,
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": source_zip.name,
                        "upload_timestamp": "2026-03-23T03:05:59Z",
                        "overall": 0.0,
                        "components": {"html": None, "css": None, "js": None, "php": None, "sql": None},
                        "status": "ok",
                        "threat_count": 5,
                        "threat_flagged": True,
                        "path": str(source_zip),
                        "report_path": str(report_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "fullstack",
            "created_at": "2026-03-23T03:02:55Z",
            "assignment_id": assignment_id,
            "status": "completed",
            "summary": "batch_summary.json",
        },
    )
    _write_run_index_batch(run_dir, {"id": run_id, "mode": "batch", "profile": "fullstack", "created_at": "2026-03-23T03:02:55Z"})
    return run_id, submission_id, run_dir

def _seed_batch_llm_error_run(tmp_path: Path, assignment_id: str = "assignment1", student_id: str = "student4") -> tuple[str, str, Path]:
    run_id = "20260323-031500_batch_fullstack_llm"
    submission_id = f"{student_id}_{assignment_id}"
    run_dir = tmp_path / assignment_id / "batch" / run_id
    submission_dir = run_dir / "runs" / submission_id
    report_path = submission_dir / "report.json"
    source_zip = run_dir / "batch_inputs" / "batch_submissions" / f"{submission_id}.zip"

    (submission_dir / "submission").mkdir(parents=True, exist_ok=True)
    (submission_dir / "submission" / "index.html").write_text("<!doctype html><html><body>safe</body></html>", encoding="utf-8")
    source_zip.parent.mkdir(parents=True, exist_ok=True)
    source_zip.write_bytes(_make_zip({"index.html": "<!doctype html><html><body>safe</body></html>"}))
    report_path.write_text(
        json.dumps(
            {
                "scores": {
                    "overall": 0.52,
                    "by_component": {
                        "html": {"score": 0.5},
                        "css": {"score": 0.6},
                        "js": {"score": 0.4},
                        "php": {"score": 0.5},
                        "sql": {"score": 0.6},
                    },
                },
                "findings": [
                    {
                        "id": "CSS.REQ.FAIL",
                        "severity": "FAIL",
                        "evidence": {
                            "llm_feedback": {
                                "summary": "fallback",
                                "items": [],
                                "meta": {"fallback": True, "reason": "llm_error", "error": "Provider timeout"},
                            }
                        },
                    },
                    {
                        "id": "LLM.ERROR.REQUIRES_REVIEW",
                        "severity": "WARN",
                        "evidence": {
                            "llm_error_message": "CSS.REQ.FAIL: Provider timeout",
                            "llm_error_messages": ["CSS.REQ.FAIL: Provider timeout"],
                        },
                    },
                ],
                "metadata": {
                    "submission_metadata": {
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": source_zip.name,
                        "timestamp": "2026-03-23T03:15:59Z",
                    },
                    "student_identity": {"student_id": student_id, "name_normalized": student_id},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "batch_summary.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": submission_id,
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": source_zip.name,
                        "upload_timestamp": "2026-03-23T03:15:59Z",
                        "overall": 0.52,
                        "components": {"html": 0.5, "css": 0.6, "js": 0.4, "php": 0.5, "sql": 0.6},
                        "status": "llm_error",
                        "llm_error_flagged": True,
                        "llm_error_message": "CSS.REQ.FAIL: Provider timeout",
                        "llm_error_messages": ["CSS.REQ.FAIL: Provider timeout"],
                        "path": str(source_zip),
                        "report_path": str(report_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "fullstack",
            "created_at": "2026-03-23T03:15:55Z",
            "assignment_id": assignment_id,
            "status": "completed",
            "summary": "batch_summary.json",
        },
    )
    _write_run_index_batch(run_dir, {"id": run_id, "mode": "batch", "profile": "fullstack", "created_at": "2026-03-23T03:15:55Z"})
    return run_id, submission_id, run_dir

def _seed_mark_run(tmp_path: Path, assignment_id: str = "assignment1", student_id: str = "student1") -> tuple[str, Path]:
    run_id = "20260323-040000_mark_frontend_demo"
    run_dir = tmp_path / assignment_id / student_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    upload_zip = run_dir / f"{student_id}_{assignment_id}.zip"
    upload_zip.write_bytes(_make_zip({"index.html": "<!doctype html><html><body>old</body></html>"}))
    extracted = run_dir / "uploaded_extract"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / "index.html").write_text("<!doctype html><html><body>old</body></html>", encoding="utf-8")
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "scores": {"overall": 0.21, "by_component": {"html": {"score": 0.21}}},
                "findings": [{"id": "HTML.REQ.FAIL", "severity": "FAIL"}],
                "metadata": {
                    "submission_metadata": {
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": upload_zip.name,
                        "timestamp": "2026-03-23T04:00:00Z",
                    },
                    "student_identity": {"student_id": student_id, "name_normalized": student_id},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "summary.txt").write_text("old summary", encoding="utf-8")
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "mark",
            "profile": "frontend",
            "scoring_mode": "static_plus_llm",
            "created_at": "2026-03-23T04:00:00Z",
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": upload_zip.name,
            "source": "github",
            "github_repo": "teacher/demo",
            "report": "report.json",
            "summary": "summary.txt",
            "status": "completed",
        },
    )
    index_run_info = {
        "id": run_id,
        "mode": "mark",
        "profile": "frontend",
        "created_at": "2026-03-23T04:00:00Z",
        "student_id": student_id,
        "assignment_id": assignment_id,
        "original_filename": upload_zip.name,
    }
    _write_run_index_mark(run_dir, index_run_info, report_path)
    return run_id, run_dir

def _seed_mark_llm_error_run(tmp_path: Path, assignment_id: str = "assignment1", student_id: str = "student2") -> tuple[str, Path]:
    run_id = "20260323-041500_mark_frontend_llm"
    run_dir = tmp_path / assignment_id / student_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    upload_zip = run_dir / f"{student_id}_{assignment_id}.zip"
    upload_zip.write_bytes(_make_zip({"index.html": "<!doctype html><html><body>old</body></html>"}))
    extracted = run_dir / "uploaded_extract"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / "index.html").write_text("<!doctype html><html><body>old</body></html>", encoding="utf-8")
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "scores": {"overall": 0.33, "by_component": {"html": {"score": 0.33}}},
                "findings": [
                    {
                        "id": "HTML.REQ.FAIL",
                        "severity": "FAIL",
                        "evidence": {
                            "llm_feedback": {
                                "summary": "fallback",
                                "items": [],
                                "meta": {"fallback": True, "reason": "llm_error", "error": "Upstream LLM timeout"},
                            }
                        },
                    },
                    {
                        "id": "LLM.ERROR.REQUIRES_REVIEW",
                        "severity": "WARN",
                        "evidence": {
                            "llm_error_message": "HTML.REQ.FAIL: Upstream LLM timeout",
                            "llm_error_messages": ["HTML.REQ.FAIL: Upstream LLM timeout"],
                        },
                    },
                ],
                "metadata": {
                    "submission_metadata": {
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": upload_zip.name,
                        "timestamp": "2026-03-23T04:15:00Z",
                    },
                    "student_identity": {"student_id": student_id, "name_normalized": student_id},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "summary.txt").write_text("old summary", encoding="utf-8")
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "mark",
            "profile": "frontend",
            "scoring_mode": "static_plus_llm",
            "created_at": "2026-03-23T04:15:00Z",
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": upload_zip.name,
            "report": "report.json",
            "summary": "summary.txt",
            "status": "llm_error",
            "llm_error_flagged": True,
            "llm_error_message": "HTML.REQ.FAIL: Upstream LLM timeout",
            "llm_error_messages": ["HTML.REQ.FAIL: Upstream LLM timeout"],
        },
    )
    index_run_info = {
        "id": run_id,
        "mode": "mark",
        "profile": "frontend",
        "created_at": "2026-03-23T04:15:00Z",
        "student_id": student_id,
        "assignment_id": assignment_id,
        "original_filename": upload_zip.name,
        "status": "llm_error",
    }
    _write_run_index_mark(run_dir, index_run_info, report_path)
    return run_id, run_dir

def _capture_job_submission(monkeypatch):
    captured: dict[str, object] = {}

    def _submit_job(task_type, func, *args, **kwargs):
        captured["task_type"] = task_type
        captured["func"] = lambda: func(*args, **kwargs)
        return "job-queued-1"

    monkeypatch.setattr("ams.web.routes_marking.job_manager.submit_job", _submit_job)
    monkeypatch.setattr("ams.web.routes_batch.job_manager.submit_job", _submit_job)
    monkeypatch.setattr("ams.web.routes_runs.job_manager.submit_job", _submit_job)
    return captured


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _use_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("ams.core.database._DEFAULT_DB_PATH", tmp_path / "ams_users.db")
    init_db()


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


def _rich_teaching_insight_context() -> dict:
    return {
        "assignment_id": "assignment1",
        "profile": "frontend",
        "assigned_students": 5,
        "active_in_scope": 5,
        "coverage_percent": 100,
        "missing_assigned": 0,
        "average_score": 63.4,
        "median_score": 61.0,
        "min_score": 27.0,
        "max_score": 92.0,
        "score_band_distribution": [
            {"label": "Partial (1-50%)", "count": 1, "percent": 20.0},
            {"label": "Good partial (51-99%)", "count": 4, "percent": 80.0},
        ],
        "dominant_score_band": {"label": "Good partial (51-99%)", "count": 4, "percent": 80.0},
        "strongest_requirement": {"component": "js", "title": "Required JavaScript behaviour", "students_met": 2, "met_percent": 40.0},
        "weakest_requirement": {"component": "sql", "title": "Required SQL/database behaviour", "students_met": 0, "met_percent": 0.0},
        "requirement_coverage_summary": [
            {
                "component": "js",
                "title": "Required JavaScript behaviour",
                "rule_count": 10,
                "met_count": 2,
                "partial_count": 3,
                "unmet_count": 0,
                "not_evaluable_count": 0,
                "fully_met_percent": 40.0,
            },
            {
                "component": "sql",
                "title": "Required SQL/database behaviour",
                "rule_count": 12,
                "met_count": 0,
                "partial_count": 3,
                "unmet_count": 2,
                "not_evaluable_count": 0,
                "fully_met_percent": 0.0,
            },
        ],
        "component_performance_summary": [
            {
                "component": "css",
                "title": "Required CSS requirements",
                "average_component_score": 48.0,
                "median_component_score": 50.0,
                "score_0_count": 1,
                "score_0_5_count": 3,
                "score_1_count": 1,
                "other_scored_count": 0,
                "total_evaluable": 5,
            }
        ],
        "top_failing_rule": {
            "rule_id": "CSS.HAS_MEDIA_QUERY",
            "label": "Uses media queries for responsive design",
            "component": "css",
            "severity": "FAIL",
            "submissions_affected": 4,
            "percent": 80.0,
        },
        "top_failing_rules": [
            {
                "rule_id": "CSS.HAS_MEDIA_QUERY",
                "label": "Uses media queries for responsive design",
                "component": "css",
                "category": "structure",
                "severity": "FAIL",
                "submissions_affected": 4,
                "percent": 80.0,
                "incident_count": 4,
                "confidence_affecting": False,
            }
        ],
        "major_rule_categories": [
            {
                "category": "structure",
                "rules_affected": 2,
                "students_affected_total": 6,
                "incident_count_total": 6,
                "fail_incidents": 5,
                "warning_incidents": 1,
            }
        ],
        "confidence_mix": {
            "high": {"count": 1, "percent": 20.0},
            "medium": {"count": 3, "percent": 60.0},
            "low": {"count": 1, "percent": 20.0},
        },
        "manual_review": 5,
        "fully_evaluated": 1,
        "partially_evaluated": 4,
        "not_analysable": 0,
        "limitation_incidents": 4,
        "major_limitations": [
            {
                "id": "runtime_skipped",
                "label": "Runtime checks skipped or unavailable",
                "incident_count": 4,
                "percent": 80.0,
            }
        ],
        "runtime_skip_count": 4,
        "browser_skip_count": 1,
        "runtime_failure_count": 0,
        "browser_failure_count": 0,
        "static_vs_behavioural_mismatch": {
            "supported": True,
            "unsupported_reason": "",
            "plotted_student_count": 5,
            "behavioural_evaluable_students": 5,
            "high_static_low_behavioural_count": 2,
            "high_behavioural_low_static_count": 0,
            "balanced_count": 2,
            "mean_static_score": 68.0,
            "mean_behavioural_score": 51.0,
            "largest_gap_examples": [
                {
                    "student_id": "student3",
                    "overall_mark_percent": 58.0,
                    "static_score_percent": 74.0,
                    "behavioural_score_percent": 39.0,
                    "gap_percent": 35.0,
                    "manual_review_recommended": True,
                    "confidence": "medium",
                }
            ],
        },
        "high_priority_flagged_submissions": {
            "count": 2,
            "medium_or_higher_count": 5,
            "low_confidence_count": 1,
            "manual_review_count": 5,
            "examples": [
                {
                    "student_id": "student3",
                    "severity": "high",
                    "confidence": "medium",
                    "reason": "reduced evaluation confidence",
                    "overall_score": 58.0,
                    "manual_review_recommended": True,
                }
            ],
        },
    }


def _valid_llm_teacher_summary() -> dict:
    return {
        "summary_mode": "llm_teacher_insight",
        "headline": "The cohort is attempting the assignment, but attainment falls once backend completion and runtime confidence become more important.",
        "insights": [
            {
                "priority": "high",
                "type": "pattern",
                "title": "Partial attainment is the main cohort pattern",
                "text": "Most submissions sit in the 'Good partial (51-99%)' band rather than at full attainment, which points to incomplete implementation rather than non-attempt.",
                "evidence_keys": ["score_band_distribution", "dominant_score_band"],
            },
            {
                "priority": "medium",
                "type": "strength",
                "title": "JavaScript is comparatively stronger",
                "text": "Required JavaScript behaviour stands out as the strongest requirement area, so the cohort looks more secure on this layer than on the rest of the stack.",
                "evidence_keys": ["strongest_requirement", "requirement_coverage_summary"],
            },
            {
                "priority": "high",
                "type": "weakness",
                "title": "Backend completion is the main weakness",
                "text": "Required SQL/database behaviour remains the weakest requirement area, which points to difficulty completing the assignment once it depends on backend or data-backed execution.",
                "evidence_keys": ["weakest_requirement", "requirement_coverage_summary"],
            },
            {
                "priority": "high",
                "type": "anomaly",
                "title": "Runtime limits are affecting interpretation",
                "text": "Runtime-check limitations affect 4 of 5 submissions (80%), so lower-confidence outcomes should be interpreted more cautiously than usual.",
                "evidence_keys": ["major_limitations", "runtime_skip_count", "manual_review"],
            },
            {
                "priority": "high",
                "type": "recommendation",
                "title": "Prioritise manual review before release",
                "text": "Manual review is currently needed for 5 of 5 submissions (100%). Prioritise work where static progress looks stronger than behavioural evidence before marks are released.",
                "evidence_keys": ["manual_review", "static_vs_behavioural_mismatch", "high_priority_flagged_submissions"],
            },
        ],
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


def _write_failed_mark_run(
    runs_root: Path,
    *,
    assignment_id: str,
    student_id: str,
    run_id: str,
    created_at: str,
) -> None:
    run_dir = runs_root / assignment_id / student_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
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
            "status": "failed",
            "error": "pipeline failed",
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
