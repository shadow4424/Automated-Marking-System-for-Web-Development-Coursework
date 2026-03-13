from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.tools.batch import run_batch
from ams.webui import create_app, _write_batch_analytics, _write_batch_reports_zip, _write_run_index_mark


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _client(tmp_path: Path):
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    # Authenticate as admin so RBAC decorators don't redirect
    from tests.webui.conftest import authenticate_client
    authenticate_client(client)
    return client, tmp_path


def test_webui_home_ok(tmp_path: Path):
    client, _ = _client(tmp_path)
    res = client.get("/")
    # Authenticated home redirects to role-based dashboard
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


def test_webui_batch_zip_happy_path_creates_run_and_lists_outputs(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    submissions_dir = tmp_path / "subs"
    submissions_dir.mkdir()
    (submissions_dir / "s1").mkdir()
    (submissions_dir / "s1" / "index.html").write_text("<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>", encoding="utf-8")
    (submissions_dir / "s1" / "style.css").write_text("body { color: red; }", encoding="utf-8")
    (submissions_dir / "s1" / "app.js").write_text("document.body.addEventListener('click', ()=>{});", encoding="utf-8")
    (submissions_dir / "s2").mkdir()
    (submissions_dir / "s2" / "index.html").write_text("<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>", encoding="utf-8")

    run_id, run_dir = create_run_dir(runs_root, mode="batch", profile="frontend")
    summary = run_batch(submissions_dir=submissions_dir, out_root=run_dir, profile="frontend", keep_individual_runs=True)
    run_info = {
        "id": run_id,
        "mode": "batch",
        "profile": "frontend",
        "created_at": "now",
        "summary": "batch_summary.json",
        "batch_summary": summary,
    }
    _write_batch_analytics(run_dir, "frontend", run_id)
    _write_batch_reports_zip(run_dir, "frontend", run_id)
    _write_run_index_mark(run_dir, run_info, run_dir / "batch_summary.json")
    save_run_info(run_dir, run_info)

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert b"Batch Summary" in detail.data
    assert any(runs_root.iterdir())
    dl = client.get(f"/download/{run_id}/batch_summary.json")
    assert dl.status_code == 200
    cd = dl.headers["Content-Disposition"].encode("utf-8")
    assert run_id.encode() in cd
    assert b"frontend" in cd


def test_webui_download_only_allows_whitelist(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    # Create a fake run with a report.json
    run_dir = runs_root / "fake"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_info.json").write_text(json.dumps({"id": "fake", "mode": "mark", "profile": "frontend"}), encoding="utf-8")
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    allowed = client.get("/download/fake/report.json")
    assert allowed.status_code == 200
    denied = client.get("/download/fake/secret.txt")
    assert denied.status_code == 403
