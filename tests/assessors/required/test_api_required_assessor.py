from __future__ import annotations


def test_api_required_missing_when_no_php_or_js(build_submission, run_pipeline):
    """API is required for api_backed_web; no PHP/JS files → MISSING_FILES finding."""
    submission = build_submission({"index.html": "<html></html>"})
    data = run_pipeline(submission, profile="api_backed_web")
    ids = [f["id"] for f in data["findings"]]
    assert "API.REQ.MISSING_FILES" in ids or "API.MISSING_FILES" in ids


def test_api_required_rule_json_encode_passes(build_submission, run_pipeline):
    """PHP file with json_encode → api.json_encode rule passes → API.REQ.PASS."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['result' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.json_encode" in rule_ids, (
        f"Expected api.json_encode to pass; got rule_ids={rule_ids}"
    )


def test_api_required_rule_json_content_type_passes(build_submission, run_pipeline):
    """PHP file with application/json header → api.json_content_type rule passes."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['status' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.json_content_type" in rule_ids, (
        f"Expected api.json_content_type to pass; got rule_ids={rule_ids}"
    )


def test_api_required_rule_no_pass_without_json_encode(build_submission, run_pipeline):
    """PHP file without json_encode → api.json_encode rule does not pass."""
    php = "<?php $x = 1; ?>"
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.json_encode" not in rule_ids, (
        "api.json_encode should not pass when json_encode is absent"
    )
    # Either API.REQ.FAIL or API.REQ.MISSING_FILES is acceptable
    non_pass = [f for f in data["findings"] if f["id"] in ("API.REQ.FAIL", "API.REQ.MISSING_FILES")]
    assert any(f["evidence"].get("rule_id") == "api.json_encode" for f in non_pass) or non_pass, (
        "Expected a non-pass finding for api.json_encode"
    )
