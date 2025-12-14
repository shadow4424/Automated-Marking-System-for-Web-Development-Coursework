import json
import tempfile
from pathlib import Path

from ams.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path) -> dict:
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir))
        return json.loads(report_path.read_text(encoding="utf-8"))


def test_html_missing_results_in_skipped(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "HTML.MISSING" and f["severity"] == "SKIPPED" for f in findings)


def test_html_parse_ok_for_simple_valid_html(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text(
        "<!doctype html><html><head></head><body>Hi</body></html>", encoding="utf-8"
    )

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(
        f["id"] == "HTML.PARSE_OK" and f["evidence"].get("path").endswith("index.html")
        for f in findings
    )


def test_html_parse_suspect_for_incomplete_html(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text("<div>hi</div>", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "HTML.PARSE_SUSPECT" for f in findings)


def test_html_element_evidence_counts(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text(
        """
        <!doctype html>
        <html>
        <head></head>
        <body>
          <form></form>
          <input />
          <input />
          <a href="#1">one</a>
          <a href="#2">two</a>
          <a href="#3">three</a>
        </body>
        </html>
        """,
        encoding="utf-8",
    )

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    evidence = next(
        f["evidence"]
        for f in findings
        if f["id"] == "HTML.ELEMENT_EVIDENCE" and f["evidence"].get("path").endswith("index.html")
    )
    assert evidence["forms"] == 1
    assert evidence["inputs"] == 2
    assert evidence["links"] == 3
