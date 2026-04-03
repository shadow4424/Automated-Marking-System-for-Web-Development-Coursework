from __future__ import annotations

import json
import os
from pathlib import Path
from time import time

from ams.core.database import init_db
from ams.webui import create_app
from tests.webui.conftest import authenticate_client


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
    authenticate_client(client)
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
    authenticate_client(client)
    res = client.get("/runs?q=11074020")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "Dale Mccance" in body


def _use_temp_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("ams.core.database._DEFAULT_DB_PATH", tmp_path / "ams_users.db")
    init_db()


def _make_attempt_run(
    runs_root: Path,
    *,
    run_id: str,
    attempt_number: int,
    created_at: str,
    score: float,
) -> None:
    run_dir = runs_root / "assignment1" / "student1" / "attempts" / f"{attempt_number:03d}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "scores": {"overall": score, "by_component": {"html": {"score": score}}},
                "summary": {"confidence": "high"},
                "findings": [],
                "metadata": {
                    "submission_metadata": {
                        "student_id": "student1",
                        "assignment_id": "assignment1",
                        "original_filename": "student1_assignment1.zip",
                        "attempt_id": run_id,
                        "attempt_number": attempt_number,
                        "source_type": "student_zip_upload",
                        "timestamp": created_at,
                    },
                    "student_identity": {"student_id": "student1", "name_normalized": "student1"},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_info.json").write_text(
        json.dumps(
            {
                "id": run_id,
                "mode": "mark",
                "profile": "frontend",
                "created_at": created_at,
                "student_id": "student1",
                "assignment_id": "assignment1",
                "original_filename": "student1_assignment1.zip",
                "status": "completed",
                "attempt_id": run_id,
                "attempt_number": attempt_number,
                "source_type": "student_zip_upload",
                "source_actor_user_id": "student1",
                "validity_status": "valid",
                "report": "report.json",
            }
        ),
        encoding="utf-8",
    )


def test_runs_history_shows_active_and_historical_attempts(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="attempt_old",
        attempt_number=1,
        created_at="2026-03-19T09:00:00Z",
        score=0.44,
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_new",
        attempt_number=2,
        created_at="2026-03-20T09:00:00Z",
        score=0.82,
    )

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    res = client.get("/runs")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "attempt_old" in body
    assert "attempt_new" in body
    assert "Attempt 1" in body
    assert "Attempt 2" in body
    assert "Active" in body
    assert "History" in body


def test_create_app_does_not_delete_persisted_runs_on_startup_by_default(tmp_path: Path) -> None:
    run_id = "20250101_mark_frontend_a"
    _make_run(tmp_path, run_id, "mark", "frontend", 1.0)
    run_info_path = tmp_path / run_id / "run_info.json"
    old_mtime = time() - (72 * 3600)
    os.utime(run_info_path, (old_mtime, old_mtime))

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    assert run_info_path.exists()
    res = client.get("/runs")
    assert res.status_code == 200
    assert run_id in res.get_data(as_text=True)


def test_create_app_can_cleanup_old_runs_when_explicitly_enabled(tmp_path: Path) -> None:
    run_id = "20250101_mark_frontend_a"
    _make_run(tmp_path, run_id, "mark", "frontend", 1.0)
    run_info_path = tmp_path / run_id / "run_info.json"
    old_mtime = time() - (72 * 3600)
    os.utime(run_info_path, (old_mtime, old_mtime))

    create_app(
        {
            "TESTING": True,
            "AMS_RUNS_ROOT": tmp_path,
            "AMS_ENABLE_STARTUP_RUN_CLEANUP": True,
            "AMS_STARTUP_RUN_MAX_AGE_HOURS": 24,
        }
    )

    assert not run_info_path.exists()
