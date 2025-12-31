from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ams.webui import create_app


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _client(tmp_path: Path):
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    return app.test_client(), tmp_path


def test_webui_home_ok(tmp_path: Path):
    client, _ = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 200
    assert b"Automated Marking System" in res.data


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
    submission_zip = _make_zip(
        {
            "index.html": "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>",
            "style.css": "body { color: red; }",
            "app.js": "document.body.addEventListener('click', ()=>{});",
        }
    )
    data = {
        "profile": "frontend",
        "submission": (io.BytesIO(submission_zip), "sub.zip"),
    }
    res = client.post("/mark", data=data, content_type="multipart/form-data", follow_redirects=False)
    assert res.status_code == 302
    # Follow to run detail
    detail = client.get(res.headers["Location"])
    assert detail.status_code == 200
    assert b"Scores" in detail.data
    # Ensure run directory created
    assert any(runs_root.iterdir())


def test_webui_batch_zip_happy_path_creates_run_and_lists_outputs(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    batch_zip = _make_zip(
        {
            "s1/index.html": "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>",
            "s1/style.css": "body { color: red; }",
            "s1/app.js": "document.body.addEventListener('click', ()=>{});",
            "s2/index.html": "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>",
        }
    )
    data = {
        "profile": "frontend",
        "submission": (io.BytesIO(batch_zip), "batch.zip"),
    }
    res = client.post("/batch", data=data, content_type="multipart/form-data", follow_redirects=False)
    assert res.status_code == 302
    run_id = res.headers["Location"].split("/")[-1]
    detail = client.get(res.headers["Location"])
    assert detail.status_code == 200
    assert b"Batch Summary" in detail.data
    assert any(runs_root.iterdir())
    # download header should include run_id
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
