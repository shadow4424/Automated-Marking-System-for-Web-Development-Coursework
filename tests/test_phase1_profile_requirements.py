"""Tests for Phase 1: Profile requirements and SKIPPED vs MISSING semantics."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ams.core.profiles import ProfileSpec, get_profile_spec
from ams.core.pipeline import AssessmentPipeline


def _run_pipeline(submission_dir: Path, profile: str = "frontend") -> dict:
    """Run pipeline and return report as dict."""
    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir), profile=profile)
        return json.loads(report_path.read_text(encoding="utf-8"))


class TestProfileSpec:
    """Test ProfileSpec requirements methods."""

    def test_frontend_profile_requirements(self):
        """Test frontend profile marks html/css/js as required."""
        spec = get_profile_spec("frontend")
        assert spec.is_component_required("html")
        assert spec.is_component_required("css")
        assert spec.is_component_required("js")
        assert not spec.is_component_required("php")
        assert not spec.is_component_required("sql")

    def test_fullstack_profile_requirements(self):
        """Test fullstack profile marks html/css/js/php/sql as required."""
        spec = get_profile_spec("fullstack")
        assert spec.is_component_required("html")
        assert spec.is_component_required("css")
        assert spec.is_component_required("js")
        assert spec.is_component_required("php")
        assert spec.is_component_required("sql")

    def test_has_required_rules(self):
        """Test has_required_rules method."""
        frontend = get_profile_spec("frontend")
        fullstack = get_profile_spec("fullstack")
        
        assert frontend.has_required_rules("html")
        assert frontend.has_required_rules("css")
        assert frontend.has_required_rules("js")
        assert not frontend.has_required_rules("php")
        assert not frontend.has_required_rules("sql")
        
        assert fullstack.has_required_rules("html")
        assert fullstack.has_required_rules("css")
        assert fullstack.has_required_rules("js")
        assert fullstack.has_required_rules("php")
        assert fullstack.has_required_rules("sql")

    def test_get_required_file_extensions(self):
        """Test get_required_file_extensions method."""
        spec = get_profile_spec("frontend")
        extensions = spec.get_required_file_extensions()
        assert ".html" in extensions


class TestSkippedVsMissing:
    """Test SKIPPED vs MISSING semantics."""

    def test_frontend_php_skipped(self, tmp_path: Path):
        """PHP should be SKIPPED (not required) for frontend profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        # No PHP files
        
        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])
        
        php_skipped = [f for f in findings if f["id"] == "PHP.SKIPPED"]
        assert len(php_skipped) > 0
        assert php_skipped[0]["severity"] == "SKIPPED"
        assert php_skipped[0]["evidence"].get("required") is False

    def test_fullstack_php_missing(self, tmp_path: Path):
        """PHP should be MISSING (required but absent) for fullstack profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        # No PHP files, but PHP is required for fullstack
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])
        
        php_missing = [f for f in findings if f["id"] == "PHP.MISSING_FILES"]
        assert len(php_missing) > 0
        assert php_missing[0]["severity"] == "FAIL"
        assert php_missing[0]["evidence"].get("required") is True

    def test_frontend_sql_skipped(self, tmp_path: Path):
        """SQL should be SKIPPED (not required) for frontend profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])
        
        sql_skipped = [f for f in findings if f["id"] == "SQL.SKIPPED"]
        assert len(sql_skipped) > 0
        assert sql_skipped[0]["severity"] == "SKIPPED"

    def test_fullstack_sql_missing(self, tmp_path: Path):
        """SQL should be MISSING (required but absent) for fullstack profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])
        
        sql_missing = [f for f in findings if f["id"] == "SQL.MISSING_FILES"]
        assert len(sql_missing) > 0
        assert sql_missing[0]["severity"] == "FAIL"

    def test_frontend_html_missing(self, tmp_path: Path):
        """HTML should be MISSING (required but absent) for frontend profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        # No HTML files, but HTML is required for frontend
        
        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])
        
        html_missing = [f for f in findings if f["id"] == "HTML.MISSING_FILES"]
        assert len(html_missing) > 0
        assert html_missing[0]["severity"] == "FAIL"
        assert html_missing[0]["evidence"].get("required") is True

    def test_fullstack_html_missing(self, tmp_path: Path):
        """HTML should be MISSING (required but absent) for fullstack profile."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])
        
        html_missing = [f for f in findings if f["id"] == "HTML.MISSING_FILES"]
        assert len(html_missing) > 0
        assert html_missing[0]["severity"] == "FAIL"


class TestRequiredRulesAssessors:
    """Test required rules assessors distinguish SKIPPED vs MISSING."""

    def test_frontend_php_required_skipped(self, tmp_path: Path):
        """PHP required rules should be SKIPPED for frontend."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="frontend")
        findings = report.get("findings", [])
        
        php_req_skipped = [f for f in findings if f["id"] == "PHP.REQ.SKIPPED"]
        assert len(php_req_skipped) > 0
        assert php_req_skipped[0]["severity"] == "SKIPPED"

    def test_fullstack_php_required_missing(self, tmp_path: Path):
        """PHP required rules should be MISSING for fullstack when files absent."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])
        
        php_req_missing = [f for f in findings if f["id"] == "PHP.REQ.MISSING_FILES"]
        assert len(php_req_missing) > 0
        assert php_req_missing[0]["severity"] == "FAIL"
        assert php_req_missing[0]["evidence"].get("required") is True


class TestScoringWithSkippedVsMissing:
    """Test scoring handles SKIPPED vs MISSING correctly."""

    def test_frontend_skipped_components_not_counted(self, tmp_path: Path):
        """SKIPPED components should not affect overall score for frontend."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        # Only HTML present, PHP/SQL should be SKIPPED
        
        (submission_dir / "index.html").write_text(
            "<!doctype html><html><head></head><body>Hi</body></html>", encoding="utf-8"
        )
        
        report = _run_pipeline(submission_dir, profile="frontend")
        scores = report.get("scores", {})
        by_component = scores.get("by_component", {})
        
        # PHP and SQL should be SKIPPED (string, not numeric)
        assert by_component.get("php", {}).get("score") == "SKIPPED"
        assert by_component.get("sql", {}).get("score") == "SKIPPED"
        
        # Overall score should only consider html/css/js
        overall = scores.get("overall")
        assert isinstance(overall, (int, float))

    def test_fullstack_missing_components_affect_score(self, tmp_path: Path):
        """MISSING components should result in 0.0 score for fullstack."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        # Only HTML present, PHP/SQL should be MISSING (required but absent)
        
        (submission_dir / "index.html").write_text(
            "<!doctype html><html><head></head><body>Hi</body></html>", encoding="utf-8"
        )
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        scores = report.get("scores", {})
        by_component = scores.get("by_component", {})
        
        # PHP and SQL should be 0.0 (MISSING)
        assert by_component.get("php", {}).get("score") == 0.0
        assert by_component.get("sql", {}).get("score") == 0.0
        
        # Overall score should be lower due to missing components
        overall = scores.get("overall")
        assert isinstance(overall, (int, float))
        assert overall < 1.0  # Should be less than 1.0 due to missing PHP/SQL

    def test_score_evidence_reflects_skipped_vs_missing(self, tmp_path: Path):
        """Score evidence should clearly distinguish SKIPPED vs MISSING."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="frontend")
        score_evidence = report.get("score_evidence", {})
        components = score_evidence.get("components", {})
        
        # PHP should be marked as not required
        php_info = components.get("php", {})
        assert php_info.get("required") is False
        
        # HTML should be marked as required
        html_info = components.get("html", {})
        assert html_info.get("required") is True


class TestReportJsonClarity:
    """Test that report.json clearly distinguishes SKIPPED vs MISSING."""

    def test_findings_include_profile_info(self, tmp_path: Path):
        """Findings should include profile and required status."""
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        
        report = _run_pipeline(submission_dir, profile="fullstack")
        findings = report.get("findings", [])
        
        # Check that MISSING findings from static/required assessors include profile info
        missing_findings = [f for f in findings if "MISSING_FILES" in f["id"] and not "BEHAVIORAL" in f["id"]]
        for finding in missing_findings:
            assert "profile" in finding.get("evidence", {}), f"Finding {finding['id']} missing profile info"
            assert finding.get("evidence", {}).get("required") is True, f"Finding {finding['id']} should be marked as required"
        
        # Check that SKIPPED findings from static/required assessors include profile info
        skipped_findings = [f for f in findings if "SKIPPED" in f["id"] and not "BEHAVIORAL" in f["id"]]
        for finding in skipped_findings:
            assert "profile" in finding.get("evidence", {}), f"Finding {finding['id']} missing profile info"
            assert finding.get("evidence", {}).get("required") is False, f"Finding {finding['id']} should be marked as not required"

