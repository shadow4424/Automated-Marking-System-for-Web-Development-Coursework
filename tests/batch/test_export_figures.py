from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from ams.tools.export_figures import export_figures


def test_export_figures_creates_outputs(tmp_path: Path) -> None:
    run_id = "run1"
    run_dir = tmp_path / run_id / "analytics"
    run_dir.mkdir(parents=True, exist_ok=True)
    analytics = {
        "overall": {"buckets": {"No attempt (0%)": 1, "Partial (1–50%)": 0, "Good partial (51–99%)": 0, "Full marks (100%)": 1}},
        "components": {
            "html": {"average": 1, "pct_zero": 0, "pct_half": 0, "pct_full": 100, "skipped": 0},
            "css": {"average": 0.5, "pct_zero": 50, "pct_half": 50, "pct_full": 0, "skipped": 0},
        },
        "needs_attention": [{"submission_id": "s1", "overall": 0.0, "reason": "missing"}],
    }
    (run_dir / f"batch_analytics_{run_id}.json").write_text(json.dumps(analytics), encoding="utf-8")
    out_dir = tmp_path / "figs"
    export_figures(run_id=run_id, runs_root=tmp_path, out_dir=out_dir)
    assert (out_dir / "score_distribution.csv").exists()
    assert (out_dir / "score_distribution.png").read_bytes().startswith(b"\x89PNG")
    assert (out_dir / "component_readiness.png").read_bytes().startswith(b"\x89PNG")
    assert (out_dir / "needs_attention_top_reasons.png").read_bytes().startswith(b"\x89PNG")
    assert (out_dir / "needs_attention.csv").exists()
