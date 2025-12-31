from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from ams.tools.demo_runner import run_demo


def test_run_demo_creates_run_outputs(tmp_path: Path) -> None:
    demo_root = tmp_path / "demo_data"
    runs_root = tmp_path / "demo_runs"
    info = run_demo(profile="fullstack", runs_root=runs_root, demo_root=demo_root)

    analytics_dir: Path = info["analytics_dir"]
    run_id = info["run_id"]
    assert (analytics_dir / f"batch_analytics_{run_id}.json").exists()
    assert (analytics_dir / "score_distribution.png").exists()
    assert (analytics_dir / "component_readiness.png").exists()
    assert (info["evaluation_dir"] / "evaluation_summary.json").exists()
