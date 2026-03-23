from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.webui import create_app, _write_batch_reports_zip, _write_run_index_batch, _write_run_index_mark
from tests.webui.conftest import authenticate_client


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
        "ams.web.routes_teacher.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.webui.get_assignment",
        lambda current_assignment_id: dict(assignment, assignmentID=current_assignment_id),
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_teacher.list_users", lambda role=None: [])


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

    monkeypatch.setattr("ams.webui.job_manager.submit_job", _submit_job)
    return captured


def test_webui_home_ok(tmp_path: Path):
    client, _ = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 302


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
    assert b"Overall Score" in detail.data
    assert any(runs_root.iterdir())


def test_webui_batch_run_redirects_to_assignment_and_keeps_batch_downloads(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    run_id = "20260319-100000_batch_frontend_demo"
    run_dir = runs_root / "assignment1" / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "runs" / "student1_assignment1" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "submission_metadata": {
                        "student_id": "student1",
                        "assignment_id": "assignment1",
                        "original_filename": "student1_assignment1.zip",
                        "timestamp": "2026-03-19T10:00:00Z",
                    },
                    "student_identity": {"student_id": "student1", "name_normalized": "student1"},
                }
            }
        ),
        encoding="utf-8",
    )

    batch_summary = {
        "records": [
            {
                "id": "student1_assignment1",
                "student_id": "student1",
                "assignment_id": "assignment1",
                "original_filename": "student1_assignment1.zip",
                "upload_timestamp": "2026-03-19T10:00:00Z",
                "overall": 0.8,
                "components": {"html": 0.8, "css": 0.8, "js": 0.8, "php": None, "sql": None},
                "status": "ok",
                "report_path": str(report_path),
            }
        ]
    }
    (run_dir / "batch_summary.json").write_text(json.dumps(batch_summary), encoding="utf-8")
    (run_dir / "batch_summary.csv").write_text("id,overall\nstudent1_assignment1,0.8\n", encoding="utf-8")
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": "now",
            "assignment_id": "assignment1",
            "summary": "batch_summary.json",
            "batch_summary": batch_summary,
        },
    )
    _write_run_index_batch(run_dir, {"id": run_id, "mode": "batch", "profile": "frontend"})
    _write_batch_reports_zip(run_dir, "frontend", run_id)

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 302
    assert detail.headers["Location"].endswith("/teacher/assignment/assignment1")

    dl = client.get(f"/download/{run_id}/batch_summary.json")
    assert dl.status_code == 200
    cd = dl.headers["Content-Disposition"].encode("utf-8")
    assert run_id.encode() in cd
    assert b"frontend" in cd


