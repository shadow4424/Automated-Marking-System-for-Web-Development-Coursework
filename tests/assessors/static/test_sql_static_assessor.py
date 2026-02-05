from pathlib import Path


def test_sql_missing_results_in_skip_for_frontend(tmp_path: Path, run_pipeline) -> None:
    """SQL not required for frontend, so missing should be SKIPPED not FAIL."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile="frontend")
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.SKIPPED" and f["severity"] == "SKIPPED" for f in findings)
    assert not any(f["severity"] == "FAIL" for f in findings if f["id"].startswith("SQL"))


def test_sql_missing_results_in_fail_for_fullstack(tmp_path: Path, run_pipeline) -> None:
    """SQL required for fullstack, so missing should be FAIL."""
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.MISSING_FILES" and f["severity"] == "FAIL" for f in findings)


def test_sql_parses_valid_schema(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "schema.sql").write_text(
        "create table users(id int primary key);\ninsert into users values (1);\nselect * from users;",
        encoding="utf-8",
    )

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.STRUCTURE_OK" for f in findings)
    evidence = next(f["evidence"] for f in findings if f["id"] == "SQL.EVIDENCE")
    assert evidence["create_table"] == 1
    assert evidence["insert_into"] == 1
    assert evidence["select"] == 1


def test_sql_parse_fail(tmp_path: Path, run_pipeline) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "schema.sql").write_text("create table users(", encoding="utf-8")

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report.get("findings", [])
    assert any(f["id"] == "SQL.NO_SEMICOLONS" for f in findings)
