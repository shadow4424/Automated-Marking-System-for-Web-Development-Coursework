from pathlib import Path


def test_php_missing_results_in_skip_for_frontend(tmp_path: Path, run_pipeline) -> None:
    """PHP not required for frontend, so missing should be SKIPPED not FAIL."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile="frontend")
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.SKIPPED" and f["severity"] == "SKIPPED" for f in findings)
    assert not any(f["severity"] == "FAIL" for f in findings if f["id"].startswith("PHP"))


def test_php_missing_results_in_fail_for_fullstack(tmp_path: Path, run_pipeline) -> None:
    """PHP required for fullstack, so missing should be FAIL."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.MISSING_FILES" and f["severity"] == "FAIL" for f in findings)


def test_php_file_ok(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php echo 'hi';", encoding="utf-8")

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.TAG_OK" for f in findings)
    assert any(f["id"] == "PHP.EVIDENCE" for f in findings)


def test_php_unclosed_tag(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php echo 'hi'", encoding="utf-8")

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "PHP.SYNTAX_SUSPECT" for f in findings)
