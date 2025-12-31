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


def test_js_required_missing_when_no_js(tmp_path: Path):
    """JS is required for frontend, so missing files should be MISSING_FILES, not SKIPPED."""
    data = _run_pipeline(tmp_path, {"index.html": "<html></html>"}, profile="frontend")
    ids = [f["id"] for f in data["findings"]]
    # JS is required for frontend, so should be MISSING_FILES
    assert "JS.REQ.MISSING_FILES" in ids or "JS.MISSING_FILES" in ids


def test_js_required_pass(tmp_path: Path):
    js = "document.body.addEventListener('click', ()=>{});"
    data = _run_pipeline(tmp_path, {"app.js": js}, profile="frontend")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    assert passes
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.has_event_listener" in rule_ids


def test_js_required_fail(tmp_path: Path):
    js = "console.log('x');"
    data = _run_pipeline(tmp_path, {"app.js": js}, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "JS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "js.has_event_listener" for f in fails)
