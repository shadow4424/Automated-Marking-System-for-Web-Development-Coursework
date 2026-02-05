def test_css_required_missing_when_no_css_for_frontend(build_submission, run_pipeline):
    """CSS required should be MISSING (required but absent) for frontend when no CSS files."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="frontend")
    ids = [f["id"] for f in data["findings"]]
    # CSS is required for frontend, so missing should be MISSING_FILES, not SKIPPED
    assert "CSS.REQ.MISSING_FILES" in ids or "CSS.MISSING_FILES" in ids


def test_css_required_pass(build_submission, run_pipeline):
    css = "body { color: red; }"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    ids = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    assert ids
    evidence = ids[0]["evidence"]
    assert evidence["count"] >= 1
    assert evidence["rule_id"] == "css.has_rule_block"


def test_css_required_fail(build_submission, run_pipeline):
    css = "body color: red;"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "CSS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "css.has_rule_block" for f in fails)
