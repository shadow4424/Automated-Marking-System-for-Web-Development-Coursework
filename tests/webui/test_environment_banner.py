from __future__ import annotations

import json
from pathlib import Path

from ams.webui import create_app
from tests.webui.conftest import authenticate_client


def _make_run(tmp_path: Path, run_id: str, report: dict) -> None:
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_info.json").write_text(json.dumps({"id": run_id, "mode": "mark", "profile": "fullstack", "created_at": "now"}), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")


def test_environment_banner_shown_when_runtime_skipped(tmp_path: Path) -> None:
    report = {
        "scores": {"overall": 1.0, "by_component": {"html": {"score": 1}}},
        "behavioural_evidence": [
            {"test_id": "PHP.SMOKE", "status": "skipped", "stderr": "php binary not available", "component": "php", "duration_ms": 0, "inputs": {}, "outputs": {}, "artifacts": {}}
        ],
        "browser_evidence": [],
        "environment": {"php_available": False, "browser_available": True, "behavioural_tests_run": False, "browser_tests_run": True},
        "metadata": {},
    }
    _make_run(tmp_path, "rid", report)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)
    res = client.get("/runs/rid")
    body = res.get_data(as_text=True)
    assert "runtime checks were unavailable" in body.lower()


def test_environment_banner_not_shown_when_all_available(tmp_path: Path) -> None:
    report = {
        "scores": {"overall": 1.0, "by_component": {"html": {"score": 1}}},
        "behavioural_evidence": [],
        "browser_evidence": [],
        "environment": {"php_available": True, "browser_available": True, "behavioural_tests_run": True, "browser_tests_run": True},
        "metadata": {},
    }
    _make_run(tmp_path, "rid2", report)
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)
    res = client.get("/runs/rid2")
    body = res.get_data(as_text=True)
    assert "runtime checks were unavailable" not in body.lower()
