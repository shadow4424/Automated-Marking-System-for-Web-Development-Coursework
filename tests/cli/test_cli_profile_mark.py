from __future__ import annotations

import json
from pathlib import Path

from ams.cli import main


def _make_submission(tmp_path: Path) -> Path:
    sub = tmp_path / "submission"
    sub.mkdir()
    (sub / "index.html").write_text(
        "<!doctype html><html><head><title>Test</title></head><body><form><input><a href='#'>x</a></form></body></html>",
        encoding="utf-8",
    )
    return sub


def _run_mark(tmp_path: Path, profile: str) -> dict:
    submission = _make_submission(tmp_path)
    workspace = tmp_path / f"workspace-{profile}"
    argv = ["mark", str(submission), "--workspace", str(workspace), "--profile", profile]
    main(argv)
    report = workspace / "report.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    return data


def test_cli_mark_profile_frontend_skips_backend(tmp_path: Path) -> None:
    data = _run_mark(tmp_path, "frontend_interactive")
    by_component = data["scores"]["by_component"]
    assert by_component["php"]["score"] == "SKIPPED"
    assert by_component["sql"]["score"] == "SKIPPED"


def test_cli_mark_profile_fullstack_scores_backend_as_zero(tmp_path: Path) -> None:
    data = _run_mark(tmp_path, "fullstack_php_sql")
    by_component = data["scores"]["by_component"]
    assert by_component["php"]["score"] == 0.0
    assert by_component["sql"]["score"] == 0.0
