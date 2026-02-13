"""Tests for the check aggregation layer (ams.core.aggregation).

Validates that:
- Raw findings are correctly classified as rubric checks vs diagnostics.
- Multiple findings from the same rule collapse into one check.
- Consistency findings aggregate by type (not by occurrence).
- Diagnostic / browser / deterministic-engine events are excluded from checks.
- Check stats accurately reflect distinct logical checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ams.core.aggregation import (
    CheckResult,
    aggregate_findings_to_checks,
    compute_check_stats,
    get_check_key,
    is_diagnostic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(
    fid: str,
    category: str = "html",
    severity: str = "INFO",
    source: str = "html_required",
    evidence: dict | None = None,
    required: bool | None = None,
) -> dict:
    return {
        "id": fid,
        "category": category,
        "message": f"Message for {fid}",
        "severity": severity,
        "source": source,
        "evidence": evidence or {},
        "finding_category": "OTHER",
        "profile": "fullstack",
        "required": required,
    }


# ---------------------------------------------------------------------------
# is_diagnostic
# ---------------------------------------------------------------------------

class TestIsDiagnostic:
    def test_browser_events(self):
        assert is_diagnostic(_finding("BROWSER.PAGE_LOAD_PASS", source="browser_automation"))
        assert is_diagnostic(_finding("BROWSER.PHP_BACKEND_LIMITATION", source="browser_automation"))
        assert is_diagnostic(_finding("BROWSER.CONSOLE_CLEAN", source="browser_automation"))

    def test_behaviour_events(self):
        assert is_diagnostic(_finding("BEHAVIOUR.PHP_SMOKE_PASS", source="deterministic_test_engine"))
        assert is_diagnostic(_finding("BEHAVIOUR.SQL_EXEC_FAIL", source="deterministic_test_engine"))

    def test_visual_enrichment(self):
        assert is_diagnostic(_finding("VISUAL.CSS.REQ.FAIL", source="VisionAnalyst"))

    def test_evidence_collectors(self):
        assert is_diagnostic(_finding("HTML.ELEMENT_EVIDENCE", source="html_static"))
        assert is_diagnostic(_finding("CSS.EVIDENCE", source="css_static"))
        assert is_diagnostic(_finding("JS.EVIDENCE", source="js_static"))
        assert is_diagnostic(_finding("SQL.EVIDENCE", source="sql_static"))

    def test_static_structural(self):
        assert is_diagnostic(_finding("HTML.PARSE_OK", source="html_static"))
        assert is_diagnostic(_finding("CSS.BRACES_BALANCED", source="css_static"))
        assert is_diagnostic(_finding("JS.SYNTAX_OK", source="js_static"))
        assert is_diagnostic(_finding("SQL.STRUCTURE_OK", source="sql_static"))

    def test_quality_security(self):
        assert is_diagnostic(_finding("JS.QUALITY.POOR_NAMING", source="js_static"))
        assert is_diagnostic(_finding("SQL.SECURITY.MISSING_LIMIT", source="sql_static"))

    def test_rubric_checks_not_diagnostic(self):
        assert not is_diagnostic(_finding("HTML.REQ.PASS", source="html_required"))
        assert not is_diagnostic(_finding("SQL.REQ.FAIL", source="sql_required"))
        assert not is_diagnostic(_finding("CSS.REQ.PASS", source="css_required"))
        assert not is_diagnostic(_finding("CONSISTENCY.JS_MISSING_HTML_ID", source="consistency"))
        assert not is_diagnostic(_finding("HTML.BEHAVIORAL.PAGE_LOADS", source="html_behavioral"))
        assert not is_diagnostic(_finding("HTML.MISSING_FILES", source="html_static"))


# ---------------------------------------------------------------------------
# get_check_key
# ---------------------------------------------------------------------------

class TestGetCheckKey:
    def test_required_rule_uses_evidence_rule_id(self):
        f = _finding("SQL.REQ.PASS", evidence={"rule_id": "sql.has_insert"})
        assert get_check_key(f) == "sql.has_insert"

    def test_consistency_uses_finding_id(self):
        f = _finding(
            "CONSISTENCY.CSS_MISSING_HTML_ID",
            evidence={"selector_value": "#foo"},
        )
        assert get_check_key(f) == "CONSISTENCY.CSS_MISSING_HTML_ID"

    def test_behavioral_uses_finding_id(self):
        f = _finding("HTML.BEHAVIORAL.PAGE_LOADS")
        assert get_check_key(f) == "HTML.BEHAVIORAL.PAGE_LOADS"

    def test_missing_files_uses_finding_id(self):
        f = _finding("HTML.MISSING_FILES")
        assert get_check_key(f) == "HTML.MISSING_FILES"


# ---------------------------------------------------------------------------
# aggregate_findings_to_checks
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_sql_required_rules_each_unique(self):
        """Each SQL required rule should map to a unique check."""
        sql_rule_ids = [
            "sql.has_create_table", "sql.has_primary_key", "sql.has_foreign_key",
            "sql.has_constraints", "sql.has_data_types",
            "sql.has_insert", "sql.has_select", "sql.has_update", "sql.has_delete",
            "sql.has_where", "sql.has_join", "sql.has_aggregate",
        ]
        findings = [
            _finding(
                "SQL.REQ.PASS",
                category="sql",
                source="sql_required",
                evidence={"rule_id": rid, "weight": 0.08},
            )
            for rid in sql_rule_ids
        ]
        checks, diags = aggregate_findings_to_checks(findings)
        assert len(checks) == 12
        assert len(diags) == 0
        check_ids = {c.check_id for c in checks}
        assert check_ids == set(sql_rule_ids)

    def test_consistency_collapses_occurrences(self):
        """Multiple CSS missing ID findings should aggregate into one check."""
        findings = [
            _finding(
                "CONSISTENCY.CSS_MISSING_HTML_ID",
                category="consistency",
                source="consistency",
                evidence={"selector_value": f"#id{i}"},
            )
            for i in range(5)
        ]
        checks, diags = aggregate_findings_to_checks(findings)
        assert len(checks) == 1
        assert checks[0].check_id == "CONSISTENCY.CSS_MISSING_HTML_ID"
        assert checks[0].occurrences == 5

    def test_diagnostics_excluded(self):
        """Diagnostics should not appear in checks."""
        findings = [
            _finding("HTML.REQ.PASS", source="html_required", evidence={"rule_id": "html.has_doctype"}),
            _finding("BROWSER.PAGE_LOAD_PASS", source="browser_automation"),
            _finding("BEHAVIOUR.PHP_SMOKE_PASS", source="deterministic_test_engine"),
            _finding("HTML.PARSE_OK", source="html_static"),
            _finding("HTML.ELEMENT_EVIDENCE", source="html_static"),
        ]
        checks, diags = aggregate_findings_to_checks(findings)
        assert len(checks) == 1
        assert checks[0].check_id == "html.has_doctype"
        assert len(diags) == 4

    def test_status_merging_fail_wins(self):
        """FAIL should win when merging with PASS for the same check."""
        findings = [
            _finding("CONSISTENCY.JS_MISSING_HTML_ID", severity="WARN", source="consistency"),
            _finding("CONSISTENCY.JS_MISSING_HTML_ID", severity="FAIL", source="consistency"),
            _finding("CONSISTENCY.JS_MISSING_HTML_ID", severity="INFO", source="consistency"),
        ]
        checks, _ = aggregate_findings_to_checks(findings)
        assert len(checks) == 1
        assert checks[0].status == "FAIL"

    def test_full_report_fixture(self):
        """Simulate a realistic fullstack report and verify check count < finding count."""
        findings = []
        # 16 HTML required rules
        for i in range(16):
            findings.append(_finding("HTML.REQ.PASS", source="html_required", evidence={"rule_id": f"html.rule_{i}", "weight": 0.05}))
        # 13 CSS required rules
        for i in range(13):
            findings.append(_finding("CSS.REQ.PASS", source="css_required", evidence={"rule_id": f"css.rule_{i}", "weight": 0.07}))
        # 12 JS required rules
        for i in range(12):
            findings.append(_finding("JS.REQ.PASS", source="js_required", evidence={"rule_id": f"js.rule_{i}", "weight": 0.08}))
        # 12 PHP required rules
        for i in range(12):
            findings.append(_finding("PHP.REQ.PASS", source="php_required", evidence={"rule_id": f"php.rule_{i}", "weight": 0.08}))
        # 12 SQL required rules
        for i in range(12):
            findings.append(_finding("SQL.REQ.PASS", source="sql_required", evidence={"rule_id": f"sql.rule_{i}", "weight": 0.08}))
        # 2 behavioral rules
        findings.append(_finding("HTML.BEHAVIORAL.PAGE_LOADS", source="html_behavioral"))
        findings.append(_finding("HTML.BEHAVIORAL.FORM_EXISTS", source="html_behavioral"))
        # 3 consistency (one type, 3 occurrences)
        for i in range(3):
            findings.append(_finding("CONSISTENCY.CSS_MISSING_HTML_ID", source="consistency", severity="WARN"))
        # Diagnostics (not checks)
        findings.append(_finding("HTML.PARSE_OK", source="html_static"))
        findings.append(_finding("HTML.ELEMENT_EVIDENCE", source="html_static"))
        findings.append(_finding("CSS.EVIDENCE", source="css_static"))
        findings.append(_finding("JS.SYNTAX_OK", source="js_static"))
        findings.append(_finding("BROWSER.PAGE_LOAD_PASS", source="browser_automation"))
        findings.append(_finding("BROWSER.CONSOLE_CLEAN", source="browser_automation"))
        findings.append(_finding("BEHAVIOUR.PHP_SMOKE_PASS", source="deterministic_test_engine"))
        findings.append(_finding("BEHAVIOUR.SQL_EXEC_PASS", source="deterministic_test_engine"))
        findings.append(_finding("JS.QUALITY.POOR_NAMING", source="js_static", severity="WARN"))

        checks, diags = aggregate_findings_to_checks(findings)
        total_findings = len(findings)
        total_checks = len(checks)

        assert total_findings >= 70  # Many findings
        assert total_checks < total_findings  # Checks < findings
        # Expected: 16+13+12+12+12 + 2 behavioral + 1 consistency = 68 checks
        assert total_checks == 68
        assert len(diags) == 9

    def test_check_stats(self):
        """compute_check_stats returns correct counts."""
        checks = [
            CheckResult(check_id="a", component="html", status="PASS"),
            CheckResult(check_id="b", component="html", status="PASS"),
            CheckResult(check_id="c", component="css", status="FAIL"),
            CheckResult(check_id="d", component="js", status="WARN"),
            CheckResult(check_id="e", component="sql", status="SKIPPED"),
        ]
        stats = compute_check_stats(checks)
        assert stats == {"total": 5, "passed": 2, "failed": 1, "warnings": 1, "skipped": 1}

    def test_real_report_aggregation(self, tmp_path):
        """Integration: run pipeline and verify checks < findings in output."""
        import json
        import tempfile
        from ams.core.pipeline import AssessmentPipeline

        # Build a minimal frontend submission
        sub = tmp_path / "submission"
        sub.mkdir()
        (sub / "index.html").write_text(
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Test</title></head><body><h1>Hello</h1>"
            "<form><input type='text'><label>Name</label></form>"
            "<a href='#'>Link</a></body></html>",
            encoding="utf-8",
        )
        (sub / "style.css").write_text(
            "body { margin: 0; } h1 { color: blue; } .container { display: flex; }",
            encoding="utf-8",
        )
        (sub / "app.js").write_text(
            "document.addEventListener('DOMContentLoaded', function() {"
            "  const el = document.querySelector('h1');"
            "  el.textContent = 'Updated';"
            "});",
            encoding="utf-8",
        )

        pipeline = AssessmentPipeline()
        with tempfile.TemporaryDirectory(prefix="ams-agg-test-") as ws:
            report_path = pipeline.run(sub, Path(ws), profile="frontend")
            report = json.loads(report_path.read_text(encoding="utf-8"))

        # Report should now have checks, check_stats, diagnostics
        assert "checks" in report
        assert "check_stats" in report
        assert "diagnostics" in report

        findings_count = len(report["findings"])
        checks_count = report["check_stats"]["total"]
        diag_count = len(report["diagnostics"])

        # Checks should be less than or equal to findings
        assert checks_count <= findings_count
        # There should be some diagnostics
        assert diag_count > 0
        # Sum of checks + diagnostics = total findings
        assert checks_count + diag_count <= findings_count  # checks from rubric + diags from non-rubric
        # Stats should add up
        stats = report["check_stats"]
        assert stats["passed"] + stats["failed"] + stats["warnings"] + stats["skipped"] == stats["total"]
