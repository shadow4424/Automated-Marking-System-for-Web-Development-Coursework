from __future__ import annotations


def test_php_required_missing_when_no_php(build_submission, run_pipeline):
    """PHP is required for fullstack, so missing files should be MISSING_FILES, not SKIPPED."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="fullstack")
    ids = [f["id"] for f in data["findings"]]
    assert "PHP.REQ.MISSING_FILES" in ids or "PHP.MISSING_FILES" in ids


def test_php_required_pass(build_submission, run_pipeline):
    php = "<?php echo 'hi'; $_POST['x']; ?>"
    submission = build_submission({"index.php": php})
    data = run_pipeline(submission, profile="fullstack")
    passes = [f for f in data["findings"] if f["id"] == "PHP.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert {"php.has_open_tag", "php.uses_request", "php.outputs"}.issubset(rule_ids)


def test_php_required_fail(build_submission, run_pipeline):
    php = "<?php $x = 1; ?>"
    submission = build_submission({"index.php": php})
    data = run_pipeline(submission, profile="fullstack")
    fails = [f for f in data["findings"] if f["id"] == "PHP.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "php.uses_request" for f in fails)
    assert any(f["evidence"]["rule_id"] == "php.outputs" for f in fails)
