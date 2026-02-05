"""Profile requirement semantics and SKIPPED vs MISSING behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from ams.core.profiles import get_profile_spec


def test_profile_required_flags():
    frontend = get_profile_spec("frontend")
    fullstack = get_profile_spec("fullstack")

    assert frontend.relevant_artefacts == ["html", "css", "js"]
    assert fullstack.relevant_artefacts == ["html", "css", "js", "php", "sql"]


def test_profile_required_rules_presence():
    frontend = get_profile_spec("frontend")
    fullstack = get_profile_spec("fullstack")

    assert frontend.has_required_rules("html")
    assert frontend.has_required_rules("css")
    assert frontend.has_required_rules("js")
    assert not frontend.has_required_rules("php")
    assert not frontend.has_required_rules("sql")

    assert fullstack.has_required_rules("php")
    assert fullstack.has_required_rules("sql")


@pytest.mark.parametrize(
    "profile,component,expected_id,expected_severity",
    [
        ("frontend", "php", "PHP.SKIPPED", "SKIPPED"),
        ("frontend", "sql", "SQL.SKIPPED", "SKIPPED"),
        ("frontend", "html", "HTML.MISSING_FILES", "FAIL"),
        ("fullstack", "php", "PHP.MISSING_FILES", "FAIL"),
        ("fullstack", "sql", "SQL.MISSING_FILES", "FAIL"),
    ],
)
def test_static_assessors_mark_skipped_or_missing(tmp_path: Path, run_pipeline, profile, component, expected_id, expected_severity):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile=profile)
    findings = report["findings"]
    match = [f for f in findings if f["id"] == expected_id]
    assert match, f"{expected_id} not produced"
    assert match[0]["severity"] == expected_severity
    assert match[0]["evidence"].get("profile") == profile


def test_required_rules_skip_vs_missing(tmp_path: Path, run_pipeline):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    frontend_report = run_pipeline(submission_dir, profile="frontend")
    php_req_skipped = [f for f in frontend_report["findings"] if f["id"] == "PHP.REQ.SKIPPED"]
    assert php_req_skipped and php_req_skipped[0]["severity"] == "SKIPPED"

    fullstack_report = run_pipeline(submission_dir, profile="fullstack")
    php_req_missing = [f for f in fullstack_report["findings"] if f["id"] == "PHP.REQ.MISSING_FILES"]
    assert php_req_missing and php_req_missing[0]["severity"] == "FAIL"
    assert php_req_missing[0]["evidence"].get("required") is True


def test_scoring_respects_skipped_components(tmp_path: Path, run_pipeline):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text("<!doctype html><html><body>Hi</body></html>", encoding="utf-8")

    report = run_pipeline(submission_dir, profile="frontend")
    by_component = report["scores"]["by_component"]
    assert by_component["php"]["score"] == "SKIPPED"
    assert by_component["sql"]["score"] == "SKIPPED"
    assert isinstance(report["scores"]["overall"], (int, float))


def test_scoring_penalises_missing_required_components(tmp_path: Path, run_pipeline):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text("<!doctype html><html><body>Hi</body></html>", encoding="utf-8")

    report = run_pipeline(submission_dir, profile="fullstack")
    by_component = report["scores"]["by_component"]
    assert by_component["php"]["score"] == 0.0
    assert by_component["sql"]["score"] == 0.0
    assert report["scores"]["overall"] < 1.0


def test_findings_and_evidence_carry_profile_metadata(tmp_path: Path, run_pipeline):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    report = run_pipeline(submission_dir, profile="fullstack")
    findings = report["findings"]
    missing_findings = [f for f in findings if "MISSING_FILES" in f["id"]]
    skipped_findings = [f for f in findings if "SKIPPED" in f["id"]]

    for finding in missing_findings + skipped_findings:
        evidence = finding.get("evidence", {})
        if "profile" in evidence:
            assert evidence["profile"] == "fullstack"
        if "required" in evidence:
            assert isinstance(evidence["required"], bool)

    components = report["score_evidence"]["components"]
    assert components["php"]["required"] is True
    assert components["html"]["required"] is True
    assert components["php"]["score"] == 0.0
