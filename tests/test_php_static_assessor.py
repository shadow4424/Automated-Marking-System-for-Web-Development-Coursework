import json
import tempfile
from pathlib import Path

from ams.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path) -> dict:
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir))
        return json.loads(report_path.read_text(encoding="utf-8"))


def test_php_missing_results_in_skipped(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.MISSING" and f["severity"] == "SKIPPED" for f in findings)


def test_php_tag_ok_and_evidence_counts(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php echo 'hi'; $_POST['x']; session_start(); ?>", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])

    assert any(
        f["id"] == "PHP.TAG_OK" and f["evidence"].get("path", "").endswith("index.php") for f in findings
    )

    evidence = next(
        f["evidence"]
        for f in findings
        if f["id"] == "PHP.EVIDENCE" and f["evidence"].get("path", "").endswith("index.php")
    )

    assert evidence.get("echo_usage", 0) >= 1
    assert evidence.get("request_usage", 0) >= 1
    assert evidence.get("session_usage", 0) >= 1


def test_php_tag_missing_warn(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("echo 'hi';", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.TAG_MISSING" for f in findings)


def test_php_syntax_suspect_for_unbalanced_tokens(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php if ($x { echo 'a'; ?>", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.SYNTAX_SUSPECT" for f in findings)
