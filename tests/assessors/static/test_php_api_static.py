"""Tests for PHP API static analysis (PHP.API_EVIDENCE finding)."""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from ams.assessors.static.php import PHPStaticAssessor
from ams.core.models import Finding, SubmissionContext


def _run_php(content: str, tmp_path: Path) -> List[Finding]:
    """Helper: write a PHP file, build context, and run the assessor."""
    submission = tmp_path / "submission"
    submission.mkdir()
    php_file = submission / "api.php"
    php_file.write_text(content, encoding="utf-8")
    context = SubmissionContext(
        submission_path=submission,
        workspace_path=tmp_path,
        discovered_files={"php": [php_file]},
        metadata={"profile": "fullstack"},
    )
    return PHPStaticAssessor().run(context)


class TestPHPApiEvidence:
    """Tests for _analyse_api_usage in PHPStaticAssessor."""

    def test_full_api_endpoint(self, tmp_path: Path) -> None:
        code = """<?php
        header('Content-Type: application/json');
        $method = $_SERVER['REQUEST_METHOD'];
        $input = json_decode(file_get_contents('php://input'), true);
        if ($method === 'GET') {
            echo json_encode(['status' => 'ok']);
        }
        ?>"""
        findings = _run_php(code, tmp_path)
        api_findings = [f for f in findings if f.id == "PHP.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert ev["json_content_type_header"] is True
        assert ev["json_encode_count"] >= 1
        assert ev["method_routing"] is True
        assert ev["php_input_read"] is True
        assert ev["json_decode_count"] >= 1
        assert ev["is_api_endpoint"] is True

    def test_json_encode_with_method_routing(self, tmp_path: Path) -> None:
        code = """<?php
        $method = $_SERVER['REQUEST_METHOD'];
        if ($method === 'POST') {
            echo json_encode(['created' => true]);
        }
        ?>"""
        findings = _run_php(code, tmp_path)
        api_findings = [f for f in findings if f.id == "PHP.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert ev["method_routing"] is True
        assert ev["json_encode_count"] >= 1

    def test_no_api_patterns(self, tmp_path: Path) -> None:
        code = """<?php
        echo '<h1>Hello World</h1>';
        $name = $_POST['name'];
        echo htmlspecialchars($name);
        ?>"""
        findings = _run_php(code, tmp_path)
        api_findings = [f for f in findings if f.id == "PHP.API_EVIDENCE"]
        assert len(api_findings) == 0

    def test_http_response_code_detected(self, tmp_path: Path) -> None:
        code = """<?php
        header('Content-Type: application/json');
        http_response_code(404);
        echo json_encode(['error' => 'Not found']);
        ?>"""
        findings = _run_php(code, tmp_path)
        api_findings = [f for f in findings if f.id == "PHP.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert ev["http_response_code"] is True
        assert ev["is_api_endpoint"] is True

    def test_json_decode_only(self, tmp_path: Path) -> None:
        code = """<?php
        $data = json_decode($raw);
        echo $data->name;
        ?>"""
        findings = _run_php(code, tmp_path)
        api_findings = [f for f in findings if f.id == "PHP.API_EVIDENCE"]
        assert len(api_findings) == 1
        assert api_findings[0].evidence["json_decode_count"] >= 1
