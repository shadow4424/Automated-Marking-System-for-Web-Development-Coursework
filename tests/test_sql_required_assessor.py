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


def test_sql_required_missing_when_no_sql(tmp_path: Path):
    """SQL is required for fullstack, so missing files should be MISSING_FILES, not SKIPPED."""
    data = _run_pipeline(tmp_path, {"index.html": "<html></html>"})
    ids = [f["id"] for f in data["findings"]]
    # SQL is required for fullstack, so should be MISSING_FILES
    assert "SQL.REQ.MISSING_FILES" in ids or "SQL.MISSING_FILES" in ids


def test_sql_required_pass(tmp_path: Path):
    sql = "CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t;"
    data = _run_pipeline(tmp_path, {"schema.sql": sql})
    passes = [f for f in data["findings"] if f["id"] == "SQL.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert {"sql.has_create_table", "sql.has_insert", "sql.has_select"}.issubset(rule_ids)


def test_sql_required_fail(tmp_path: Path):
    sql = "SELECT * FROM t"
    data = _run_pipeline(tmp_path, {"schema.sql": sql})
    fails = [f for f in data["findings"] if f["id"] == "SQL.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "sql.has_create_table" for f in fails)
    assert any(f["evidence"]["rule_id"] == "sql.has_insert" for f in fails)
