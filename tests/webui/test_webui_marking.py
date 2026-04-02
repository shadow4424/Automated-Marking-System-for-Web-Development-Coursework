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


def test_webui_mark_rejects_non_zip(tmp_path: Path):
    client, _ = _client(tmp_path)
    data = {
        "profile": "frontend",
        "submission": (io.BytesIO(b"notzip"), "note.txt"),
    }
    res = client.post("/mark", data=data, content_type="multipart/form-data")
    assert res.status_code == 400

def test_webui_mark_zip_happy_path_creates_run_and_shows_scores(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text("<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>", encoding="utf-8")
    (submission_dir / "style.css").write_text("body { color: red; }", encoding="utf-8")
    (submission_dir / "app.js").write_text("document.body.addEventListener('click', ()=>{});", encoding="utf-8")

    run_id, run_dir = create_run_dir(runs_root, mode="mark", profile="frontend")
    report_path = AssessmentPipeline().run(submission_dir, run_dir, profile="frontend")
    run_info = {
        "id": run_id,
        "mode": "mark",
        "profile": "frontend",
        "created_at": "now",
        "report": report_path.name,
        "summary": "summary.txt",
    }
    save_run_info(run_dir, run_info)
    _write_run_index_mark(run_dir, run_info, report_path)

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert b"Recommended outcome" in detail.data
    assert b"Summary" in detail.data
    assert b"Export Results" in detail.data  # Dropdown trigger text (was "Download JSON")
    assert b"Admin actions" not in detail.data
    assert b"Informational evidence" not in detail.data
    assert b"Raw technical details" not in detail.data
    assert any(runs_root.iterdir())

def test_individual_submission_pdf_export_downloads_attachment(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    run_id, run_dir = create_run_dir(runs_root, mode="mark", profile="frontend")
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "scores": {
                    "overall": 0.82,
                    "by_component": {
                        "html": {"score": 0.9},
                        "css": {"score": 0.8},
                        "js": {"score": 0.75},
                    },
                },
                "score_evidence": {
                    "confidence": {"level": "high"},
                    "review": {"recommended": False},
                },
                "findings": [{"id": "HTML.REQ.FAIL"}],
                "metadata": {
                    "submission_metadata": {
                        "student_id": "student1",
                        "assignment_id": "assignment1",
                        "original_filename": "student1_assignment1.zip",
                        "timestamp": "2026-03-24T10:00:00Z",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "mark",
            "profile": "frontend",
            "created_at": "2026-03-24T10:00:00Z",
            "report": "report.json",
        },
    )

    response = client.get(f"/run/{run_id}/export/pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"] == f'attachment; filename="report_frontend_{run_id}.pdf"'
    assert response.data.startswith(b"%PDF-")

def test_mark_form_shows_assignment_dropdown_and_hides_profile_selector_for_teacher_view(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment_options(
        monkeypatch,
        [
            {"assignmentID": "assignment1", "title": "Assignment 1", "profile": "frontend_interactive"},
            {"assignmentID": "assignment2", "title": "Assignment 2", "profile": "fullstack_php_sql"},
        ],
    )

    response = client.get("/mark")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'select id="assignment_id"' in body
    assert "assignment1 - Assignment 1" in body
    assert "assignment2 - Assignment 2" in body
    assert "Assessment profile" not in body
    assert 'name="profile"' not in body

def test_mark_and_batch_forms_hide_released_assignments(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment_options(
        monkeypatch,
        [
            {"assignmentID": "assignment_open", "title": "Open Assignment", "profile": "frontend_interactive", "marks_released": False},
            {"assignmentID": "assignment_released", "title": "Released Assignment", "profile": "frontend_interactive", "marks_released": True},
        ],
    )

    mark_response = client.get("/mark")
    assert mark_response.status_code == 200
    mark_body = mark_response.get_data(as_text=True)
    assert "assignment_open - Open Assignment" in mark_body
    assert "assignment_released - Released Assignment" not in mark_body

    batch_response = client.get("/batch")
    assert batch_response.status_code == 200
    batch_body = batch_response.get_data(as_text=True)
    assert "assignment_open" in batch_body
    assert "Open Assignment" in batch_body
    assert "assignment_released" not in batch_body
    assert "Released Assignment" not in batch_body

def test_student_mark_form_hides_released_assignments(tmp_path: Path, monkeypatch) -> None:
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
    _stub_student_assignment_options(
        monkeypatch,
        [
            {
                "assignmentID": "assignment_open",
                "title": "Open Assignment",
                "profile": "frontend_interactive",
                "due_date": "2027-04-01T12:00",
                "marks_released": False,
                "assigned_students": ["student1"],
            },
            {
                "assignmentID": "assignment_released",
                "title": "Released Assignment",
                "profile": "frontend_interactive",
                "due_date": "2027-04-01T12:00",
                "marks_released": True,
                "assigned_students": ["student1"],
            },
        ],
    )

    response = client.get("/mark")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Open Assignment - Due 2027-04-01 12:00" in body
    assert "Released Assignment - Due 2027-04-01 12:00" not in body

def test_mark_route_resolves_profile_from_selected_assignment(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _capture_job_submission(monkeypatch)
    _stub_assignment_options(
        monkeypatch,
        [{"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql"}],
    )

    bundle = _make_zip({"index.html": "<!doctype html><html><body>ok</body></html>"})

    response = client.post(
        "/mark",
        data={
            "student_id": "student1",
            "assignment_id": "assignment1",
            "submission_method": "upload",
            "submission": (io.BytesIO(bundle), "student1_assignment1.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    run_info_path = next(tmp_path.rglob("run_info.json"))
    run_info = json.loads(run_info_path.read_text(encoding="utf-8"))
    assert run_info["profile"] == "fullstack_php_sql"
    assert run_info["assignment_id"] == "assignment1"

def test_mark_route_rejects_new_submission_when_grades_released(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment_options(
        monkeypatch,
        [{"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql", "marks_released": True}],
    )

    bundle = _make_zip({"index.html": "<!doctype html><html><body>ok</body></html>"})
    response = client.post(
        "/mark",
        data={
            "student_id": "student1",
            "assignment_id": "assignment1",
            "submission_method": "upload",
            "submission": (io.BytesIO(bundle), "student1_assignment1.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 403
    body = response.get_data(as_text=True)
    assert "Grades have already been released for this assignment, so new submissions are locked." in body
    assert not list(tmp_path.rglob("run_info.json"))

def test_student_pages_link_assignment_ids_to_submission_reports(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
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
    monkeypatch.setattr(
        "ams.web.routes_student.list_assignments_for_student",
        lambda student_id: [
            {
                "assignmentID": "assignment1",
                "title": "Assignment 1",
                "profile": "frontend",
                "due_date": "2026-03-01T12:00",
                "assigned_students": ["student1"],
            },
            {
                "assignmentID": "assignment2",
                "title": "Assignment 2",
                "profile": "fullstack",
                "due_date": "2026-03-01T12:00",
                "assigned_students": ["student1"],
            },
        ],
    )
    monkeypatch.setattr(
        "ams.web.routes_student.get_assignment",
        lambda assignment_id: {"assignmentID": assignment_id, "marks_released": True},
    )
    monkeypatch.setattr("ams.web.routes_student.get_runs_root", lambda app: tmp_path)
    monkeypatch.setattr(
        "ams.web.routes_student.list_runs",
        lambda _runs_root, only_active=True: [
            {
                "id": "20260320-090000_mark_frontend_old",
                "mode": "mark",
                "profile": "frontend",
                "created_at": "2026-03-20T09:00:00Z",
                "assignment_id": "assignment1",
                "student_id": "student1",
                "score": 55.0,
                "status": "completed",
            },
            {
                "id": "20260321-120000_batch_fullstack_visible",
                "mode": "batch",
                "profile": "fullstack",
                "created_at": "2026-03-21T12:00:00Z",
                "assignment_id": "assignment2",
                "status": "completed",
                "submissions": [
                    {
                        "submission_id": "student1_assignment2",
                        "student_id": "student1",
                        "assignment_id": "assignment2",
                        "overall": 0.72,
                    }
                ],
            },
            {
                "id": "20260322-140000_mark_frontend_new",
                "mode": "mark",
                "profile": "frontend",
                "created_at": "2026-03-22T14:00:00Z",
                "assignment_id": "assignment1",
                "student_id": "student1",
                "score": 81.0,
                "status": "completed",
            },
        ],
    )

    dashboard = client.get("/student/")
    assert dashboard.status_code == 200
    dashboard_body = dashboard.get_data(as_text=True)
    assert '<a href="/runs/20260322-140000_mark_frontend_new" class="table-title-link"><code>assignment1</code></a>' in dashboard_body
    assert '<a href="/batch/20260321-120000_batch_fullstack_visible/submissions/student1_assignment2/view" class="table-title-link"><code>assignment2</code></a>' in dashboard_body
    assert '/runs/20260320-090000_mark_frontend_old' not in dashboard_body

    coursework = client.get("/student/coursework")
    assert coursework.status_code == 200
    coursework_body = coursework.get_data(as_text=True)
    completed_section = coursework_body.split("My submissions", 1)[0]
    assert '<a href="/runs/20260322-140000_mark_frontend_new" class="table-title-link"><code>assignment1</code></a>' in completed_section
    assert '<a href="/batch/20260321-120000_batch_fullstack_visible/submissions/student1_assignment2/view" class="table-title-link"><code>assignment2</code></a>' in completed_section
    assert '/runs/20260320-090000_mark_frontend_old' not in completed_section

def test_student_submission_report_shows_view_analytics_button_when_marks_released(tmp_path: Path, monkeypatch) -> None:
    run_id, _run_dir = _seed_mark_run(tmp_path, assignment_id="assignment1", student_id="student1")
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
    monkeypatch.setattr(
        "ams.web.routes_runs.get_assignment",
        lambda assignment_id: {
            "assignmentID": assignment_id,
            "title": "Assignment 1",
            "profile": "frontend",
            "marks_released": True,
        },
    )

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/student/assignment/assignment1/analytics"' in body
    assert "View analytics" in body

def test_reprocessing_llm_error_submission_unblocks_grade_release(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, run_dir = _seed_mark_llm_error_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student2"])
    queued = _capture_job_submission(monkeypatch)
    seen_skip_flags: list[bool] = []

    class _RecoveredPipeline:
        def __init__(self, scoring_mode=None):
            self.scoring_mode = scoring_mode

        def run(self, submission_path, workspace_path, profile, metadata, skip_threat_scan=False):
            seen_skip_flags.append(skip_threat_scan)
            report_path = Path(workspace_path) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scores": {"overall": 0.71, "by_component": {"html": {"score": 0.71}}},
                        "findings": [{"id": "HTML.REQ.PASS", "severity": "INFO"}],
                        "metadata": {
                            "submission_metadata": metadata,
                            "student_identity": {
                                "student_id": metadata.get("student_id"),
                                "name_normalized": metadata.get("student_id"),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (Path(workspace_path) / "summary.txt").write_text("rerun summary", encoding="utf-8")
            return report_path

    monkeypatch.setattr("ams.web.routes_marking.AssessmentPipeline", _RecoveredPipeline)

    response = client.post(
        "/teacher/assignment/assignment1/submissions/rerun",
        data={"run_id": run_id},
        headers={"X-AMS-Async": "1"},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == "job-queued-1"
    assert payload["refresh_url"].endswith("/teacher/assignment/assignment1")

    queued_run_info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    assert queued_run_info["status"] == "pending"
    assert queued_run_info["llm_error_flagged"] is True

    queued["func"]()

    response = client.get("/teacher/assignment/assignment1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "One or more submissions failed during LLM-assisted marking." not in body
    assert "71%" in body
    assert "aria-disabled=\"true\"" not in body
    assert seen_skip_flags == [True]

    run_info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    assert run_info["status"] == "completed"
    assert run_info["llm_error_flagged"] is False

def test_rerun_single_submission_updates_report_and_detail_view(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, run_dir = _seed_mark_run(tmp_path)
    queued = _capture_job_submission(monkeypatch)
    seen_skip_flags: list[bool] = []

    class _RerunPipeline:
        def __init__(self, scoring_mode=None):
            self.scoring_mode = scoring_mode

        def run(self, submission_path, workspace_path, profile, metadata, skip_threat_scan=False):
            seen_skip_flags.append(skip_threat_scan)
            report_path = Path(workspace_path) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scores": {
                            "overall": 0.64,
                            "by_component": {
                                "html": {"score": 0.64},
                                "css": {"score": 0.5},
                                "js": {"score": 0.45},
                            },
                        },
                        "findings": [{"id": "HTML.REQ.PASS", "severity": "INFO"}],
                        "metadata": {
                            "submission_metadata": metadata,
                            "student_identity": {
                                "student_id": metadata.get("student_id"),
                                "name_normalized": metadata.get("student_id"),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (Path(workspace_path) / "summary.txt").write_text("new summary", encoding="utf-8")
            return report_path

    monkeypatch.setattr("ams.web.routes_marking.AssessmentPipeline", _RerunPipeline)

    response = client.post(f"/runs/{run_id}/rerun", headers={"X-AMS-Async": "1"})
    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == "job-queued-1"
    assert payload["refresh_url"].endswith(f"/runs/{run_id}")

    queued_run_info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    assert queued_run_info["status"] == "pending"
    assert queued_run_info["rerun_pending"] is True

    queued_page = client.get(f"/runs/{run_id}")
    queued_body = queued_page.get_data(as_text=True)
    assert "Rerun queued" in queued_body
    assert "Awaiting rerun" in queued_body
    assert "Export Results" not in queued_body  # Export dropdown hidden while rerun pending

    queued["func"]()

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "64%" in body
    assert "Recommended outcome" in body
    assert "Rerun queued" not in body
    assert seen_skip_flags == [True]

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["scores"]["overall"] == 0.64
    run_info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    assert run_info["status"] == "completed"
    assert run_info["last_rerun_at"]

def test_webui_download_only_allows_whitelist(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    run_dir = runs_root / "fake"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_info.json").write_text(json.dumps({"id": "fake", "mode": "mark", "profile": "frontend"}), encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    allowed = client.get("/download/fake/report.json")
    assert allowed.status_code == 200
    denied = client.get("/download/fake/secret.txt")
    assert denied.status_code == 403
