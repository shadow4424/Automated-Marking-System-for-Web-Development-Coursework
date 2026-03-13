from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ams.webui import create_app, _write_batch_reports_zip
from tests.webui.conftest import authenticate_client


def _write_batch_summary(run_dir: Path, run_id: str) -> None:
    summary = {
        "total_submissions": 1,
        "succeeded": 1,
        "failed": 0,
        "overall_stats": {"mean": 1.0, "median": 1.0, "min": 1.0, "max": 1.0},
        "buckets": {"zero": 0, "gt_0_to_0_5": 0, "gt_0_5_to_1": 0, "one": 1},
        "finding_frequency": {},
        "profile": "frontend",
    }
    record = {
        "id": "s1",
        "overall": 1.0,
        "components": {"html": 1, "css": 1, "js": 1, "php": "SKIPPED", "sql": "SKIPPED"},
        "status": "ok",
        "report_path": str(run_dir / "runs" / "s1" / "report.json"),
    }
    batch_summary = {"records": [record], "summary": summary}
    (run_dir / "batch_summary.json").write_text(json.dumps(batch_summary, indent=2), encoding="utf-8")


def test_batch_analytics_route_generates_on_demand(tmp_path: Path) -> None:
    run_id = "20250101-000000_batch_frontend_deadbeef"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_info.json").write_text(
        json.dumps({"id": run_id, "mode": "batch", "profile": "frontend", "created_at": "2025-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    _write_batch_summary(run_dir, run_id)

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)
    res = client.get(f"/batch/{run_id}/analytics")
    assert res.status_code == 200
    analytics_path = run_dir / "analytics" / f"batch_analytics_{run_id}.json"
    assert analytics_path.exists()


def test_batch_zip_contains_required_structure(tmp_path: Path) -> None:
    run_id = "20250101-000000_batch_frontend_deadbeef"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "runs" / "s1" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("{}", encoding="utf-8")
    _write_batch_summary(run_dir, run_id)

    _write_batch_reports_zip(run_dir, "frontend", run_id)
    zip_path = run_dir / f"batch_reports_frontend_{run_id}.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
    assert f"{run_id}/batch_summary.json" in names
    assert f"{run_id}/analytics/batch_analytics_{run_id}.json" in names
    assert f"{run_id}/analytics/batch_analytics_{run_id}.csv" in names
    assert f"{run_id}/analytics/component_breakdown_{run_id}.csv" in names
    assert f"{run_id}/analytics/needs_attention_{run_id}.csv" in names
    assert f"{run_id}/submissions/s1/report.json" in names
    assert f"{run_id}/README.txt" in names
