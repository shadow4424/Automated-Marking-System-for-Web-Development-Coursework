from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ams.webui import _write_batch_reports_zip


def test_batch_reports_zip_includes_analytics(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    analytics_dir = run_dir / "analytics"
    analytics_dir.mkdir(parents=True)
    (analytics_dir / "score_distribution.png").write_bytes(b"\x89PNG")
    (analytics_dir / "batch_analytics_runid.json").write_text("{}", encoding="utf-8")

    runs_subdir = run_dir / "runs" / "s1"
    runs_subdir.mkdir(parents=True)
    report_path = runs_subdir / "report.json"
    report_path.write_text("{}", encoding="utf-8")

    summary = {"records": [{"id": "s1", "report_path": str(report_path)}], "summary": {"total_submissions": 1, "failed": 0, "succeeded": 1}}
    (run_dir / "batch_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    _write_batch_reports_zip(run_dir, profile="frontend", run_id="runid")

    zip_path = run_dir / "batch_reports_frontend_runid.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert "runid/analytics/score_distribution.png" in names
        assert "runid/submissions/s1/report.json" in names
