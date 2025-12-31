from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.tools.batch import run_batch
from ams.webui import create_app


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
    (submissions_dir / "s1.zip").write_bytes(zip_bytes)
    batch_out = tmp_path / "batch_out"
    summary = run_batch(submissions_dir=submissions_dir, out_root=batch_out, profile="frontend", keep_individual_runs=True)
    batch_summary = json.loads((batch_out / "batch_summary.json").read_text(encoding="utf-8"))
    batch_overall = batch_summary["records"][0]["overall"]

    # Run via web upload path using test client
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path / "web_runs"})
    client = app.test_client()
    data = {"profile": "frontend", "submission": (io.BytesIO(zip_bytes), "student.zip")}
    res = client.post("/mark", data=data, content_type="multipart/form-data", follow_redirects=False)
    assert res.status_code == 302
    detail = client.get(res.headers["Location"])
    assert detail.status_code == 200

    runs_root = Path(app.config["AMS_RUNS_ROOT"])
    run_dirs = sorted(runs_root.iterdir())
    assert run_dirs
    report_path = run_dirs[0] / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    web_overall = report["scores"]["overall"]

    assert batch_overall == web_overall
