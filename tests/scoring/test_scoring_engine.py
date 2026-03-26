from __future__ import annotations

import json
from pathlib import Path
import pytest

from ams.core.pipeline import AssessmentPipeline


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
        score = data["scores"]["by_component"][component]["score"]
        if component in ["html", "css", "js"]:
            assert score == 0.0
        else:
            assert score == "SKIPPED"


def test_scoring_html_only_partial(tmp_path):
    data, _ = run_pipeline_with_files(tmp_path, {"index.html": "<div>hi</div>"})

    assert 0.0 < data["scores"]["by_component"]["html"]["score"] < 1.0
    others = ["css", "js", "php", "sql"]
    for component in others:
        score = data["scores"]["by_component"][component]["score"]
        if component in ["css", "js"]:
            assert score <= 0.1  # may be slightly > 0 due to optional (min_count=0) rules
        else:
            assert score == "SKIPPED"
    assert data["scores"]["overall"] <= 0.15  # minimal HTML, no CSS/JS


def test_scoring_html_css_js_good_attempt(tmp_path):
    files = {
        "index.html": "<!doctype html><html><head><title>Test</title></head><body>Hello</body></html>",
        "style.css": "body { color: red; }",
        "app.js": "function init(){const el=document.querySelector('body');el.addEventListener('click',()=>{});}",
    }
    data, _ = run_pipeline_with_files(tmp_path, files)

    assert data["scores"]["by_component"]["html"]["score"] > 0.0
    assert data["scores"]["by_component"]["css"]["score"] > 0.0
    assert data["scores"]["by_component"]["js"]["score"] > 0.0
    assert data["scores"]["by_component"]["php"]["score"] == "SKIPPED"
    assert data["scores"]["by_component"]["sql"]["score"] == "SKIPPED"
    assert data["scores"]["overall"] >= 0.5


def test_summary_txt_created(tmp_path):
    data, workspace = run_pipeline_with_files(tmp_path, {"index.html": "<p>hi</p>"})

    summary_path = workspace / "summary.txt"
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "Overall" in content


def run_pipeline_with_profile(tmp_path: Path, files: dict[str, str], profile: str):
    submission = tmp_path / "submission"
    submission.mkdir()
    for relative, content in files.items():
        dest = submission / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    workspace = tmp_path / "workspace"
    pipeline = AssessmentPipeline()
    report_path = pipeline.run(submission, workspace, profile=profile)
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data


def test_profile_frontend_skips_backend_components(tmp_path):
    """Test that frontend profile marks php/sql as SKIPPED."""
    files = {
        "index.html": "<!doctype html><html><head><title>Test</title></head><body><form><input></form></body></html>",
        "style.css": "body { color: red; }",
        "app.js": "document.addEventListener('click', () => {});",
    }
    data = run_pipeline_with_profile(tmp_path, files, profile="frontend")

    # Frontend components should be scored
    assert isinstance(data["scores"]["by_component"]["html"]["score"], (int, float))
    assert isinstance(data["scores"]["by_component"]["css"]["score"], (int, float))
    assert isinstance(data["scores"]["by_component"]["js"]["score"], (int, float))

    # Backend components should be SKIPPED
    assert data["scores"]["by_component"]["php"]["score"] == "SKIPPED"
    assert data["scores"]["by_component"]["sql"]["score"] == "SKIPPED"

    assert data["scores"]["overall"] >= 0.5


def test_profile_fullstack_includes_all_components(tmp_path):
    """Test that fullstack profile includes all components."""
    files = {
        "index.html": "<!doctype html><html><head><title>Test</title></head><body><form><input></form></body></html>",
        "style.css": "body { color: red; }",
        "app.js": "document.addEventListener('click', () => {});",
        "process.php": "<?php echo $_GET['x']; ?>",
        "schema.sql": "CREATE TABLE users (id INT); INSERT INTO users VALUES (1); SELECT * FROM users;",
    }
    data = run_pipeline_with_profile(tmp_path, files, profile="fullstack")

    # All components should be scored (not SKIPPED)
    for component in ["html", "css", "js", "php", "sql"]:
        score = data["scores"]["by_component"][component]["score"]
        assert isinstance(score, (int, float)), f"{component} should be scored, got {score}"

    assert data["scores"]["overall"] >= 0.3  # with expanded rule set, simple test files score lower


def test_profile_skipped_components_not_in_denominator(tmp_path):
    """Test that SKIPPED components don't affect the overall score denominator."""
    # Minimal frontend submission
    files = {
        "index.html": "<div>hi</div>",
    }
    data = run_pipeline_with_profile(tmp_path, files, profile="frontend")

    # Only html, css, js are relevant (3 components)
    # php and sql should be SKIPPED
    assert data["scores"]["by_component"]["php"]["score"] == "SKIPPED"
    assert data["scores"]["by_component"]["sql"]["score"] == "SKIPPED"

    assert data["scores"]["overall"] <= 0.5  # minimal HTML only
