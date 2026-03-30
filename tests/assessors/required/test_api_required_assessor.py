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


def test_api_accepts_method_pass(build_submission, run_pipeline):
    """$_SERVER['REQUEST_METHOD'] → api.accepts_method passes."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "$method = $_SERVER['REQUEST_METHOD'];\n"
        "if ($method === 'GET') {\n"
        "    echo json_encode(['data' => 'ok']);\n"
        "}\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.accepts_method" in rule_ids


def test_api_accepts_method_fail(build_submission, run_pipeline):
    """No method check → api.accepts_method fails."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['result' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    fails = [f for f in data["findings"] if f["id"] == "API.REQ.FAIL"]
    assert any(f["evidence"]["rule_id"] == "api.accepts_method" for f in fails)


def test_api_valid_json_shape_pass(build_submission, run_pipeline):
    """Json_encode with array literal → api.valid_json_shape passes."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['status' => 'ok', 'data' => $result]);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.valid_json_shape" in rule_ids


def test_api_http_status_codes_pass(build_submission, run_pipeline):
    """Http_response_code() call → api.http_status_codes passes."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "http_response_code(200);\n"
        "echo json_encode(['result' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.http_status_codes" in rule_ids


def test_api_error_response_path_pass(build_submission, run_pipeline):
    """Json_encode + error key + conditional → api.error_response_path passes."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "if (!isset($_GET['id'])) {\n"
        "    http_response_code(400);\n"
        "    echo json_encode(['error' => 'Missing id parameter']);\n"
        "    exit;\n"
        "}\n"
        "echo json_encode(['data' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    rule_ids = {f["evidence"]["rule_id"] for f in passes}
    assert "api.error_response_path" in rule_ids


def test_api_error_response_path_absent_count_zero(build_submission, run_pipeline):
    """No error key → api.error_response_path has count=0 (optional rule — still passes)."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['result' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    # Rule is optional (min_count=0), so it always emits PASS; count should be 0
    passes = [f for f in data["findings"] if f["id"] == "API.REQ.PASS"]
    error_path_findings = [f for f in passes if f["evidence"]["rule_id"] == "api.error_response_path"]
    assert error_path_findings, "api.error_response_path should produce a PASS finding (optional)"
    assert error_path_findings[0]["evidence"]["count"] == 0
