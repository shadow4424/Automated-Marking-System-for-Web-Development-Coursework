from __future__ import annotations

import json
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(tmp_path: Path, files: dict[str, str], profile: str = "fullstack"):
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


def test_php_required_missing_when_no_php(tmp_path: Path):
    """PHP is required for fullstack, so missing files should be MISSING_FILES, not SKIPPED."""
    data = _run_pipeline(tmp_path, {"index.html": "<html></html>"})
    ids = [f["id"] for f in data["findings"]]
    # PHP is required for fullstack, so should be MISSING_FILES
    assert "PHP.REQ.MISSING_FILES" in ids or "PHP.MISSING_FILES" in ids


def test_php_required_pass(tmp_path: Path):
    php = "<?php echo 'hi'; $_POST['x']; ?>"
    data = _run_pipeline(tmp_path, {"index.php": php})
    passes = [f for f in data["findings"] if f["id"] == "PHP.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert {"php.has_open_tag", "php.uses_request", "php.outputs"}.issubset(rule_ids)


def test_php_required_fail(tmp_path: Path):
    php = "<?php $x = 1; ?>"
    data = _run_pipeline(tmp_path, {"index.php": php})
    fails = [f for f in data["findings"] if f["id"] == "PHP.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "php.uses_request" for f in fails)
    assert any(f["evidence"]["rule_id"] == "php.outputs" for f in fails)
