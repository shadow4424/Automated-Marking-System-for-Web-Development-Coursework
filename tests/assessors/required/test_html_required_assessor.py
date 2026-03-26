def test_html_required_missing_when_no_html(build_submission, run_pipeline) -> None:
    """HTML is required for frontend, so missing files should be MISSING_FILES, not SKIPPED."""
    submission = build_submission({"style.css": "body{}"})
    data = run_pipeline(submission, profile="frontend")

    findings = data["findings"]
    ids = [f["id"] for f in findings]
    # HTML is required for frontend, so should be MISSING_FILES
    assert "HTML.REQ.MISSING_FILES" in ids or "HTML.MISSING_FILES" in ids


def test_html_required_passes_when_all_present(build_submission, run_pipeline) -> None:
    html = "<!doctype html><html><body><form></form><input/><a href='#'>x</a></body></html>"
    submission = build_submission({"index.html": html})
    data = run_pipeline(submission, profile="frontend")

    req_findings = [f for f in data["findings"] if f["id"].startswith("HTML.REQ.")]
    passed = [f for f in req_findings if f["id"] == "HTML.REQ.PASS"]
    assert len(passed) >= 3
    rule_ids = {f["evidence"]["rule_id"] for f in passed}
    assert {"html.has_form", "html.has_input", "html.has_link"}.issubset(rule_ids)
    counts = {f["evidence"]["rule_id"]: f["evidence"]["count"] for f in passed}
    assert counts.get("html.has_form", 0) >= 1


def test_html_required_fail_when_missing_form(build_submission, run_pipeline) -> None:
    html = "<html><body><input/><a href='#'>x</a></body></html>"
    submission = build_submission({"index.html": html})
    data = run_pipeline(submission, profile="frontend")

    req_findings = [f for f in data["findings"] if f["id"] == "HTML.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "html.has_form" for f in req_findings)
    missing_form = next(f for f in req_findings if f["evidence"]["rule_id"] == "html.has_form")
    assert missing_form["evidence"]["count"] == 0
    assert missing_form["severity"] == "WARN"
