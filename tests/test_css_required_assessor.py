from __future__ import annotations

import json
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(tmp_path: Path, files: dict[str, str], profile: str = "frontend"):
    submission = tmp_path / "sub"
    submission.mkdir()
    for rel, content in files.items():
        dest = submission / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    workspace = tmp_path / "workspace"
    report = AssessmentPipeline().run(submission, workspace, profile=profile)
    data = json.loads(report.read_text(encoding="utf-8"))
    return data


def test_css_required_missing_when_no_css_for_frontend(tmp_path: Path):
    """CSS required should be MISSING (required but absent) for frontend when no CSS files."""
    data = _run_pipeline(tmp_path, {"index.html": "<html></html>"}, profile="frontend")
    ids = [f["id"] for f in data["findings"]]
    # CSS is required for frontend, so missing should be MISSING_FILES, not SKIPPED
    assert "CSS.REQ.MISSING_FILES" in ids or "CSS.MISSING_FILES" in ids


def test_css_required_pass(tmp_path: Path):
    css = "body { color: red; }"
    data = _run_pipeline(tmp_path, {"style.css": css}, profile="frontend")
    ids = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    assert ids
    evidence = ids[0]["evidence"]
    assert evidence["count"] >= 1
    assert evidence["rule_id"] == "css.has_rule_block"


def test_css_required_fail(tmp_path: Path):
    css = "body color: red;"
    data = _run_pipeline(tmp_path, {"style.css": css}, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "CSS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "css.has_rule_block" for f in fails)
