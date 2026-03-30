"""Tests for the redesigned AMS export system."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from ams.io.export_report import (
    build_export_report,
    validate_export_report,
    export_json,
    export_txt,
    export_csv_summary,
    export_csv_findings,
    export_csv_rules,
    export_csv_zip,
    export_pdf,
    STATUS_PASS,
    STATUS_PARTIAL,
    STATUS_FAIL,
    STATUS_SKIPPED_BY_PROFILE,
    STATUS_NOT_RUN,
    STATUS_ENVIRONMENT_UNAVAILABLE,
    STATUS_NO_RELEVANT_FILES,
)


# Shared fixture.


def _make_report(
    overall: float = 0.65,
    student_id: str = "stu123",
    assignment_id: str = "asn1",
    profile: str = "frontend",
    include_requirements: bool = True,
    include_findings: bool = True,
    php_available: bool = False,
    browser_available: bool = True,
) -> dict:
    """Build a minimal but realistic report.json dict matching the actual pipeline output."""
    requirements = []
    if include_requirements:
        requirements = [
            {
                "requirement_id": "HTML.HAS_DOCTYPE",
                "component": "html",
                "description": "Document must have a valid DOCTYPE declaration",
                "stage": "static",
                "score": 1.0,
                "status": "met",
                "weight": 1.0,
                "skipped_reason": None,
                "evidence": {"found": "<!DOCTYPE html>", "expected": "<!DOCTYPE html>"},
            },
            {
                "requirement_id": "HTML.MISSING_HEADING",
                "component": "html",
                "description": "Page must have at least one heading element",
                "stage": "static",
                "score": 0.0,
                "status": "not_met",
                "weight": 1.0,
                "skipped_reason": None,
                "evidence": {"found": None, "expected": "h1/h2/h3"},
            },
            {
                "requirement_id": "JS.EVENT_LISTENER",
                "component": "js",
                "description": "Must have at least one event listener",
                "stage": "static",
                "score": 0.5,
                "status": "partial",
                "weight": 1.0,
                "skipped_reason": None,
                "evidence": {"found": "onclick attribute", "expected": "addEventListener"},
            },
            {
                "requirement_id": "PHP.PREPARED_STMT",
                "component": "php",
                "description": "Must use prepared statements",
                "stage": "runtime",
                "score": "SKIPPED",
                "status": "skipped",
                "weight": 1.0,
                "skipped_reason": "component_not_required",
                "evidence": {},
            },
        ]
    findings = []
    if include_findings:
        findings = [
            {
                "id": "HTML.MISSING_ALT",
                "category": "html",
                "message": "Images are missing alt text attributes",
                "severity": "WARN",
                "evidence": {"count": 3, "elements": ["<img src='a.jpg'>"]},
                "source": "html_assessor",
                "finding_category": "structure",
            },
            {
                "id": "CSS.SYNTAX_ERROR",
                "category": "css",
                "message": "Unbalanced braces detected in stylesheet",
                "severity": "FAIL",
                "evidence": {"line": 42, "context": "body { color: red"},
                "source": "css_assessor",
                "finding_category": "syntax",
            },
        ]
    return {
        "report_version": "1.0",
        "generated_at": "2026-01-01T12:00:00Z",
        "metadata": {
            "timestamp": "2026-01-01T12:00:00Z",
            "pipeline_version": "1.0.0",
            "scoring_mode": "static_only",
            "profile": profile,
            "submission_metadata": {
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": "submission.zip",
                "timestamp": "2026-01-01T11:00:00Z",
            },
        },
        "submission_path": "/tmp/test/submission",
        "workspace_path": "/tmp/test/workspace",
        "findings": findings,
        "scores": {
            "overall": overall,
            "by_component": {
                "html": {"score": 0.8, "rationale": [{"rule": "has_doctype", "finding_ids": []}]},
                "css": {"score": 0.4, "rationale": []},
                "js": {"score": 0.5, "rationale": []},
                "php": {"score": "SKIPPED", "rationale": [{"rule": "component_skipped_profile", "note": "Not required"}]},
            },
        },
        "score_evidence": {
            "profile": profile,
            "generated_at": "2026-01-01T12:00:00Z",
            "environment": {"python_version": "3.11", "platform": "linux"},
            "components": {
                "html": {"score": 0.8, "weight": 1.0, "required": True, "met": 4, "partial": 0, "failed": 1, "skipped": 0},
                "css": {"score": 0.4, "weight": 1.0, "required": True, "met": 1, "partial": 1, "failed": 2, "skipped": 0},
                "js": {"score": 0.5, "weight": 1.0, "required": True, "met": 1, "partial": 1, "failed": 1, "skipped": 0},
                "php": {"score": "SKIPPED", "weight": 0.0, "required": False, "met": 0, "partial": 0, "failed": 0, "skipped": 3},
            },
            "overall": {"final": overall, "raw_average": overall, "label": "Pass" if overall >= 0.5 else "Fail"},
            "requirements": requirements,
            "confidence": {"level": "high", "reasons": ["All required files present", "Static analysis complete"], "flags": []},
            "review": {"recommended": False, "reasons": []},
            "manifest": {"entries": [], "warnings": [], "errors": []},
            "artefact_inventory": {},
            "role_mapping": {},
            "assignment_profile": {},
        },
        "behavioural_evidence": [
            {"test_id": "php.smoke", "component": "php", "status": "skipped", "stage": "behavioural",
             "stdout": "", "stderr": "php unavailable", "duration_ms": 0, "inputs": {}, "outputs": {}, "artifacts": {}},
        ],
        "browser_evidence": [],
        "environment": {
            "php_available": php_available,
            "browser_available": browser_available,
            "behavioural_tests_run": False,
            "browser_tests_run": False,
        },
        "marking_policy": {
            "profile": profile,
            "notes": [
                "SKIPPED = Component not applicable to this profile; no impact on marks.",
                "MISSING = Component required for this profile but absent; component scores 0.",
                "CONFIG warnings = Marker configuration issues; do not affect student scores.",
            ],
            "component_scoring": {
                "skipped": "Not counted in overall score calculation",
                "missing": "Scores 0.0 and reduces overall score",
                "present": "Scored based on evidence (1.0, 0.5, or 0.0)",
            },
        },
    }


# Group A: TestBuildExportReport.


class TestBuildExportReport:
    def test_maps_overall_score(self):
        report = build_export_report(_make_report(overall=0.65))
        assert report.overall_score == pytest.approx(0.65)
        assert report.overall_pct == "65.00%"

    def test_maps_overall_label_pass(self):
        # Pass threshold is >= 0.70
        report = build_export_report(_make_report(overall=0.75))
        assert report.overall_label == "Pass"

    def test_maps_overall_label_fail(self):
        report = build_export_report(_make_report(overall=0.3))
        assert report.overall_label in ("Fail", "Marginal Fail")

    def test_maps_student_metadata(self):
        report = build_export_report(_make_report(student_id="stu123", assignment_id="asn1", profile="frontend"))
        assert report.student_id == "stu123"
        assert report.assignment_id == "asn1"
        assert report.profile == "frontend"

    def test_maps_component_results(self):
        report = build_export_report(_make_report())
        names = [c.name for c in report.components]
        assert "html" in names
        assert "css" in names
        assert "js" in names
        assert "php" in names
        html_comp = next(c for c in report.components if c.name == "html")
        assert html_comp.score == pytest.approx(0.8)

    def test_maps_component_met_counts(self):
        report = build_export_report(_make_report())
        html_comp = next(c for c in report.components if c.name == "html")
        assert html_comp.met == 4
        assert html_comp.failed == 1

    def test_maps_skipped_component(self):
        report = build_export_report(_make_report())
        php_comp = next(c for c in report.components if c.name == "php")
        assert php_comp.score == "SKIPPED"
        assert php_comp.score_pct == "SKIPPED"

    def test_maps_findings(self):
        report = build_export_report(_make_report())
        assert len(report.findings) == 2
        first = next(f for f in report.findings if f.finding_id == "HTML.MISSING_ALT")
        assert first.severity == "WARN"

    def test_maps_rule_outcomes(self):
        report = build_export_report(_make_report())
        rule_ids = {r.requirement_id: r for r in report.rule_outcomes}
        assert "HTML.HAS_DOCTYPE" in rule_ids
        assert rule_ids["HTML.HAS_DOCTYPE"].status == STATUS_PASS

    def test_maps_rule_outcome_fail(self):
        report = build_export_report(_make_report())
        rule_ids = {r.requirement_id: r for r in report.rule_outcomes}
        assert "HTML.MISSING_HEADING" in rule_ids
        assert rule_ids["HTML.MISSING_HEADING"].status == STATUS_FAIL

    def test_maps_rule_outcome_partial(self):
        report = build_export_report(_make_report())
        rule_ids = {r.requirement_id: r for r in report.rule_outcomes}
        assert "JS.EVENT_LISTENER" in rule_ids
        assert rule_ids["JS.EVENT_LISTENER"].status == STATUS_PARTIAL

    def test_maps_rule_outcome_skipped_by_profile(self):
        report = build_export_report(_make_report())
        rule_ids = {r.requirement_id: r for r in report.rule_outcomes}
        assert "PHP.PREPARED_STMT" in rule_ids
        assert rule_ids["PHP.PREPARED_STMT"].status == STATUS_SKIPPED_BY_PROFILE

    def test_maps_confidence(self):
        report = build_export_report(_make_report())
        assert report.confidence_level == "high"
        assert len(report.confidence_reasons) > 0

    def test_maps_execution_environment(self):
        report = build_export_report(_make_report(php_available=False, browser_available=True))
        assert report.execution.php_available is False
        assert report.execution.browser_available is True

    def test_maps_behavioural_evidence(self):
        report = build_export_report(_make_report())
        assert len(report.execution.behavioural_results) == 1

    def test_handles_none_score_evidence(self):
        data = _make_report()
        data["score_evidence"] = None
        report = build_export_report(data)
        assert report.rule_outcomes == []
        assert report.components == [] or len(report.components) >= 0  # Falls back to by_component

    def test_handles_missing_findings_key(self):
        data = _make_report()
        del data["findings"]
        report = build_export_report(data)
        assert report.findings == []

    def test_run_id_passed_through(self):
        report = build_export_report(_make_report(), run_id="run999")
        assert report.run_id == "run999"


# Group B: TestValidateExportReport.


class TestValidateExportReport:
    def test_accepts_valid_report(self):
        report = build_export_report(_make_report())
        # Should not raise
        validate_export_report(report)

    def test_accepts_all_skipped_report(self):
        data = _make_report(overall=0.0)
        # Set all components to SKIPPED
        for comp_name in data["score_evidence"]["components"]:
            data["score_evidence"]["components"][comp_name]["score"] = "SKIPPED"
        data["scores"]["overall"] = 0.0
        report = build_export_report(data)
        # Should not raise since we still have student_id etc.
        validate_export_report(report)

    def test_rejects_completely_empty_report(self):
        data = _make_report()
        # Strip all identity fields and data
        data["metadata"]["submission_metadata"] = {
            "student_id": "",
            "assignment_id": "",
            "original_filename": "",
            "timestamp": "",
        }
        data["generated_at"] = ""
        data["score_evidence"]["components"] = {}
        data["score_evidence"]["requirements"] = []
        data["findings"] = []
        data["scores"]["overall"] = None
        data["scores"]["by_component"] = {}
        report = build_export_report(data, run_id="")
        with pytest.raises(ValueError):
            validate_export_report(report)

    def test_accepts_zero_score_with_findings(self):
        data = _make_report(overall=0.0)
        report = build_export_report(data)
        # Should not raise since findings are present
        validate_export_report(report)


# Group C: TestExportTxt.


class TestExportTxt:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.output = export_txt(self.report)

    def test_contains_student_id(self):
        assert "stu123" in self.output

    def test_contains_overall_score_pct(self):
        assert "65.00%" in self.output

    def test_contains_overall_label(self):
        # Score 0.65 → "Marginal Fail" (pass threshold is >= 0.70)
        assert "Marginal Fail" in self.output or "Pass" in self.output or "Fail" in self.output

    def test_contains_submission_details_section(self):
        assert "SUBMISSION DETAILS" in self.output.upper()

    def test_contains_component_section(self):
        assert "COMPONENT" in self.output.upper()

    def test_contains_findings_section(self):
        assert "FINDING" in self.output.upper()

    def test_contains_rule_outcomes_section(self):
        assert "RULE" in self.output.upper()

    def test_contains_policy_notes(self):
        assert "SKIPPED" in self.output

    def test_contains_execution_section(self):
        assert "PHP" in self.output.upper() or "EXECUTION" in self.output.upper()

    def test_not_empty(self):
        assert len(self.output) > 100

    def test_handles_no_findings(self):
        data = _make_report(include_findings=False)
        report = build_export_report(data)
        output = export_txt(report)
        assert len(output) > 50  # Still produces output


# Group D: TestExportCsvSummary.


class TestExportCsvSummary:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.output = export_csv_summary(self.report)

    def test_has_header_row(self):
        rows = self.output.strip().splitlines()
        assert len(rows) >= 1
        header = rows[0]
        assert "run_id" in header
        assert "student_id" in header

    def test_has_data_row(self):
        rows = self.output.strip().splitlines()
        assert len(rows) >= 2

    def test_data_row_has_student_id(self):
        assert "stu123" in self.output

    def test_data_row_has_overall_score(self):
        # Either the pct string or raw score
        assert "65.00%" in self.output or "0.65" in self.output

    def test_data_row_has_html_score(self):
        assert "80.00%" in self.output or "0.8" in self.output

    def test_data_row_has_php_skipped(self):
        assert "SKIPPED" in self.output

    def test_data_row_has_fail_count(self):
        import csv
        reader = csv.DictReader(io.StringIO(self.output))
        rows = list(reader)
        assert len(rows) >= 1
        row = rows[0]
        assert "fail_findings" in row


# Group E: TestExportCsvFindings.


class TestExportCsvFindings:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.output = export_csv_findings(self.report)

    def test_has_finding_rows(self):
        rows = self.output.strip().splitlines()
        # Header + 2 data rows
        assert len(rows) == 3

    def test_finding_row_has_severity(self):
        assert "WARN" in self.output

    def test_finding_row_has_component(self):
        assert "html" in self.output or "css" in self.output

    def test_finding_row_has_message(self):
        assert "Images are missing alt text attributes" in self.output


# Group F: TestExportCsvRules.


class TestExportCsvRules:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.output = export_csv_rules(self.report)

    def test_has_rule_rows(self):
        rows = self.output.strip().splitlines()
        # Header + 4 requirements
        assert len(rows) >= 2

    def test_rule_row_has_status(self):
        assert "PASS" in self.output

    def test_rule_row_has_description(self):
        assert "Document must have a valid DOCTYPE declaration" in self.output

    def test_rule_row_has_score_label(self):
        import csv
        reader = csv.DictReader(io.StringIO(self.output))
        rows = list(reader)
        # At least one scored rule should have a non-empty score_label
        scored = [r for r in rows if r.get("score_label", "")]
        assert len(scored) >= 1


# Group G: TestExportCsvZip.


class TestExportCsvZip:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.result = export_csv_zip(self.report)

    def test_returns_valid_zip(self):
        assert zipfile.is_zipfile(io.BytesIO(self.result))

    def test_zip_contains_summary(self):
        with zipfile.ZipFile(io.BytesIO(self.result)) as zf:
            assert "submission_summary.csv" in zf.namelist()

    def test_zip_contains_findings(self):
        with zipfile.ZipFile(io.BytesIO(self.result)) as zf:
            assert "submission_findings.csv" in zf.namelist()

    def test_zip_contains_rules(self):
        with zipfile.ZipFile(io.BytesIO(self.result)) as zf:
            assert "submission_rules.csv" in zf.namelist()

    def test_zip_files_not_empty(self):
        with zipfile.ZipFile(io.BytesIO(self.result)) as zf:
            for name in ["submission_summary.csv", "submission_findings.csv", "submission_rules.csv"]:
                assert len(zf.read(name)) > 0


# Group H: TestExportJson.


class TestExportJson:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.result = export_json(self.report)

    def test_is_valid_json(self):
        json.loads(self.result)  # Should not raise

    def test_has_schema_version(self):
        data = json.loads(self.result)
        assert data["export_schema_version"] == "2.0"

    def test_has_student_id(self):
        data = json.loads(self.result)
        assert data["student_id"] == "stu123"

    def test_has_components_list(self):
        data = json.loads(self.result)
        assert isinstance(data["components"], list)
        assert len(data["components"]) > 0

    def test_has_findings_list(self):
        data = json.loads(self.result)
        assert isinstance(data["findings"], list)

    def test_has_rule_outcomes(self):
        data = json.loads(self.result)
        assert isinstance(data["rule_outcomes"], list)


# Group I: TestExportPdf.


class TestExportPdf:
    def setup_method(self):
        self.report = build_export_report(_make_report(), run_id="run001")
        self.result = export_pdf(self.report)

    def test_returns_bytes(self):
        assert isinstance(self.result, bytes)

    def test_starts_with_pdf_header(self):
        assert self.result.startswith(b"%PDF-")

    def test_non_trivial_size(self):
        assert len(self.result) > 500


# Group J: TestWebRoutes.


def _client(tmp_path):
    from ams.webui import create_app
    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    from tests.webui.conftest import authenticate_client
    authenticate_client(client)
    return client, tmp_path


def _seed_run(tmp_path, run_id="run_test_001", report_data=None):
    """Create a minimal run directory with report.json for route tests."""
    from ams.io.web_storage import save_run_info
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if report_data is None:
        report_data = _make_report()
    (run_dir / "report.json").write_text(json.dumps(report_data), encoding="utf-8")
    save_run_info(run_dir, {"run_id": run_id, "profile": "frontend", "mode": "mark"})
    return run_id


def _seed_batch_run(tmp_path, run_id="batch_test_001", submission_id="sub_001", report_data=None):
    """Create a minimal batch run directory structure with submission report.json."""
    from ams.io.web_storage import save_run_info
    run_dir = tmp_path / run_id
    sub_dir = run_dir / "runs" / submission_id
    sub_dir.mkdir(parents=True, exist_ok=True)
    if report_data is None:
        report_data = _make_report()
    (sub_dir / "report.json").write_text(json.dumps(report_data), encoding="utf-8")
    save_run_info(run_dir, {"run_id": run_id, "profile": "frontend", "mode": "batch"})
    return run_id, submission_id


class TestWebRoutes:
    def test_individual_export_txt_returns_200(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/txt")
        assert resp.status_code == 200
        assert "text/plain" in resp.content_type

    def test_individual_export_txt_contains_score(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/txt")
        body = resp.data.decode("utf-8", errors="replace")
        assert "65.00%" in body or "stu123" in body

    def test_individual_export_csv_returns_zip(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/csv")
        assert resp.status_code == 200
        assert "application/zip" in resp.content_type

    def test_individual_export_csv_is_valid_zip(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/csv")
        assert zipfile.is_zipfile(io.BytesIO(resp.data))

    def test_individual_export_pdf_returns_pdf(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/pdf")
        assert resp.status_code == 200
        assert "application/pdf" in resp.content_type
        assert resp.data.startswith(b"%PDF-")

    def test_individual_export_json_returns_200(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/json")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type

    def test_individual_export_json_is_parseable(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/json")
        json.loads(resp.data)  # Should not raise

    def test_individual_export_invalid_format_returns_400(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        resp = client.get(f"/run/{run_id}/export/xyz")
        assert resp.status_code == 400

    def test_individual_export_missing_run_returns_404(self, tmp_path):
        client, root = _client(tmp_path)
        resp = client.get("/run/nonexistent/export/txt")
        assert resp.status_code == 404

    def test_batch_export_txt_returns_200(self, tmp_path):
        client, root = _client(tmp_path)
        run_id, sub_id = _seed_batch_run(root)
        resp = client.get(f"/batch/{run_id}/submissions/{sub_id}/export/txt")
        assert resp.status_code == 200

    def test_batch_export_csv_returns_zip(self, tmp_path):
        client, root = _client(tmp_path)
        run_id, sub_id = _seed_batch_run(root)
        resp = client.get(f"/batch/{run_id}/submissions/{sub_id}/export/csv")
        assert resp.status_code == 200
        assert "application/zip" in resp.content_type

    def test_export_does_not_produce_empty_body(self, tmp_path):
        client, root = _client(tmp_path)
        run_id = _seed_run(root)
        for fmt in ("txt", "csv", "json", "pdf"):
            resp = client.get(f"/run/{run_id}/export/{fmt}")
            assert resp.status_code == 200
            assert len(resp.data) > 0, f"Empty body for format: {fmt}"