def test_batch_form_shows_assignment_dropdown_and_hides_profile_selector(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    monkeypatch.setattr(
        "ams.webui.list_assignments",
        lambda teacher_id=None: [
            {"assignmentID": "assignment1", "title": "Assignment 1", "profile": "frontend_interactive"},
            {"assignmentID": "assignment2", "title": "Assignment 2", "profile": "fullstack_php_sql"},
        ],
    )

    response = client.get("/batch")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'select id="assignment_id"' in body
    assert "assignment1 — Assignment 1" in body
    assert "assignment2 — Assignment 2" in body
    assert "Assessment Profile" not in body
    assert 'name="profile"' not in body


def test_mark_form_shows_assignment_dropdown_and_hides_profile_selector_for_teacher_view(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    monkeypatch.setattr(
        "ams.webui.list_assignments",
        lambda teacher_id=None: [
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


def test_batch_route_resolves_profile_from_selected_assignment(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    queued = _capture_job_submission(monkeypatch)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "ams.webui.list_assignments",
        lambda teacher_id=None: [
            {"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql"},
        ],
    )

    def _run_batch_stub(**kwargs):
        captured.update(kwargs)
        return {"records": []}

    monkeypatch.setattr("ams.webui.run_batch", _run_batch_stub)

    inner_submission = _make_zip({"index.html": "<!doctype html><html><body>ok</body></html>"})
    batch_bundle = _make_zip({"student1_assignment1.zip": inner_submission})

    response = client.post(
        "/batch",
        data={
            "assignment_id": "assignment1",
            "profile": "frontend_basic",
            "submission_method": "upload",
            "submission": (io.BytesIO(batch_bundle), "batch_submissions.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    queued["func"]()
    assert captured["profile"] == "fullstack_php_sql"
    assert captured["assignment_id"] == "assignment1"


def test_mark_route_resolves_profile_from_selected_assignment(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _capture_job_submission(monkeypatch)
    monkeypatch.setattr(
        "ams.webui.list_assignments",
        lambda teacher_id=None: [
            {"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql"},
        ],
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

    monkeypatch.setattr("ams.web.routes_teacher.create_assignment", _create_assignment_stub)
    monkeypatch.setattr("ams.web.routes_teacher.list_users", _list_users_stub)

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
        "ams.web.routes_teacher.get_assignment",
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
        "ams.web.routes_teacher.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_teacher.list_users", lambda role=None: [])
    monkeypatch.setattr("ams.web.routes_teacher.get_runs_root", lambda app: tmp_path)
    monkeypatch.setattr(
        "ams.web.routes_teacher.list_runs",
        lambda _runs_root: [
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
        "ams.web.routes_teacher.get_assignment",
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
        "ams.web.routes_teacher.get_user",
        lambda student_id: {"userID": student_id, "firstName": student_id, "lastName": "", "email": f"{student_id}@example.com"},
    )
    monkeypatch.setattr("ams.web.routes_teacher.list_users", lambda role=None: [])

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
    monkeypatch.setattr("ams.web.routes_teacher.release_marks", lambda assignment_id: released.append(assignment_id))

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
    monkeypatch.setattr("ams.web.routes_teacher.release_marks", lambda assignment_id: released.append(assignment_id))

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

    monkeypatch.setattr("ams.web.routes_teacher.delete_assignment", lambda assignment_id: True)

    response = client.post("/teacher/assignment/assignment1/delete")

    assert response.status_code == 302
    assert not run_dir.exists()
    assert not (runs_root / "assignment1").exists()
    assert not legacy_run_dir.exists()


def test_reprocessing_flagged_batch_submission_unblocks_grade_release(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])
    queued = _capture_job_submission(monkeypatch)
    seen_skip_flags: list[bool] = []

    class _SafePipeline:
        def __init__(self, scoring_mode=None):
            self.scoring_mode = scoring_mode

        def run(self, submission_path, workspace_path, profile, metadata, skip_threat_scan=False):
            seen_skip_flags.append(skip_threat_scan)
            report_path = Path(workspace_path) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scores": {
                            "overall": 0.82,
                            "by_component": {
                                "html": {"score": 0.8},
                                "css": {"score": 0.85},
                                "js": {"score": 0.8},
                                "php": {"score": 0.8},
                                "sql": {"score": 0.85},
                            },
                        },
                        "findings": [{"id": "HTML.REQ.PASS", "severity": "INFO"}],
                        "metadata": {
                            "submission_metadata": metadata,
                            "student_identity": {
                                "student_id": metadata.get("student_id"),
                                "name_normalized": metadata.get("student_id"),
                            },
                            "threat_override": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return report_path

    monkeypatch.setattr("ams.webui.AssessmentPipeline", _SafePipeline)

    response = client.post(
        "/teacher/assignment/assignment1/threats/reprocess",
        data={"run_id": run_id, "submission_id": submission_id},
        headers={"X-AMS-Async": "1"},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == "job-queued-1"
    assert payload["view_url"].endswith(f"/batch/{run_id}/submissions/{submission_id}/view")

    queued_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    queued_record = queued_summary["records"][0]
    assert queued_record["status"] == "pending"
    assert queued_record["overall"] is None
    assert queued_record["rerun_pending"] is True

    queued["func"]()

    response = client.get("/teacher/assignment/assignment1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Grade release blocked" not in body
    assert "82%" in body
    assert "THREAT" not in body
    assert seen_skip_flags == [True]

    batch_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    record = batch_summary["records"][0]
    assert record["overall"] == 0.82
    assert record["threat_flagged"] is False
    assert "threat_count" not in record


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

    monkeypatch.setattr("ams.webui.AssessmentPipeline", _RecoveredPipeline)

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


def test_assignment_detail_shows_rerun_action_for_every_submission(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student1", "student2"])
    monkeypatch.setattr(
        "ams.web.routes_teacher.get_runs_root",
        lambda app: tmp_path,
    )
    monkeypatch.setattr(
        "ams.web.routes_teacher.list_runs",
        lambda _runs_root: [
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

    monkeypatch.setattr("ams.webui.AssessmentPipeline", _RerunPipeline)

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
    assert "Overall Score" not in queued_body
    assert "Download JSON" not in queued_body

    queued["func"]()

    response = client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "64%" in body
    assert "Rerun queued" not in body
    assert seen_skip_flags == [True]

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["scores"]["overall"] == 0.64
    run_info = json.loads((run_dir / "run_info.json").read_text(encoding="utf-8"))
    assert run_info["status"] == "completed"
    assert run_info["last_rerun_at"]


def test_threat_detail_page_only_shows_one_rerun_button(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, _run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])

    response = client.get(f"/batch/{run_id}/submissions/{submission_id}/view")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("Rerun Submission") == 1


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
