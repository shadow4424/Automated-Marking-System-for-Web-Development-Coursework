import json
import tempfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path) -> dict:
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir))
        return json.loads(report_path.read_text(encoding="utf-8"))


def test_js_missing_results_in_fail_for_frontend(tmp_path: Path) -> None:
    """JS missing should be FAIL (required but absent) for frontend profile."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    # JS is required for frontend, so missing should be FAIL
    assert any(f["id"] == "JS.MISSING_FILES" and f["severity"] == "FAIL" for f in findings)


def test_js_syntax_ok_for_simple_js(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "app.js").write_text("function test(){ return 1; }", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(
        f["id"] == "JS.SYNTAX_OK" and f["evidence"].get("path").endswith("app.js")
        for f in findings
    )


def test_js_syntax_suspect_for_unbalanced_tokens(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "app.js").write_text("function x( {", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "JS.SYNTAX_SUSPECT" for f in findings)


def test_js_evidence_counts(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "app.js").write_text(
        "document.querySelector('#a').addEventListener('click', () => { fetch('/api'); });\n"
        "for (let i=0;i<1;i++){ }",
        encoding="utf-8",
    )

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    evidence = next(
        f["evidence"]
        for f in findings
        if f["id"] == "JS.EVIDENCE" and f["evidence"].get("path").endswith("app.js")
    )

    assert evidence["dom_calls"] >= 1
    assert evidence["query_calls"] >= 1
    assert evidence["event_listeners"] >= 1
    assert evidence["fetch_calls"] >= 1
    assert evidence.get("loops", 0) >= 1
    assert evidence.get("functions", 0) >= 1
