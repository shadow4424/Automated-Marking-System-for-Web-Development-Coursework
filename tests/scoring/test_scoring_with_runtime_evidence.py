from __future__ import annotations

from ams.core.models import BehaviouralEvidence, BrowserEvidence, Finding, FindingCategory, Severity
from ams.core.scoring import ScoringEngine


def _f(fid: str, category: str) -> Finding:
    return Finding(
        id=fid,
        category=category,
        message=fid,
        severity=Severity.INFO,
        evidence={},
        source="test",
        finding_category=FindingCategory.OTHER,
    )


def test_php_behavioural_pass_raises_floor() -> None:
    findings = [_f("PHP.TAG_MISSING", "php")]
    behavioural = [BehaviouralEvidence(test_id="PHP.SMOKE", status="pass", component="php", duration_ms=10)]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="fullstack", behavioural_evidence=behavioural)
    php_score = scores["by_component"]["php"]["score"]
    assert php_score >= 0.5


def test_php_behavioural_fail_caps_full_score() -> None:
    findings = [_f("PHP.TAG_OK", "php"), _f("PHP.SYNTAX_OK", "php")]
    behavioural = [BehaviouralEvidence(test_id="PHP.SMOKE", status="fail", component="php", duration_ms=10)]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="fullstack", behavioural_evidence=behavioural)
    assert scores["by_component"]["php"]["score"] == 0.5


def test_sql_exec_pass_reaches_full_with_static() -> None:
    findings = [
        _f("SQL.STRUCTURE_OK", "sql"),
        Finding(
            id="SQL.EVIDENCE",
            category="sql",
            message="",
            severity=Severity.INFO,
            evidence={"create_table": 1, "insert_into": 1, "select": 1},
            source="test",
            finding_category=FindingCategory.OTHER,
        ),
    ]
    behavioural = [BehaviouralEvidence(test_id="SQL.SQLITE_EXEC", status="pass", component="sql", duration_ms=20)]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="fullstack", behavioural_evidence=behavioural)
    assert scores["by_component"]["sql"]["score"] == 1.0


def test_browser_page_fail_caps_html() -> None:
    findings = [_f("HTML.PARSE_OK", "html")]
    browser = [
        BrowserEvidence(
            test_id="BROWSER.PAGE",
            status="fail",
            duration_ms=30,
            url="file://index.html",
            actions=[],
            dom_before="",
            dom_after="",
        )
    ]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="frontend", browser_evidence=browser)
    assert scores["by_component"]["html"]["score"] == 0.5


def test_console_errors_cap_js_score() -> None:
    findings = [
        _f("JS.SYNTAX_OK", "js"),
        Finding(
            id="JS.EVIDENCE",
            category="js",
            message="",
            severity=Severity.INFO,
            evidence={"dom_calls": 1},
            source="test",
            finding_category=FindingCategory.OTHER,
        ),
    ]
    browser = [
        BrowserEvidence(
            test_id="BROWSER.PAGE",
            status="pass",
            duration_ms=20,
            url="file://index.html",
            actions=[{"type": "form_submit"}],
            dom_before="",
            dom_after="",
            console_errors=["ReferenceError"],
        )
    ]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="frontend", browser_evidence=browser)
    assert scores["by_component"]["js"]["score"] == 0.5


def test_overall_quantization_with_mixed_scores() -> None:
    findings = [
        _f("HTML.PARSE_OK", "html"),
        _f("CSS.MISSING_FILES", "css"),
        _f("JS.NO_CODE", "js"),
    ]
    scores, _ = ScoringEngine().score_with_evidence(findings, profile="frontend")
    assert scores["overall"] in {0.0, 0.5, 1.0}
    assert scores["overall"] == 0.5
