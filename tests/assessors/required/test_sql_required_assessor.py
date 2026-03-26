from __future__ import annotations


def test_sql_required_missing_when_no_sql(build_submission, run_pipeline):
    """SQL is required for fullstack, so missing files should be MISSING_FILES, not SKIPPED."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="fullstack")
    ids = [f["id"] for f in data["findings"]]
    assert "SQL.REQ.MISSING_FILES" in ids or "SQL.MISSING_FILES" in ids


def test_sql_required_pass(build_submission, run_pipeline):
    sql = "CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t;"
    submission = build_submission({"schema.sql": sql})
    data = run_pipeline(submission, profile="fullstack")
    passes = [f for f in data["findings"] if f["id"] == "SQL.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert {"sql.has_create_table", "sql.has_insert", "sql.has_select"}.issubset(rule_ids)


def test_sql_required_fail(build_submission, run_pipeline):
    sql = "SELECT * FROM t"
    submission = build_submission({"schema.sql": sql})
    data = run_pipeline(submission, profile="fullstack")
    fails = [f for f in data["findings"] if f["id"] == "SQL.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "sql.has_create_table" for f in fails)
    assert any(f["evidence"]["rule_id"] == "sql.has_insert" for f in fails)


def test_sql_parses_cleanly_pass(build_submission, run_pipeline):
    """Balanced parens and proper semicolons → sql.parses_cleanly passes."""
    sql = "CREATE TABLE users (id INT, name VARCHAR(50)); INSERT INTO users VALUES (1, 'Alice'); SELECT * FROM users;"
    submission = build_submission({"schema.sql": sql})
    data = run_pipeline(submission, profile="fullstack")
    passes = [f for f in data["findings"] if f["id"] == "SQL.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "sql.parses_cleanly" in rule_ids


def test_sql_parses_cleanly_fail(build_submission, run_pipeline):
    """No semicolons → sql.parses_cleanly fails."""
    sql = "CREATE TABLE t id INT name VARCHAR(50)"
    submission = build_submission({"schema.sql": sql})
    data = run_pipeline(submission, profile="fullstack")
    fails = [f for f in data["findings"] if f["id"] == "SQL.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "sql.parses_cleanly" for f in fails)
