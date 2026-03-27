from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.web.routes_batch import _write_batch_reports_zip, _write_run_index_batch
from ams.web.routes_runs import _write_run_index_mark
from ams.webui import create_app
from tests.webui.conftest import (
    _capture_job_submission,
    _client,
    _make_zip,
    _seed_batch_llm_error_run,
    _seed_batch_threat_run,
    _seed_mark_llm_error_run,
    _seed_mark_run,
    _stub_assignment,
    _stub_assignment_options,
    _stub_student_assignment_options,
    authenticate_client,
)


def test_admin_dashboard_hides_preview_badge_on_admin_routes(tmp_path: Path) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)
    with client.session_transaction() as sess:
        sess["view_as_role"] = "teacher"

    response = client.get("/admin/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Admin view active" in body
    assert "Previewing teacher" not in body

def test_create_assignment_route_passes_selected_teachers(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    captured: dict[str, object] = {}

    def _create_assignment_stub(**kwargs):
        captured.update(kwargs)
        return True

    def _list_users_stub(role=None):
        if role == "teacher":
            return [
                {"userID": "teacher2", "firstName": "Ada", "lastName": "Jones"},
                {"userID": "teacher3", "firstName": "Ben", "lastName": "Smith"},
            ]
        return []

    monkeypatch.setattr("ams.web.routes_assignment_mgmt.create_assignment", _create_assignment_stub)
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", _list_users_stub)

    response = client.post(
        "/teacher/create-assignment",
        data={
            "assignment_id": "assignment1",
            "title": "Assignment 1",
            "profile": "frontend_interactive",
            "due_date": "2026-03-30T12:00",
            "teachers": ["teacher2", "teacher3"],
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured["assigned_teachers"] == ["teacher2", "teacher3"]

def test_assignment_detail_uses_per_submission_scores_for_batch_rows(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Assignment 1",
            "description": "",
            "profile": "fullstack",
            "marks_released": False,
            "assigned_students": ["testStudent2", "testStudent3"],
            "due_date": "2026-03-27T14:00",
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", lambda role=None: [])
    monkeypatch.setattr("ams.web.routes_teacher.get_runs_root", lambda app: tmp_path)
    monkeypatch.setattr(
        "ams.web.routes_teacher.list_runs",
        lambda _runs_root, only_active=True: [
            {
                "id": "20260323-020000_batch_fullstack_demo",
                "mode": "batch",
                "profile": "fullstack",
                "created_at": "2026-03-23T02:00:00Z",
                "assignment_id": "test_assignment1",
                "score": 42.0,
                "submissions": [
                    {
                        "submission_id": "testStudent2_test_assignment1",
                        "student_id": "testStudent2",
                        "assignment_id": "test_assignment1",
                        "overall": 0.26,
                        "status": "ok",
                        "invalid": False,
                    },
                    {
                        "submission_id": "testStudent3_test_assignment1",
                        "student_id": "testStudent3",
                        "assignment_id": "test_assignment1",
                        "overall": 0.58,
                        "status": "ok",
                        "invalid": False,
                    },
                ],
            }
        ],
    )

    response = client.get("/teacher/assignment/test_assignment1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("42%") == 0
    assert "26%" in body
    assert "58%" in body

def test_assignment_detail_marks_threat_batch_submission_as_threat(tmp_path: Path, monkeypatch) -> None:
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    run_id = "20260323-030255_batch_fullstack_demo"
    run_dir = tmp_path / "assignment1" / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "runs" / "student5_assignment1" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "scores": {"overall": 0.0, "by_component": {}},
                "findings": [{"id": "THREAT.TEST", "severity": "THREAT"}],
                "metadata": {
                    "submission_metadata": {
                        "student_id": "student5",
                        "assignment_id": "assignment1",
                        "original_filename": "student5_assignment1.zip",
                        "timestamp": "2026-03-23T03:05:59Z",
                    },
                    "student_identity": {"student_id": "student5", "name_normalized": "student5"},
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
                        "id": "student5_assignment1",
                        "student_id": "student5",
                        "assignment_id": "assignment1",
                        "original_filename": "student5_assignment1.zip",
                        "upload_timestamp": "2026-03-23T03:05:59Z",
                        "overall": 0.0,
                        "components": {"html": None, "css": None, "js": None, "php": None, "sql": None},
                        "status": "ok",
                        "threat_count": 5,
                        "report_path": str(report_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_index.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "mode": "batch",
                "profile": "fullstack",
                "created_at": "2026-03-23T03:02:55Z",
                "overall": None,
                "status": "ok",
                "submissions": [
                    {
                        "submission_id": "student5_assignment1",
                        "student_name": None,
                        "student_id": "student5",
                        "assignment_id": "assignment1",
                        "original_filename": "student5_assignment1.zip",
                        "upload_timestamp": "2026-03-23T03:05:59Z",
                        "status": "ok",
                        "invalid": False,
                        "error": None,
                    }
                ],
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
            "assignment_id": "assignment1",
            "status": "completed",
            "summary": "batch_summary.json",
        },
    )

    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Assignment 1",
            "description": "",
            "profile": "fullstack",
            "marks_released": False,
            "assigned_students": ["student5"],
            "due_date": "2026-03-27T14:00",
        },
    )
    monkeypatch.setattr(
        "ams.web.routes_assignment_mgmt.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.list_users", lambda role=None: [])

    response = client.get("/teacher/assignment/assignment1")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "student5" in body
    assert "THREAT" in body
    assert ">Threat<" in body
    assert ">Fail<" not in body

def test_assignment_detail_blocks_grade_release_when_threat_submission_exists(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, _run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])

    released: list[str] = []
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.release_marks", lambda assignment_id: released.append(assignment_id))

    page = client.get("/teacher/assignment/assignment1")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Grade release blocked" in body
    assert "Delete flagged submission" in body
    assert "Re-run submission" in body
    assert "aria-disabled=\"true\"" in body

    blocked = client.post("/teacher/assignment/assignment1/release", follow_redirects=True)
    assert blocked.status_code == 200
    assert released == []
    blocked_body = blocked.get_data(as_text=True)
    assert "Grades cannot be released while flagged submissions remain." in blocked_body
    assert run_id in blocked_body or submission_id in blocked_body

def test_assignment_detail_blocks_grade_release_when_llm_error_submission_exists(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, _run_dir = _seed_mark_llm_error_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student2"])

    released: list[str] = []
    monkeypatch.setattr("ams.web.routes_assignment_mgmt.release_marks", lambda assignment_id: released.append(assignment_id))

    page = client.get("/teacher/assignment/assignment1")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "One or more submissions failed during LLM-assisted marking." in body
    assert "LLM Error - Requires Review" in body
    assert "Delete flagged submission" in body
    assert "Re-run submission" in body
    assert "aria-disabled=\"true\"" in body

    blocked = client.post("/teacher/assignment/assignment1/release", follow_redirects=True)
    assert blocked.status_code == 200
    assert released == []
    blocked_body = blocked.get_data(as_text=True)
    assert "Grades cannot be released while flagged submissions remain." in blocked_body
    assert run_id in blocked_body or "student2" in blocked_body

def test_deleting_flagged_batch_submission_unblocks_grade_release(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])

    response = client.post(
        "/teacher/assignment/assignment1/threats/delete",
        data={"run_id": run_id, "submission_id": submission_id},
        follow_redirects=True,
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Grade release blocked" not in body
    assert "No flagged submissions remain. Grades can now be released." in body
    assert "aria-disabled=\"true\"" not in body

    batch_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    assert batch_summary["records"] == []
    assert not (run_dir / "runs" / submission_id).exists()

def test_deleting_llm_error_submission_unblocks_grade_release(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, run_dir = _seed_mark_llm_error_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student2"])

    response = client.post(
        "/teacher/assignment/assignment1/threats/delete",
        data={"run_id": run_id},
        follow_redirects=True,
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "LLM Error - Requires Review" not in body
    assert "No flagged submissions remain. Grades can now be released." in body
    assert "aria-disabled=\"true\"" not in body
    assert not run_dir.exists()

def test_delete_assignment_route_purges_assignment_storage(tmp_path: Path, monkeypatch) -> None:
    client, runs_root = _client(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student1"])
    run_id = "20260323-060000_batch_frontend_demo"
    run_dir = runs_root / "assignment1" / "batch" / run_id
    submission_dir = run_dir / "runs" / "student1_assignment1"
    (submission_dir / "submission").mkdir(parents=True, exist_ok=True)
    (submission_dir / "submission" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (run_dir / "batch_inputs" / "batch_submissions").mkdir(parents=True, exist_ok=True)
    (run_dir / "batch_inputs" / "batch_submissions" / "student1_assignment1.zip").write_bytes(_make_zip({"index.html": "<!doctype html>"}))
    (run_dir / "batch_submissions.zip").write_bytes(_make_zip({"student1_assignment1.zip": "placeholder"}))
    (run_dir / "batch_reports_frontend_demo.zip").write_bytes(b"legacy")
    for filename in (
        "component_means.csv",
        "failure_reasons_frequency.csv",
        "findings_frequency.csv",
        "score_buckets.csv",
    ):
        (run_dir / filename).write_text("legacy", encoding="utf-8")
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "assignment_id": "assignment1",
            "original_filename": "batch_submissions.zip",
            "created_at": "2026-03-23T06:00:00Z",
            "status": "completed",
        },
    )

    legacy_run_dir = runs_root / "legacy_batch_run"
    legacy_run_dir.mkdir(parents=True, exist_ok=True)
    save_run_info(
        legacy_run_dir,
        {
            "id": "legacy_batch_run",
            "mode": "batch",
            "profile": "frontend",
            "assignment_id": "assignment1",
            "created_at": "2026-03-23T06:01:00Z",
            "status": "completed",
        },
    )

    monkeypatch.setattr("ams.web.routes_assignment_mgmt.delete_assignment", lambda assignment_id: True)

    response = client.post("/teacher/assignment/assignment1/delete")

    assert response.status_code == 302
    assert not run_dir.exists()
    assert not (runs_root / "assignment1").exists()
    assert not legacy_run_dir.exists()

def test_assignment_detail_shows_rerun_action_for_every_submission(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student1", "student2"])
    monkeypatch.setattr(
        "ams.web.routes_teacher.get_runs_root",
        lambda app: tmp_path,
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.list_runs",
        lambda _runs_root, only_active=True: [
            {
                "id": "20260323-050000_mark_frontend_demo",
                "mode": "mark",
                "profile": "frontend",
                "created_at": "2026-03-23T05:00:00Z",
                "assignment_id": "assignment1",
                "student_id": "student1",
                "score": 61.0,
                "status": "completed",
                "submissions": [
                    {
                        "submission_id": "20260323-050000_mark_frontend_demo",
                        "student_id": "student1",
                        "assignment_id": "assignment1",
                        "status": "ok",
                        "invalid": False,
                    }
                ],
            },
            {
                "id": "20260323-050500_batch_frontend_demo",
                "mode": "batch",
                "profile": "frontend",
                "created_at": "2026-03-23T05:05:00Z",
                "assignment_id": "assignment1",
                "submissions": [
                    {
                        "submission_id": "student2_assignment1",
                        "student_id": "student2",
                        "assignment_id": "assignment1",
                        "overall": 0.48,
                        "status": "ok",
                        "invalid": False,
                    }
                ],
            },
        ],
    )

    response = client.get("/teacher/assignment/assignment1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("Rerun Submission") >= 2
    assert body.count('data-job-form="rerun"') >= 2
