from pathlib import Path

def test_css_missing_results_in_fail_for_frontend(tmp_path: Path, run_pipeline) -> None:
    """CSS missing should be FAIL (required but absent) for frontend profile."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir)
    findings = report.get("findings", [])
    # CSS is required for frontend, so missing should be FAIL
    assert any(f["id"] == "CSS.MISSING_FILES" and f["severity"] == "FAIL" for f in findings)


def test_css_braces_balanced_for_simple_css(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "style.css").write_text("body { margin: 0; }", encoding="utf-8")

    report = run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(
        f["id"] == "CSS.BRACES_BALANCED" and f["evidence"].get("path").endswith("style.css")
        for f in findings
    )


def test_css_braces_unbalanced_warn(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "style.css").write_text("body { margin: 0;", encoding="utf-8")

    report = run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "CSS.BRACES_UNBALANCED" for f in findings)


def test_css_evidence_counts(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    css_content = "\n".join(
        [
            "@media (max-width: 600px) { body { } }",
            "@keyframes fade { from { } to { } }",
            "p { color: red !important; }",
        ]
    )
    (submission_dir / "style.css").write_text(css_content, encoding="utf-8")

    report = run_pipeline(submission_dir)
    findings = report.get("findings", [])
    evidence = next(
        f["evidence"]
        for f in findings
        if f["id"] == "CSS.EVIDENCE" and f["evidence"].get("path").endswith("style.css")
    )
    assert evidence["media_queries"] == 1
    assert evidence["keyframes"] == 1
    assert evidence["important"] == 1
    assert evidence["selectors_approx"] == css_content.count("{")
