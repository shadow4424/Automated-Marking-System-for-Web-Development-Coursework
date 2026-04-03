from __future__ import annotations

from pathlib import Path


def test_api_assessor_name(tmp_path: Path) -> None:
    from ams.assessors.static.api_static import APIStaticAssessor
    assert APIStaticAssessor.name == "api_static"


def test_php_api_endpoint_detected(build_submission, run_pipeline) -> None:
    """PHP file with json_encode + Content-Type header → API.EVIDENCE with is_api_endpoint."""
    php = (
        "<?php\n"
        "header('Content-Type: application/json');\n"
        "echo json_encode(['status' => 'ok']);\n"
    )
    submission = build_submission({"api.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    findings = data.get("findings", [])
    evidence_findings = [f for f in findings if f["id"] == "API.EVIDENCE"]
    assert evidence_findings, "Expected API.EVIDENCE finding for PHP with json_encode + content-type header"
    php_ev = [f for f in evidence_findings if f["evidence"].get("file_type") == "php"]
    assert php_ev, "Expected at least one PHP API.EVIDENCE finding"
    assert php_ev[0]["evidence"]["is_api_endpoint"] is True


def test_js_fetch_detected(build_submission, run_pipeline) -> None:
    """JS file with fetch() call → API.EVIDENCE with has_api_patterns."""
    js = "fetch('/api/data').then(r => r.json()).then(console.log);\n"
    submission = build_submission({
        "index.html": "<html><body></body></html>",
        "app.js": js,
    })
    data = run_pipeline(submission, profile="api_backed_web")
    findings = data.get("findings", [])
    evidence_findings = [f for f in findings if f["id"] == "API.EVIDENCE"]
    js_ev = [f for f in evidence_findings if f["evidence"].get("file_type") == "js"]
    assert js_ev, "Expected at least one JS API.EVIDENCE finding for fetch() usage"
    assert js_ev[0]["evidence"]["has_api_patterns"] is True


def test_api_missing_files_when_required(build_submission, run_pipeline) -> None:
    """No PHP or JS files, profile requires API → API.MISSING_FILES."""
    submission = build_submission({"index.html": "<html><body></body></html>"})
    data = run_pipeline(submission, profile="api_backed_web")
    findings = data.get("findings", [])
    assert any(f["id"] == "API.MISSING_FILES" for f in findings), (
        "Expected API.MISSING_FILES when no PHP/JS files and profile requires API"
    )


def test_api_skipped_when_not_required(build_submission, run_pipeline) -> None:
    """No PHP or JS files, profile does not require API → API.SKIPPED."""
    submission = build_submission({"index.html": "<html><body></body></html>"})
    data = run_pipeline(submission, profile="frontend")
    findings = data.get("findings", [])
    assert any(f["id"] == "API.SKIPPED" for f in findings), (
        "Expected API.SKIPPED when no PHP/JS files and profile does not require API"
    )
    assert not any(f["id"] == "API.MISSING_FILES" for f in findings)


def test_php_with_method_routing_only(build_submission, run_pipeline) -> None:
    """PHP file with REQUEST_METHOD routing (no json_encode) → API.EVIDENCE via has_api_patterns."""
    php = (
        "<?php\n"
        "if ($_SERVER['REQUEST_METHOD'] === 'POST') {\n"
        "    $data = 'ok';\n"
        "}\n"
    )
    submission = build_submission({"router.php": php})
    data = run_pipeline(submission, profile="api_backed_web")
    findings = data.get("findings", [])
    evidence_findings = [f for f in findings if f["id"] == "API.EVIDENCE"]
    assert evidence_findings, "Expected API.EVIDENCE for PHP with REQUEST_METHOD routing"
    php_ev = [f for f in evidence_findings if f["evidence"].get("file_type") == "php"]
    assert php_ev[0]["evidence"]["has_api_patterns"] is True
    assert php_ev[0]["evidence"]["is_api_endpoint"] is False
