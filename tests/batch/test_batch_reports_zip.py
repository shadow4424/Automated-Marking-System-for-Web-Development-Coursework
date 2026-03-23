from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ams.webui import _write_batch_reports_zip


def test_batch_reports_zip_includes_batch_outputs_without_legacy_analytics_folder(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "batch_summary.csv").write_text("id,overall\ns1,1.0\n", encoding="utf-8")

    runs_subdir = run_dir / "runs" / "s1"
    runs_subdir.mkdir(parents=True)
    report_path = runs_subdir / "report.json"
    report_path.write_text("{}", encoding="utf-8")

    summary = {
        "records": [{"id": "s1", "report_path": str(report_path)}],
    }
    (run_dir / "batch_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    _write_batch_reports_zip(run_dir, profile="frontend", run_id="runid")

    zip_path = run_dir / "batch_reports_frontend_runid.zip"
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

    assert "runid/batch_summary.json" in names
    assert "runid/batch_summary.csv" in names
    assert "runid/submissions/s1/report.json" in names
    assert not any(name.startswith("runid/analytics/") for name in names)
