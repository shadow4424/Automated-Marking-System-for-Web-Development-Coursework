from __future__ import annotations

import json
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline_with_files(tmp_path: Path, files: dict[str, str]):
    submission = tmp_path / "submission"
    submission.mkdir()
    for rel, content in files.items():
        dest = submission / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    workspace = tmp_path / "workspace"
    pipeline = AssessmentPipeline()
    report_path = pipeline.run(submission, workspace, profile="frontend")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data


def test_html_required_missing_when_no_html(tmp_path: Path) -> None:
    """HTML is required for frontend, so missing files should be MISSING_FILES, not SKIPPED."""
    data = _run_pipeline_with_files(tmp_path, {"style.css": "body{}"})

    findings = data["findings"]
    ids = [f["id"] for f in findings]
    # HTML is required for frontend, so should be MISSING_FILES
    assert "HTML.REQ.MISSING_FILES" in ids or "HTML.MISSING_FILES" in ids


def test_html_required_passes_when_all_present(tmp_path: Path) -> None:
    html = "<!doctype html><html><body><form></form><input/><a href='#'>x</a></body></html>"
    data = _run_pipeline_with_files(tmp_path, {"index.html": html})

    req_findings = [f for f in data["findings"] if f["id"].startswith("HTML.REQ.")]
    passed = [f for f in req_findings if f["id"] == "HTML.REQ.PASS"]
    assert len(passed) >= 3
    rule_ids = {f["evidence"]["rule_id"] for f in passed}
    assert {"html.has_form", "html.has_input", "html.has_link"}.issubset(rule_ids)
    counts = {f["evidence"]["selector"]: f["evidence"]["count"] for f in passed}
    assert counts.get("form", 0) >= 1


def test_html_required_fail_when_missing_form(tmp_path: Path) -> None:
    html = "<html><body><input/><a href='#'>x</a></body></html>"
    data = _run_pipeline_with_files(tmp_path, {"index.html": html})

    req_findings = [f for f in data["findings"] if f["id"] == "HTML.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "html.has_form" for f in req_findings)
    missing_form = next(f for f in req_findings if f["evidence"]["rule_id"] == "html.has_form")
    assert missing_form["evidence"]["count"] == 0
    assert missing_form["severity"] == "WARN"
