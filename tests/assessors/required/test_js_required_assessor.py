def test_js_required_missing_when_no_js(build_submission, run_pipeline):
    """JS is required for frontend, so missing files should be MISSING_FILES, not SKIPPED."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="frontend")
    ids = [f["id"] for f in data["findings"]]
    # JS is required for frontend, so should be MISSING_FILES
    assert "JS.REQ.MISSING_FILES" in ids or "JS.MISSING_FILES" in ids


def test_js_required_pass(build_submission, run_pipeline):
    js = "document.body.addEventListener('click', ()=>{});"
    submission = build_submission({"app.js": js})
    data = run_pipeline(submission, profile="frontend")
    passes = [f for f in data["findings"] if f["id"] == "JS.REQ.PASS"]
    assert passes
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "js.has_event_listener" in rule_ids


def test_js_required_fail(build_submission, run_pipeline):
    js = "console.log('x');"
    submission = build_submission({"app.js": js})
    data = run_pipeline(submission, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "JS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "js.has_event_listener" for f in fails)
