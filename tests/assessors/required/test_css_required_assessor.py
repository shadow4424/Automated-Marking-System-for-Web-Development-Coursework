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
    # All passing CSS rules should have a rule_id starting with "css."
    for finding in ids:
        evidence = finding["evidence"]
        assert evidence["rule_id"].startswith("css.")
    # At least one rule (css.has_rule_block or css.has_color) should count >= 1
    counted = [f for f in ids if f["evidence"].get("count", 0) >= 1]
    assert counted, "Expected at least one CSS.REQ.PASS finding with count >= 1"


def test_css_required_fail(build_submission, run_pipeline):
    css = "body color: red;"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "CSS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "css.has_rule_block" for f in fails)
