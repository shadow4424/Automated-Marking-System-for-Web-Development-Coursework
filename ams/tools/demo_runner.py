from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ams.tools.evaluation import evaluate
from ams.io.web_storage import create_run_dir, save_run_info
from ams.tools.batch import run_batch
from ams.tools.export_figures import export_figures
from demo.build_demo_batch import build_demo

# Reuse batch helpers from the web UI to stay consistent with analytics/report packaging
from ams.webui import _write_batch_analytics, _write_batch_reports_zip, _write_run_index_batch


def run_demo(profile: str = "fullstack", runs_root: Path | str = Path("demo_out"), demo_root: Path | str = Path("demo")) -> dict:
    runs_root = Path(runs_root)
    demo_root = Path(demo_root)
    runs_root.mkdir(parents=True, exist_ok=True)

    batch_zip = build_demo(demo_root)
    run_id, run_dir = create_run_dir(runs_root, mode="batch", profile=profile)

    # Run batch marking on the demo bundle
    result = run_batch(submissions_dir=batch_zip, out_root=run_dir, profile=profile, keep_individual_runs=True)
    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run_info = {
        "id": run_id,
        "mode": "batch",
        "profile": profile,
        "created_at": created_at,
        "summary": "batch_summary.json",
        "batch_summary": summary,
    }
    save_run_info(run_dir, run_info)
    _write_run_index_batch(run_dir, run_info)

    # Analytics + artifacts
    _write_batch_analytics(run_dir, profile, run_id)
    _write_batch_reports_zip(run_dir, profile, run_id)
    analytics_dir = run_dir / "analytics"
    try:
        export_figures(run_id=run_id, runs_root=runs_root, out_dir=analytics_dir)
    except RuntimeError:
        # Keep demo flow running even if optional plotting deps are missing
        pass

    # Evaluation harness against demo expectations
    eval_out = run_dir / "evaluation"
    evaluate(fixtures_root=demo_root, out_root=eval_out, profile="all")

    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "analytics_dir": analytics_dir,
        "figures_dir": analytics_dir,
        "evaluation_dir": eval_out,
    }


__all__ = ["run_demo"]
