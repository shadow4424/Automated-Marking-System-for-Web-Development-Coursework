"""Tests for Phase 2: Cross-file consistency checks."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path, profile: str = "frontend") -> dict:
    """Run pipeline and return report as dict."""
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir), profile=profile)
        return json.loads(report_path.read_text(encoding="utf-8"))


class TestJSHTMLConsistency:
    """Test B1: HTML ↔ JS DOM selector consistency."""
    def test_js_references_missing_html_id(self, tmp_path: Path):
        """JS getElementById references non-existent HTML ID."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div id="existing">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.getElementById("missing-id");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_id_findings = [f for f in findings if f["id"] == "CONSISTENCY.JS_MISSING_HTML_ID"]
        assert len(missing_id_findings) > 0
        assert missing_id_findings[0]["severity"] == "WARN"
        assert missing_id_findings[0]["finding_category"] == "structure"
        assert missing_id_findings[0]["evidence"]["selector_value"] == "missing-id"

    def test_js_references_missing_html_class(self, tmp_path: Path):
        """JS querySelector references non-existent HTML class."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div class="existing">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.querySelector(".missing-class");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_class_findings = [f for f in findings if f["id"] == "CONSISTENCY.JS_MISSING_HTML_CLASS"]
        assert len(missing_class_findings) > 0
        assert missing_class_findings[0]["severity"] == "WARN"
        assert missing_class_findings[0]["evidence"]["selector_value"] == "missing-class"

    def test_js_queryselector_all_missing_id(self, tmp_path: Path):
        """JS querySelectorAll references non-existent HTML ID."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.querySelectorAll("#nonexistent");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_findings = [f for f in findings if f["id"] == "CONSISTENCY.JS_MISSING_HTML_ID"]
        assert len(missing_findings) > 0

    def test_js_matches_existing_html_elements(self, tmp_path: Path):
        """JS references existing HTML elements - no findings."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div id="myid" class="myclass">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.getElementById("myid"); document.querySelector(".myclass");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        consistency_findings = [f for f in findings if f["id"].startswith("CONSISTENCY.JS_MISSING")]
        assert len(consistency_findings) == 0


class TestCSSHTMLConsistency:
    """Test B2: HTML ↔ CSS selector consistency."""
    def test_css_references_missing_html_id(self, tmp_path: Path):
        """CSS references non-existent HTML ID."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div id="existing">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "style.css").write_text(
            '#missing-id { colour: red; }', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_id_findings = [f for f in findings if f["id"] == "CONSISTENCY.CSS_MISSING_HTML_ID"]
        assert len(missing_id_findings) > 0
        assert missing_id_findings[0]["severity"] == "WARN"
        assert missing_id_findings[0]["evidence"]["selector_value"] == "missing-id"

    def test_css_references_missing_html_class(self, tmp_path: Path):
        """CSS references non-existent HTML class."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div class="existing">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "style.css").write_text(
            '.missing-class { margin: 0; }', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_class_findings = [f for f in findings if f["id"] == "CONSISTENCY.CSS_MISSING_HTML_CLASS"]
        assert len(missing_class_findings) > 0
        assert missing_class_findings[0]["severity"] == "WARN"

    def test_css_matches_existing_html_elements(self, tmp_path: Path):
        """CSS references existing HTML elements - no findings."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div id="myid" class="myclass">Hello</div></body></html>', encoding="utf-8"
        )
        (submission_dir / "style.css").write_text(
            '#myid { colour: red; } .myclass { margin: 0; }', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        consistency_findings = [f for f in findings if f["id"].startswith("CONSISTENCY.CSS_MISSING")]
        assert len(consistency_findings) == 0


class TestPHPFormConsistency:
    """Test B3: Form field ↔ PHP variable consistency (fullstack only)."""
    def test_php_expects_missing_form_field(self, tmp_path: Path):
        """PHP accesses form field that doesn't exist in HTML."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><form><input name="username" /></form></body></html>', encoding="utf-8"
        )
        (submission_dir / "process.php").write_text(
            '<?php $email = $_POST["email"]; ?>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])

        missing_field_findings = [f for f in findings if f["id"] == "CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD"]
        assert len(missing_field_findings) > 0
        assert missing_field_findings[0]["severity"] == "WARN"
        assert missing_field_findings[0]["evidence"]["field_name"] == "email"

    def test_form_field_unused_in_php(self, tmp_path: Path):
        """HTML form field not accessed in PHP."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><form><input name="username" /><input name="unused" /></form></body></html>', encoding="utf-8"
        )
        (submission_dir / "process.php").write_text(
            '<?php $user = $_POST["username"]; ?>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])

        unused_field_findings = [f for f in findings if f["id"] == "CONSISTENCY.FORM_FIELD_UNUSED_IN_PHP"]
        assert len(unused_field_findings) > 0
        assert unused_field_findings[0]["severity"] == "INFO"
        assert unused_field_findings[0]["evidence"]["field_name"] == "unused"

    def test_php_form_consistency_skipped_for_frontend(self, tmp_path: Path):
        """PHP form consistency checks should not run for frontend profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><form><input name="username" /></form></body></html>', encoding="utf-8"
        )
        (submission_dir / "process.php").write_text(
            '<?php $email = $_POST["email"]; ?>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        # PHP form consistency checks should not run for frontend
        php_consistency_findings = [f for f in findings if f["id"].startswith("CONSISTENCY.PHP") or f["id"].startswith("CONSISTENCY.FORM_FIELD")]
        assert len(php_consistency_findings) == 0

    def test_php_matches_form_fields(self, tmp_path: Path):
        """PHP accesses form fields that exist in HTML - no missing field findings."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><form><input name="username" /><input name="email" /></form></body></html>', encoding="utf-8"
        )
        (submission_dir / "process.php").write_text(
            '<?php $user = $_POST["username"]; $email = $_GET["email"]; ?>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])

        missing_field_findings = [f for f in findings if f["id"] == "CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD"]
        assert len(missing_field_findings) == 0


class TestLinkTargets:
    """Test B4: Link/action target existence."""

    def test_missing_link_target(self, tmp_path: Path):
        """Link points to non-existent file."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><a href="missing.html">Link</a></body></html>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_link_findings = [f for f in findings if f["id"] == "CONSISTENCY.MISSING_LINK_TARGET"]
        assert len(missing_link_findings) > 0
        assert missing_link_findings[0]["severity"] == "WARN"
        assert missing_link_findings[0]["evidence"]["target"] == "missing.html"

    def test_missing_form_action_target(self, tmp_path: Path):
        """Form action points to non-existent file."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><form action="nonexistent.php"><input name="x" /></form></body></html>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_action_findings = [f for f in findings if f["id"] == "CONSISTENCY.MISSING_FORM_ACTION_TARGET"]
        assert len(missing_action_findings) > 0
        assert missing_action_findings[0]["severity"] == "WARN"

    def test_existing_link_target(self, tmp_path: Path):
        """Link points to existing file - no finding."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><a href="about.html">About</a></body></html>', encoding="utf-8"
        )
        (submission_dir / "about.html").write_text(
            '<html><body>About page</body></html>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_link_findings = [f for f in findings if f["id"] == "CONSISTENCY.MISSING_LINK_TARGET"]
        assert len(missing_link_findings) == 0

    def test_ignores_external_links(self, tmp_path: Path):
        """External links (http/https/mailto) should be ignored."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><a href="https://example.com">External</a><a href="mailto:test@example.com">Email</a></body></html>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_link_findings = [f for f in findings if f["id"] == "CONSISTENCY.MISSING_LINK_TARGET"]
        assert len(missing_link_findings) == 0

    def test_ignores_anchor_links(self, tmp_path: Path):
        """Anchor links (#) should be ignored."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><a href="#section1">Section</a></body></html>', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        missing_link_findings = [f for f in findings if f["id"] == "CONSISTENCY.MISSING_LINK_TARGET"]
        assert len(missing_link_findings) == 0


class TestConsistencyFindingsInReports:
    """Test that consistency findings appear in reports."""

    def test_consistency_findings_in_report_json(self, tmp_path: Path):
        """Consistency findings should appear in report.json."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body><div id="existing"></div></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.getElementById("missing");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        consistency_findings = [f for f in findings if f["id"].startswith("CONSISTENCY.")]
        assert len(consistency_findings) > 0

        # Check finding schema
        for finding in consistency_findings:
            assert "id" in finding
            assert "category" in finding
            assert "message" in finding
            assert "severity" in finding
            assert "evidence" in finding
            assert "source" in finding
            assert "finding_category" in finding
            assert finding["finding_category"] in ["structure", "evidence"]

    def test_consistency_findings_have_profile(self, tmp_path: Path):
        """Consistency findings should include profile information."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()

        (submission_dir / "index.html").write_text(
            '<html><body></body></html>', encoding="utf-8"
        )
        (submission_dir / "app.js").write_text(
            'document.getElementById("missing");', encoding="utf-8"
        )

        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])

        consistency_findings = [f for f in findings if f["id"].startswith("CONSISTENCY.")]
        for finding in consistency_findings:
            assert finding.get("profile") == "frontend"


