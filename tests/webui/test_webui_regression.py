from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.config import ScoringMode
from ams.tools.batch import run_batch
from ams.webui import create_app, _write_run_index_mark
from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.io.web_storage import safe_extract_zip, find_submission_root
from tests.webui.conftest import authenticate_client


def _make_submission_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>")
        zf.writestr("style.css", "body { color: red; }")
        zf.writestr("app.js", "document.body.addEventListener('click', ()=>{});")
    return buf.getvalue()


def test_web_mark_and_batch_same_score(tmp_path: Path) -> None:
    zip_bytes = _make_submission_zip()

    # Run via batch path
    submissions_dir = tmp_path / "subs"
    submissions_dir.mkdir()
    (submissions_dir / "s1_assignment1.zip").write_bytes(zip_bytes)
    batch_out = tmp_path / "batch_out"
    summary = run_batch(
        submissions_dir=submissions_dir,
        out_root=batch_out,
        profile="frontend",
        keep_individual_runs=True,
        scoring_mode=ScoringMode.STATIC_ONLY,
    )
    batch_summary = json.loads((batch_out / "batch_summary.json").read_text(encoding="utf-8"))
    batch_overall = batch_summary["records"][0]["overall"]

    runs_root = tmp_path / "web_runs"
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": runs_root})
    client = app.test_client()
    authenticate_client(client)
    run_id, run_dir = create_run_dir(runs_root, mode="mark", profile="frontend")

    extract_root = run_dir / "uploaded_extract"
    extract_root.mkdir()
    upload_zip = run_dir / "submission.zip"
    upload_zip.write_bytes(zip_bytes)
    safe_extract_zip(upload_zip, extract_root)
    submission_root = find_submission_root(extract_root)
    report_path = AssessmentPipeline().run(submission_root, run_dir, profile="frontend")
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
    report = json.loads(report_path.read_text(encoding="utf-8"))
    web_overall = report["scores"]["overall"]

    assert batch_overall == web_overall
