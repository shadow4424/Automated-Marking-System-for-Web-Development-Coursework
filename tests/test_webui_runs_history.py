from __future__ import annotations

import json
from pathlib import Path

from ams.webui import create_app


def _make_run(tmp_path: Path, run_id: str, mode: str, profile: str, overall: float | None = None) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    info = {"id": run_id, "mode": mode, "profile": profile, "created_at": "2025-01-01T00:00:00Z", "status": "ok"}
    if overall is not None:
        info["overall"] = overall
    (run_dir / "run_info.json").write_text(json.dumps(info), encoding="utf-8")


def test_runs_history_lists_existing_runs(tmp_path: Path) -> None:
    _make_run(tmp_path, "20250101_mark_frontend_a", "mark", "frontend", 1.0)
    _make_run(tmp_path, "20250102_batch_fullstack_b", "batch", "fullstack", 0.5)

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    res = client.get("/runs")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "20250101_mark_frontend_a" in body
    assert "20250102_batch_fullstack_b" in body


def test_runs_history_searches_student_fields(tmp_path: Path) -> None:
    rid = "20250101_mark_frontend_a"
    _make_run(tmp_path, rid, "mark", "frontend", 1.0)
    index = {
        "run_id": rid,
        "mode": "mark",
        "profile": "frontend",
        "created_at": "2025-01-01T00:00:00Z",
        "overall": 1.0,
        "status": "ok",
        "submissions": [
            {"submission_id": "sub1", "student_name": "Dale Mccance", "student_id": "11074020", "original_filename": "11074020_Dale.zip"}
        ],
    }
    (tmp_path / rid / "run_index.json").write_text(json.dumps(index), encoding="utf-8")

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    res = client.get("/runs?q=11074020")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "Dale Mccance" in body
