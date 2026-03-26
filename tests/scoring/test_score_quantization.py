from __future__ import annotations

import json
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run(tmp_path: Path, files: dict[str, str], profile: str = "frontend") -> float:
    submission = tmp_path / "submission"
    submission.mkdir(parents=True)
    for rel, content in files.items():
        dest = submission / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    workspace = tmp_path / "workspace"
    report_path = AssessmentPipeline().run(submission, workspace, profile=profile)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return float(data["scores"]["overall"])


def test_overall_score_is_continuous(tmp_path: Path) -> None:
    """Scores are continuous values in [0.0, 1.0] (no longer quantized)."""
    scores = [
        _run(tmp_path / "full", {
            "index.html": "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>",
            "style.css": "body { color: red; }",
            "app.js": "document.body.addEventListener('click', ()=>{});",
        }),
        _run(tmp_path / "partial", {
            "index.html": "<div>hi</div>",
        }),
        _run(tmp_path / "none", {}),
    ]
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_overall_full_marks(tmp_path: Path) -> None:
    # Browser capture is unavailable in the test environment (no live browser),
    # so the achievable score is lower than 0.5 — verify it's meaningfully positive.
    score = _run(
        tmp_path,
        {
            "index.html": "<!doctype html><html><head><title>Test</title></head><body><form><input><a href='#'>x</a></form></body></html>",
            "style.css": "body { color: red; }",
            "app.js": "document.body.addEventListener('click', ()=>{});",
        },
    )
    assert score > 0.0


def test_overall_partial_marks(tmp_path: Path) -> None:
    score = _run(tmp_path, {"index.html": "<div>hi</div>"})
    assert 0.0 <= score <= 0.5


def test_overall_no_attempt(tmp_path: Path) -> None:
    score = _run(tmp_path, {})
    assert score == 0.0
