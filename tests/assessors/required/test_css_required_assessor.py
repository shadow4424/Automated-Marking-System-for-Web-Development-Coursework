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


def test_css_parses_cleanly_pass(build_submission, run_pipeline):
    """Balanced braces → css.parses_cleanly passes."""
    css = "body { color: red; } h1 { font-size: 2em; }"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    passes = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "css.parses_cleanly" in rule_ids


def test_css_parses_cleanly_fail(build_submission, run_pipeline):
    """Severely unbalanced braces → css.parses_cleanly fails."""
    css = "body { color: red; h1 { font-size: 2em; "
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    fails = [f for f in data["findings"] if f["id"] == "CSS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "css.parses_cleanly" for f in fails)


def test_css_universal_reset_pass(build_submission, run_pipeline):
    """box-sizing reset strategy → css.has_universal_reset passes."""
    css = "* { box-sizing: border-box; } body { color: red; }"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend")
    passes = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "css.has_universal_reset" in rule_ids


def test_css_body_card_layout_pass(build_submission, run_pipeline):
    """4+ card-layout traits → css.body_card_layout passes in frontend_css_lab profile."""
    css = (
        "body { max-width: 800px; margin: 0 auto; padding: 20px; "
        "box-shadow: 0 2px 4px #000; border-radius: 8px; color: #333; }"
        "h1 { color: blue; } a:hover { color: red; }"
        "h2 { font-size: 1.5em; } "
    )
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend_css_lab")
    passes = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "css.body_card_layout" in rule_ids


def test_css_link_hover_style_pass(build_submission, run_pipeline):
    """a:hover rule → css.link_hover_style passes in frontend_css_lab profile."""
    css = (
        "body { color: #333; } a { text-decoration: none; } "
        "a:hover { text-decoration: underline; color: blue; }"
    )
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend_css_lab")
    passes = [f for f in data["findings"] if f["id"] == "CSS.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "css.link_hover_style" in rule_ids


def test_css_link_hover_style_fail(build_submission, run_pipeline):
    """No hover rule → css.link_hover_style fails in frontend_css_lab profile."""
    css = "body { color: #333; } a { text-decoration: none; }"
    submission = build_submission({"style.css": css})
    data = run_pipeline(submission, profile="frontend_css_lab")
    fails = [f for f in data["findings"] if f["id"] == "CSS.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "css.link_hover_style" for f in fails)
