from __future__ import annotations

from ams.analytics import build_teacher_analytics


def test_build_teacher_analytics_fields():
    batch_summary = {
        "records": [
            {"id": "a", "overall": 1.0, "components": {"html": 1, "css": 1, "js": 1, "php": "SKIPPED", "sql": "SKIPPED"}, "status": "ok", "findings": []},
            {"id": "b", "overall": 0.0, "components": {"html": 0, "css": 0, "js": 0, "php": "SKIPPED", "sql": "SKIPPED"}, "status": "ok", "findings": []},
        ],
        "summary": {
            "profile": "frontend",
            "overall_stats": {"mean": 0.5, "median": 0.5, "min": 0.0, "max": 1.0},
            "buckets": {"zero": 1, "gt_0_to_0_5": 0, "gt_0_5_to_1": 0, "one": 1},
            "finding_frequency": {"HTML.MISSING": 1, "CSS.MISSING": 1},
        },
    }
    analytics = build_teacher_analytics(batch_summary)
    assert "overall" in analytics
    assert "components" in analytics
    assert "common_issues" in analytics
    assert len(analytics["needs_attention"]) >= 1
    comp_html = analytics["components"]["html"]
    assert comp_html["average"] == 0.5
    assert analytics["overall"]["buckets"]["No attempt (0%)"] == 1
    assert analytics["overall"]["buckets"]["Partial (1–50%)"] == 0


def test_student_issues_excludes_pass_and_dedups_missing():
    records = [
        {"id": "s1", "overall": 0.5, "components": {}, "status": "ok", "findings": [{"id": "PHP.MISSING_FILES", "severity": "FAIL", "finding_category": "missing"}, {"id": "PHP.REQ.MISSING_FILES", "severity": "FAIL", "finding_category": "missing"}]},
        {"id": "s2", "overall": 1.0, "components": {}, "status": "ok", "findings": [{"id": "HTML.PARSE_OK", "severity": "INFO", "finding_category": "other"}]},
    ]
    batch_summary = {"records": records, "summary": {"profile": "fullstack", "overall_stats": {}, "buckets": {"zero": 0, "gt_0_to_0_5": 0, "gt_0_5_to_1": 0, "one": 0}, "finding_frequency": {}}}
    analytics = build_teacher_analytics(batch_summary)
    issues = analytics.get("student_issues", [])
    assert any(i["category"] == "missing_backend" and i["students_affected"] == 1 for i in issues)
    # PASS info should not surface
    assert not any(i for i in issues if "parse ok" in str(i).lower())


def test_runner_limitations_from_skipped_findings():
    records = [
        {
            "id": "s1",
            "overall": 0.5,
            "components": {},
            "status": "ok",
            "findings": [
                {"id": "BEHAVIOUR.PHP_SMOKE_SKIPPED", "severity": "INFO", "finding_category": "behavioural"},
                {"id": "BEHAVIOUR.PHP_FORM_RUN_SKIPPED", "severity": "INFO", "finding_category": "behavioural"},
            ],
        },
        {
            "id": "s2",
            "overall": 1.0,
            "components": {},
            "status": "ok",
            "findings": [{"id": "BROWSER.PAGE_LOAD_SKIPPED", "severity": "INFO", "finding_category": "behavioural"}],
        },
    ]
    batch_summary = {"records": records, "summary": {"profile": "fullstack", "overall_stats": {}, "buckets": {"zero": 0, "gt_0_to_0_5": 1, "gt_0_5_to_1": 1, "one": 0}, "finding_frequency": {}}}
    analytics = build_teacher_analytics(batch_summary)
    assert "runtime_health" not in analytics
    runner_limits = analytics["runner_limitations"]
    assert any(r["category"] == "behavioural_skipped" for r in runner_limits)
