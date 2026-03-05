"""Tests for JS API static analysis (JS.API_EVIDENCE finding)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import pytest

from ams.assessors.static.js import JSStaticAssessor
from ams.core.models import Finding, SubmissionContext


def _run_js(content: str, tmp_path: Path) -> List[Finding]:
    """Helper: write a JS file, build context, and run the assessor."""
    submission = tmp_path / "submission"
    submission.mkdir()
    js_file = submission / "app.js"
    js_file.write_text(content, encoding="utf-8")
    context = SubmissionContext(
        submission_path=submission,
        workspace_path=tmp_path,
        discovered_files={"js": [js_file]},
        metadata={"profile": "frontend"},
    )
    return JSStaticAssessor().run(context)


class TestJSApiEvidence:
    """Tests for _analyse_api_usage in JSStaticAssessor."""

    def test_fetch_with_post_method(self, tmp_path: Path) -> None:
        code = """
        fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'Alice' })
        }).then(res => res.json()).catch(err => console.error(err));
        """
        findings = _run_js(code, tmp_path)
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert "POST" in ev["http_methods"]
        assert ev["json_stringify_count"] >= 1
        assert ev["content_type_json"] >= 1
        assert ev["response_json_count"] >= 1
        assert ev["catch_count"] >= 1

    def test_fetch_with_get_endpoint(self, tmp_path: Path) -> None:
        code = """
        fetch('/api/products')
            .then(response => response.json())
            .then(data => console.log(data));
        """
        findings = _run_js(code, tmp_path)
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert "/api/products" in ev["endpoints"]
        assert ev["then_count"] >= 2

    def test_no_api_usage(self, tmp_path: Path) -> None:
        code = """
        document.getElementById('btn').addEventListener('click', function() {
            console.log('clicked');
        });
        """
        findings = _run_js(code, tmp_path)
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 0

    def test_xhr_not_counted_as_api_evidence(self, tmp_path: Path) -> None:
        code = """
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '/data');
        xhr.send();
        """
        findings = _run_js(code, tmp_path)
        # XMLHttpRequest alone (without fetch patterns) should NOT produce API_EVIDENCE
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 0

    def test_multiple_methods_detected(self, tmp_path: Path) -> None:
        code = """
        fetch('/api/a', { method: 'GET' });
        fetch('/api/b', { method: 'DELETE' });
        fetch('/api/c', { method: 'PUT', body: JSON.stringify({}) });
        """
        findings = _run_js(code, tmp_path)
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 1
        ev = api_findings[0].evidence
        assert set(ev["http_methods"]) == {"GET", "DELETE", "PUT"}

    def test_response_ok_check_detected(self, tmp_path: Path) -> None:
        code = """
        fetch('/api/data').then(response => {
            if (!response.ok) throw new Error('fail');
            return response.json();
        });
        """
        findings = _run_js(code, tmp_path)
        api_findings = [f for f in findings if f.id == "JS.API_EVIDENCE"]
        assert len(api_findings) == 1
        assert api_findings[0].evidence["response_ok_count"] >= 1
