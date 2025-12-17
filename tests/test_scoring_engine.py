from __future__ import annotations

import json
from pathlib import Path
import pytest

from ams.pipeline import AssessmentPipeline


def run_pipeline_with_files(tmp_path: Path, files: dict[str, str]):
    submission = tmp_path / "submission"
    submission.mkdir()
    for relative, content in files.items():
        dest = submission / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    workspace = tmp_path / "workspace"
    pipeline = AssessmentPipeline()
    report_path = pipeline.run(submission, workspace)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data, workspace


def test_scoring_all_missing_scores_zero(tmp_path):
    data, _ = run_pipeline_with_files(tmp_path, {})

    assert data["scores"]["overall"] == pytest.approx(0.0)
    for component in ["html", "css", "js", "php", "sql"]:
        assert data["scores"]["by_component"][component]["score"] == 0.0


def test_scoring_html_only_partial(tmp_path):
    data, _ = run_pipeline_with_files(tmp_path, {"index.html": "<div>hi</div>"})

    assert data["scores"]["by_component"]["html"]["score"] == 0.5
    others = ["css", "js", "php", "sql"]
    for component in others:
        assert data["scores"]["by_component"][component]["score"] == 0.0
    assert data["scores"]["overall"] == pytest.approx(0.5 / 5)


def test_scoring_html_css_js_good_attempt(tmp_path):
    files = {
        "index.html": "<!doctype html><html><head><title>Test</title></head><body>Hello</body></html>",
        "style.css": "body { color: red; }",
        "app.js": "function init(){const el=document.querySelector('body');el.addEventListener('click',()=>{});}",
    }
    data, _ = run_pipeline_with_files(tmp_path, files)

    assert data["scores"]["by_component"]["html"]["score"] == 1.0
    assert data["scores"]["by_component"]["css"]["score"] == 1.0
    assert data["scores"]["by_component"]["js"]["score"] == 1.0
    assert data["scores"]["by_component"]["php"]["score"] == 0.0
    assert data["scores"]["by_component"]["sql"]["score"] == 0.0
    assert data["scores"]["overall"] == pytest.approx(3 / 5)


def test_summary_txt_created(tmp_path):
    data, workspace = run_pipeline_with_files(tmp_path, {"index.html": "<p>hi</p>"})

    summary_path = workspace / "summary.txt"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "Overall" in content

