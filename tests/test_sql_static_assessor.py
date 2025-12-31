import json
import tempfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path) -> dict:
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir))
        return json.loads(report_path.read_text(encoding="utf-8"))


def test_sql_missing_results_in_skipped_for_frontend(tmp_path: Path) -> None:
    """SQL missing should be SKIPPED (not required) for frontend profile."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    # SQL is not required for frontend, so missing should be SKIPPED
    assert any(f["id"] == "SQL.SKIPPED" and f["severity"] == "SKIPPED" for f in findings)


def test_sql_evidence_counts_and_structure_ok(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "schema.sql").write_text(
        "CREATE TABLE users(id INT);\nINSERT INTO users VALUES (1);\nSELECT * FROM users;\n",
        encoding="utf-8",
    )

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])

    evidence = next(f for f in findings if f["id"] == "SQL.EVIDENCE")
    assert evidence["evidence"]["create_table"] >= 1
    assert evidence["evidence"]["insert_into"] >= 1
    assert evidence["evidence"]["select"] >= 1
    assert any(f["id"] == "SQL.STRUCTURE_OK" for f in findings)


def test_sql_empty_warn(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "empty.sql").write_text("   \n\n\t", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.EMPTY" for f in findings)


def test_sql_no_semicolons_warn(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "q.sql").write_text("SELECT * FROM users", encoding="utf-8")

    report = _run_pipeline(submission_dir)
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.NO_SEMICOLONS" for f in findings)
