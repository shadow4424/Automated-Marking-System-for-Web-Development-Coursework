from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.webui import create_app, _write_batch_reports_zip, _write_run_index_batch, _write_run_index_mark


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _client(tmp_path: Path):
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    from tests.webui.conftest import authenticate_client

    authenticate_client(client)
    return client, tmp_path


def test_webui_home_ok(tmp_path: Path):
    client, _ = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 302


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
        ],
        "summary": {
            "total_submissions": 1,
            "succeeded": 1,
            "failed": 0,
            "overall_stats": {"mean": 0.8, "median": 0.8, "min": 0.8, "max": 0.8},
            "profile": "frontend",
        },
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
