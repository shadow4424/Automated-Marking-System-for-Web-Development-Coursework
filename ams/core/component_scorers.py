"""Per-component scoring analysers and stateless scoring helpers.

Each ``analyse_<component>`` function produces a score, rationale, and
summaries dict for a single language component.  The helpers
(view builders, penalty calculators, rule-weight logic) are also
collected here so ``scoring.py`` stays focused on orchestration.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Sequence, Tuple

from ams.core.finding_ids import API as AID, CSS as CID, HTML as HID, JS as JID, PHP as PID, SQL as SID
from ams.core.models import (
    BehaviouralEvidence,
    BrowserEvidence,
    Finding,
    Severity,
)


# ---------------------------------------------------------------------------
# View / summary helpers
# ---------------------------------------------------------------------------

def static_summary(component: str, findings: List[Finding]) -> Dict[str, object]:
    """Return a quick static-analysis summary dict for *component*."""
    ids = [f.id for f in findings]
    return {
        "component": component,
        "missing": any("MISSING_FILES" in fid for fid in ids),
        "skipped": any("SKIPPED" in fid for fid in ids),
        "finding_ids": ids,
    }


def behavioural_view(evidence: List[BehaviouralEvidence]) -> Dict[str, object]:
    """Collapse behavioural evidence into a flat status dict."""
    view: Dict[str, object] = {}
    for ev in evidence:
        test_id = (getattr(ev, "test_id", "") or "").upper()
        status = (getattr(ev, "status", "") or "").lower()
        if test_id.startswith("PHP.SMOKE"):
            view["php_smoke"] = status
        elif test_id.startswith("PHP.FORM"):
            view["php_form"] = status
        elif test_id.startswith("SQL.SQLITE_EXEC"):
            view["sql_exec"] = status
        elif test_id.startswith("API.EXEC"):
            view["api_exec"] = status
        if status == "skipped" and getattr(ev, "component", "") == "php":
            view["php_skipped_env"] = True
    return view


def browser_view(evidence: List[BrowserEvidence]) -> Dict[str, object]:
    """Collapse browser evidence into a flat status dict."""
    if not evidence:
        return {}
    ev = evidence[0]
    status = (getattr(ev, "status", "") or "").lower()
    actions = list(getattr(ev, "actions", []) or [])
    interacted = any(a.get("type") in {"form_submit", "click"} for a in actions)
    interaction_skipped = any(a.get("type") == "interaction_skipped" for a in actions)
    interaction_status = "skipped" if interaction_skipped else ("pass" if interacted and status == "pass" else None)
    return {
        "page_status": status,
        "interaction_status": interaction_status,
        "interacted": interacted,
        "console_errors": len(getattr(ev, "console_errors", []) or []),
        "actions": actions,
    }


# ---------------------------------------------------------------------------
# Penalty / bonus calculators
# ---------------------------------------------------------------------------

def calculate_quality_penalty(findings: List[Finding], component: str) -> float:
    """Calculate penalty for code quality, security, and consistency issues."""
    penalty = 0.0
    quality_findings = [f for f in findings if "QUALITY" in f.id or "SECURITY" in f.id or "CONSISTENCY" in f.id]
    for finding in quality_findings:
        severity = finding.severity
        if severity == Severity.FAIL:
            penalty += 0.2
        elif severity == Severity.WARN:
            penalty += 0.1
    return min(0.5, penalty)


def calculate_functional_test_score(
    findings: List[Finding],
    browser_evidence_list: List[BrowserEvidence],
) -> float | None:
    """Calculate score based on functional test results."""
    functional_findings = [f for f in findings if "BROWSER.FUNCTIONAL" in f.id and not f.id.endswith(".SUMMARY")]
    if not functional_findings:
        return None
    passed = sum(1 for f in functional_findings if f.severity == Severity.INFO and "passed" in f.message.lower())
    total = len(functional_findings)
    if total == 0:
        return None
    return passed / total


def calculate_performance_penalty(findings: List[Finding]) -> float:
    """Calculate penalty for performance issues."""
    penalty = 0.0
    performance_findings = [f for f in findings if "BROWSER.PERFORMANCE" in f.id and f.severity == Severity.FAIL]
    for _ in performance_findings:
        penalty += 0.1
    return min(0.3, penalty)


def calculate_error_penalty(findings: List[Finding]) -> float:
    """Calculate penalty for runtime errors."""
    penalty = 0.0
    error_findings = [f for f in findings if "BROWSER.ERROR" in f.id]
    for finding in error_findings:
        if finding.severity == Severity.FAIL:
            penalty += 0.15
        elif finding.severity == Severity.WARN:
            penalty += 0.05
    return min(0.4, penalty)


# ---------------------------------------------------------------------------
# Weighted rule-score helpers
# ---------------------------------------------------------------------------

def build_rule_weight_map(
    findings: List[Finding],
    component: str,
) -> Dict[str, float]:
    """Build the rule weight map."""
    _ = component
    return {
        str(finding.evidence.get("rule_id", "unknown")): float(finding.evidence.get("weight", 1.0))
        for finding in findings
        if finding.id.endswith(".REQ.PASS") or finding.id.endswith(".REQ.FAIL")
    }


def apply_weights_to_findings(
    findings: List[Finding],
    weight_map: Mapping[str, float],
) -> Tuple[float, List[dict]]:
    """Apply the weights to findings."""
    req_pass_findings = [f for f in findings if f.id.endswith(".REQ.PASS")]
    req_fail_findings = [f for f in findings if f.id.endswith(".REQ.FAIL")]

    if not req_pass_findings and not req_fail_findings:
        return 0.0, []

    total_weight = 0.0
    passed_weight = 0.0
    rule_details = []

    for finding in req_pass_findings:
        rule_id = str(finding.evidence.get("rule_id", "unknown"))
        weight = float(weight_map.get(rule_id, finding.evidence.get("weight", 1.0)))
        total_weight += weight
        passed_weight += weight
        rule_details.append({
            "rule": rule_id,
            "status": "pass",
            "weight": weight,
            "finding_ids": [finding.id],
        })

    for finding in req_fail_findings:
        rule_id = str(finding.evidence.get("rule_id", "unknown"))
        weight = float(weight_map.get(rule_id, finding.evidence.get("weight", 1.0)))
        total_weight += weight

        partial_credit = 0.0
        status = "fail"
        llm_adjusted = False

        vision = finding.evidence.get("vision_analysis", {})
        if isinstance(vision, dict) and vision.get("status") == "PASS":
            partial_credit = weight
            status = "pass_vision"
            llm_adjusted = True

        elif isinstance(finding.evidence, dict):
            hybrid = finding.evidence.get("hybrid_score", {})
            if isinstance(hybrid, dict) and hybrid.get("final_score") is not None:
                partial_score = float(hybrid["final_score"])
                partial_credit = weight * partial_score
                status = f"partial_{int(partial_score * 100)}%"
                llm_adjusted = True

        passed_weight += partial_credit

        rule_details.append({
            "rule": rule_id,
            "status": status,
            "weight": weight,
            "partial_credit": partial_credit,
            "llm_adjusted": llm_adjusted,
            "finding_ids": [finding.id],
        })

    if total_weight == 0:
        return 0.0, []

    weighted_score = passed_weight / total_weight
    return weighted_score, rule_details


def calculate_weighted_rule_score(findings: List[Finding], component: str) -> Tuple[float, List[dict]]:
    """Calculate weighted score from required rule findings (HTML.REQ.PASS/FAIL, etc.)."""
    weight_map = build_rule_weight_map(findings, component)
    return apply_weights_to_findings(findings, weight_map)


# ---------------------------------------------------------------------------
# Per-component analysers
# ---------------------------------------------------------------------------

def analyse_html(
    findings: List[Finding],
    browser_evidence_list: List[BrowserEvidence],
) -> Dict[str, object]:
    """Analyse the html."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("html", findings)
    ids = {f.id for f in findings}
    if HID.SKIPPED in ids:
        rationale.append({"rule": "html_skipped", "finding_ids": [HID.SKIPPED], "note": "HTML not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}
    if HID.MISSING_FILES in ids or HID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "html_missing", "finding_ids": missing_ids, "note": "HTML required but files missing"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    parse_ok = HID.PARSE_OK in ids
    parse_suspect = HID.PARSE_SUSPECT in ids
    evidence_findings = [f for f in findings if f.id == HID.ELEMENT_EVIDENCE]

    bview = browser_view(browser_evidence_list)
    summaries["browser_summary"] = bview

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "html")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.5
    if parse_ok:
        rationale.append(
            {
                "rule": "html_structure_ok",
                "finding_ids": [HID.PARSE_OK],
                "evidence": evidence_findings[0].evidence if evidence_findings else {},
            }
        )
        base_score = 1.0
    elif parse_suspect or evidence_findings:
        rationale.append(
            {
                "rule": "html_structure_suspect_or_minimal",
                "finding_ids": [fid for fid in ids],
                "evidence": evidence_findings[0].evidence if evidence_findings else {},
            }
        )
        base_score = 0.5
    else:
        rationale.append({"rule": "html_default_partial", "finding_ids": [fid for fid in ids]})
        base_score = 0.5

    if weighted_rule_score > 0:
        base_score = weighted_rule_score

    quality_penalty = calculate_quality_penalty(findings, "html")
    if quality_penalty > 0:
        base_score = max(0.0, base_score - quality_penalty)
        rationale.append({
            "rule": "html_quality_penalty",
            "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
            "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
        })

    if bview:
        if bview.get("page_status") in {"fail", "timeout"}:
            adjusted = 0.5 if base_score >= 0.5 else 0.0
            rationale.append(
                {
                    "rule": "browser_page_issue",
                    "finding_ids": ["BROWSER.PAGE_LOAD_FAIL" if bview.get("page_status") == "fail" else "BROWSER.PAGE_LOAD_TIMEOUT"],
                    "note": "Browser page load did not complete",
                }
            )
            base_score = min(base_score, adjusted)
        elif bview.get("page_status") == "pass" and base_score >= 0.5:
            rationale.append(
                {
                    "rule": "browser_page_pass",
                    "finding_ids": ["BROWSER.PAGE_LOAD_PASS"],
                    "note": "Browser page load succeeded",
                }
            )

    return {"score": base_score, "rationale": rationale, "summaries": summaries}


def analyse_css(
    findings: List[Finding],
    _evidence: object = None,
) -> Dict[str, object]:
    """Analyse the css."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("css", findings)
    ids = {f.id for f in findings}
    if CID.SKIPPED in ids:
        rationale.append({"rule": "css_skipped", "finding_ids": [CID.SKIPPED], "note": "CSS not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}
    if CID.MISSING_FILES in ids or CID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "css_missing", "finding_ids": missing_ids, "note": "CSS required but files missing"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    balanced = CID.BRACES_BALANCED in ids
    unbalanced = CID.BRACES_UNBALANCED in ids
    no_rules = CID.NO_RULES in ids
    selectors_approx = sum(int(f.evidence.get("selectors_approx", 0)) for f in findings if f.id == CID.EVIDENCE)

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "css")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.5
    if balanced and selectors_approx >= 1:
        rationale.append(
            {
                "rule": "css_balanced_with_selectors",
                "finding_ids": [CID.BRACES_BALANCED, CID.EVIDENCE],
                "evidence": {"selectors_approx": selectors_approx},
            }
        )
        base_score = 1.0
    elif unbalanced or no_rules or selectors_approx == 0:
        rationale.append(
            {
                "rule": "css_partial_or_suspect",
                "finding_ids": [fid for fid in ids],
                "evidence": {"selectors_approx": selectors_approx},
            }
        )
        base_score = 0.5
    else:
        rationale.append({"rule": "css_default_partial", "finding_ids": [fid for fid in ids]})
        base_score = 0.5

    if weighted_rule_score > 0:
        base_score = weighted_rule_score

    quality_penalty = calculate_quality_penalty(findings, "css")
    if quality_penalty > 0:
        base_score = max(0.0, base_score - quality_penalty)
        rationale.append({
            "rule": "css_quality_penalty",
            "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
            "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
        })

    return {"score": base_score, "rationale": rationale, "summaries": summaries}


def analyse_js(
    findings: List[Finding],
    browser_evidence_list: List[BrowserEvidence],
) -> Dict[str, object]:
    """Analyse the js."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("js", findings)
    ids = {f.id for f in findings}
    if JID.SKIPPED in ids:
        rationale.append({"rule": "js_skipped", "finding_ids": [JID.SKIPPED], "note": "JS not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}
    if JID.MISSING_FILES in ids or JID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "js_missing", "finding_ids": missing_ids, "note": "JS required but files missing"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    syntax_ok = JID.SYNTAX_OK in ids
    syntax_suspect = JID.SYNTAX_SUSPECT in ids or JID.NO_CODE in ids
    evidence_entries = [f for f in findings if f.id == JID.EVIDENCE]
    evidence_totals = {
        "dom_calls": 0,
        "query_calls": 0,
        "event_listeners": 0,
        "loops": 0,
        "functions": 0,
    }
    for entry in evidence_entries:
        for key in list(evidence_totals.keys()):
            evidence_totals[key] += int(entry.evidence.get(key, 0))

    has_activity = any(value > 0 for value in evidence_totals.values())
    bview = browser_view(browser_evidence_list)
    summaries["browser_summary"] = bview

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "js")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.5

    if syntax_ok and has_activity:
        rationale.append(
            {
                "rule": "js_syntax_ok_with_activity",
                "finding_ids": [JID.SYNTAX_OK, JID.EVIDENCE],
                "evidence": evidence_totals,
            }
        )
        base_score = 1.0
    elif syntax_suspect or not has_activity:
        rationale.append(
            {
                "rule": "js_partial_or_no_activity",
                "finding_ids": [fid for fid in ids],
                "evidence": evidence_totals,
            }
        )
        base_score = 0.5
    else:
        rationale.append({"rule": "js_default_partial", "finding_ids": [fid for fid in ids]})
        base_score = 0.5

    if weighted_rule_score > 0:
        base_score = weighted_rule_score

    quality_penalty = calculate_quality_penalty(findings, "js")
    if quality_penalty > 0:
        base_score = max(0.0, base_score - quality_penalty)
        rationale.append({
            "rule": "js_quality_penalty",
            "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
            "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
        })

    if bview:
        console_errors = bview.get("console_errors", 0)
        page_status = bview.get("page_status")
        interaction_status = bview.get("interaction_status")
        interacted = bview.get("interacted")

        if page_status in {"fail", "timeout"} and has_activity:
            base_score = min(base_score, 0.5)
            rationale.append(
                {
                    "rule": "browser_page_issue_js",
                    "finding_ids": ["BROWSER.PAGE_LOAD_FAIL" if page_status == "fail" else "BROWSER.PAGE_LOAD_TIMEOUT"],
                    "note": "Browser page load issue impacted JS",
                }
            )

        if page_status == "pass" and interaction_status == "pass" and has_activity:
            base_score = max(base_score, 1.0)
            rationale.append(
                {
                    "rule": "browser_interaction_pass",
                    "finding_ids": ["BROWSER.INTERACTION_PASS", "BROWSER.PAGE_LOAD_PASS"],
                    "note": "Browser interaction executed successfully",
                }
            )
        if console_errors and has_activity:
            base_score = min(base_score, 0.5)
            rationale.append(
                {
                    "rule": "browser_console_errors",
                    "finding_ids": ["BROWSER.CONSOLE_ERRORS_PRESENT"],
                    "note": f"Console errors observed ({console_errors})",
                }
            )
        if interacted and page_status in {"fail", "timeout"}:
            base_score = min(base_score, 0.5)

    functional_test_score = calculate_functional_test_score(findings, browser_evidence_list)
    if functional_test_score is not None:
        base_score = 0.7 * base_score + 0.3 * functional_test_score
        rationale.append({
            "rule": "functional_tests_score",
            "finding_ids": [f.id for f in findings if "BROWSER.FUNCTIONAL" in f.id],
            "note": f"Functional tests contributed {functional_test_score:.2f} to score",
        })

    performance_penalty = calculate_performance_penalty(findings)
    error_penalty = calculate_error_penalty(findings)

    if performance_penalty > 0:
        base_score = max(0.0, base_score - performance_penalty)
        rationale.append({
            "rule": "performance_penalty",
            "finding_ids": [f.id for f in findings if "BROWSER.PERFORMANCE" in f.id and f.severity == Severity.FAIL],
            "note": f"Performance issues reduced score by {performance_penalty:.2f}",
        })

    if error_penalty > 0:
        base_score = max(0.0, base_score - error_penalty)
        rationale.append({
            "rule": "error_penalty",
            "finding_ids": [f.id for f in findings if "BROWSER.ERROR" in f.id and f.severity == Severity.FAIL],
            "note": f"Runtime errors reduced score by {error_penalty:.2f}",
        })

    missing_features = [f for f in findings if "REQUIRED_FEATURES_MISSING" in f.id]
    if missing_features:
        base_score = max(0.0, base_score - 0.3)
        rationale.append({
            "rule": "missing_required_features",
            "finding_ids": [f.id for f in missing_features],
            "note": "Missing required features reduced score by 0.30",
        })

    return {"score": base_score, "rationale": rationale, "summaries": summaries}


def analyse_php(
    findings: List[Finding],
    behavioural_evidence_list: List[BehaviouralEvidence],
) -> Dict[str, object]:
    """Analyse the php."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("php", findings)
    ids = {f.id for f in findings}
    if PID.SKIPPED in ids:
        rationale.append({"rule": "php_skipped", "finding_ids": [PID.SKIPPED], "note": "PHP not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}
    if PID.MISSING_FILES in ids or PID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "php_missing", "finding_ids": missing_ids, "note": "PHP required but files missing"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    tag_ok = PID.TAG_OK in ids
    tag_missing = PID.TAG_MISSING in ids
    syntax_ok = PID.SYNTAX_OK in ids
    syntax_partial = PID.SYNTAX_SUSPECT in ids or PID.NO_CODE in ids
    evidence_entries = [f for f in findings if f.id == PID.EVIDENCE]
    evidence_totals = {"echo_usage": 0, "request_usage": 0, "db_usage": 0}
    for entry in evidence_entries:
        for key in list(evidence_totals.keys()):
            evidence_totals[key] += int(entry.evidence.get(key, 0))

    has_usage = any(value > 0 for value in evidence_totals.values())

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "php")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.5

    if tag_ok and (syntax_ok or has_usage):
        rationale.append(
            {
                "rule": "php_tag_and_syntax_or_usage",
                "finding_ids": [fid for fid in ids if fid.startswith("PHP.")],
                "evidence": evidence_totals,
            }
        )
        base_score = 1.0
    elif tag_missing or syntax_partial or findings:
        rationale.append(
            {
                "rule": "php_partial",
                "finding_ids": [fid for fid in ids],
                "evidence": evidence_totals,
            }
        )
        base_score = 0.5
    else:
        rationale.append({"rule": "php_default_partial", "finding_ids": [fid for fid in ids]})
        base_score = 0.5

    if weighted_rule_score > 0:
        base_score = weighted_rule_score

    quality_penalty = calculate_quality_penalty(findings, "php")
    if quality_penalty > 0:
        base_score = max(0.0, base_score - quality_penalty)
        security_findings = [f.id for f in findings if "SECURITY" in f.id and f.severity == Severity.FAIL]
        quality_findings = [f.id for f in findings if "QUALITY" in f.id]
        rationale.append({
            "rule": "php_quality_security_penalty",
            "finding_ids": security_findings + quality_findings,
            "note": f"Code quality and security issues reduced score by {quality_penalty:.2f}",
        })

    beh_view = behavioural_view(behavioural_evidence_list)
    summaries["behavioural_summary"] = beh_view
    if beh_view.get("php_skipped_env"):
        rationale.append(
            {
                "rule": "php_behavioural_skipped",
                "finding_ids": ["BEHAVIOUR.PHP_FORM_RUN_SKIPPED"],
                "note": "Behavioural PHP checks skipped",
            }
        )

    php_smoke = beh_view.get("php_smoke")
    php_form = beh_view.get("php_form")
    any_pass = php_smoke == "pass" or php_form == "pass"
    both_pass = php_smoke == "pass" and php_form == "pass"
    any_fail = php_smoke in {"fail", "timeout", "error"} or php_form in {"fail", "timeout", "error"}
    static_attempt = has_usage or tag_ok or syntax_ok or syntax_partial

    if any_pass:
        base_score = max(base_score, 0.5)
        rationale.append({"rule": "php_behavioural_pass", "finding_ids": ["BEHAVIOUR.PHP_SMOKE_PASS" if php_smoke == "pass" else "BEHAVIOUR.PHP_FORM_RUN_PASS"], "note": "Behavioural PHP test passed"})
    if both_pass and base_score >= 0.5:
        base_score = max(base_score, 1.0 if base_score >= 1.0 else 0.5)
    if any_fail and static_attempt:
        base_score = 0.5 if base_score > 0 else 0.5
        rationale.append({"rule": "php_behavioural_fail", "finding_ids": ["BEHAVIOUR.PHP_SMOKE_FAIL" if php_smoke in {'fail','timeout','error'} else "BEHAVIOUR.PHP_FORM_RUN_FAIL"], "note": "Behavioural PHP test failed"})
    if any_fail and not static_attempt:
        base_score = 0.0

    return {"score": base_score, "rationale": rationale, "summaries": summaries}


def analyse_sql(
    findings: List[Finding],
    behavioural_evidence_list: List[BehaviouralEvidence],
) -> Dict[str, object]:
    """Analyse the sql."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("sql", findings)
    ids = {f.id for f in findings}
    if SID.SKIPPED in ids:
        rationale.append({"rule": "sql_skipped", "finding_ids": [SID.SKIPPED], "note": "SQL not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}
    if SID.MISSING_FILES in ids or SID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "sql_missing", "finding_ids": missing_ids, "note": "SQL required but files missing"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    structure_ok = SID.STRUCTURE_OK in ids
    no_semicolons = SID.NO_SEMICOLONS in ids
    empty = SID.EMPTY in ids
    evidence_entries = [f for f in findings if f.id == SID.EVIDENCE]
    evidence_totals = {"create_table": 0, "insert_into": 0, "select": 0}
    for entry in evidence_entries:
        for key in list(evidence_totals.keys()):
            evidence_totals[key] += int(entry.evidence.get(key, 0))

    has_activity = any(value > 0 for value in evidence_totals.values())

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "sql")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.5

    if structure_ok and has_activity:
        rationale.append(
            {
                "rule": "sql_structure_ok_with_activity",
                "finding_ids": [SID.STRUCTURE_OK, SID.EVIDENCE],
                "evidence": evidence_totals,
            }
        )
        base_score = 1.0
    elif no_semicolons or empty or not has_activity:
        rationale.append(
            {
                "rule": "sql_partial_or_empty",
                "finding_ids": [fid for fid in ids],
                "evidence": evidence_totals,
            }
        )
        base_score = 0.5
    else:
        rationale.append({"rule": "sql_default_partial", "finding_ids": [fid for fid in ids]})
        base_score = 0.5

    if weighted_rule_score > 0:
        base_score = weighted_rule_score

    quality_penalty = calculate_quality_penalty(findings, "sql")
    if quality_penalty > 0:
        base_score = max(0.0, base_score - quality_penalty)
        security_findings = [f.id for f in findings if "SECURITY" in f.id]
        quality_findings = [f.id for f in findings if "QUALITY" in f.id]
        rationale.append({
            "rule": "sql_quality_security_penalty",
            "finding_ids": security_findings + quality_findings,
            "note": f"Code quality and security issues reduced score by {quality_penalty:.2f}",
        })

    beh_view = behavioural_view(behavioural_evidence_list)
    summaries["behavioural_summary"] = beh_view
    sql_exec = beh_view.get("sql_exec")
    if sql_exec == "pass":
        base_score = max(base_score, 0.5)
        rationale.append({"rule": "sql_behavioural_pass", "finding_ids": ["BEHAVIOUR.SQL_EXEC_PASS"], "note": "SQL execution passed"})
        if base_score >= 1.0:
            base_score = 1.0
    elif sql_exec in {"fail", "timeout", "error"} and has_activity:
        base_score = 0.5
        rationale.append({"rule": "sql_behavioural_fail", "finding_ids": ["BEHAVIOUR.SQL_EXEC_FAIL"], "note": "SQL execution failed"})
    elif sql_exec in {"fail", "timeout", "error"} and not has_activity:
        base_score = 0.0
    elif sql_exec == "skipped":
        base_score = min(base_score, 0.5)
        rationale.append({"rule": "sql_behavioural_skipped", "finding_ids": ["BEHAVIOUR.SQL_EXEC_SKIPPED"], "note": "SQL behavioural checks skipped"})

    return {"score": base_score, "rationale": rationale, "summaries": summaries}


def analyse_api(
    findings: List[Finding],
    behavioural_evidence_list: List[BehaviouralEvidence],
) -> Dict[str, object]:
    """Analyse the api."""
    rationale: List[dict] = []
    summaries: Dict[str, object] = {}
    summaries["static_summary"] = static_summary("api", findings)
    ids = {f.id for f in findings}

    if AID.SKIPPED in ids:
        rationale.append({"rule": "api_skipped", "finding_ids": [AID.SKIPPED], "note": "API not required for this profile"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    if AID.MISSING_FILES in ids or AID.REQ_MISSING_FILES in ids:
        missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
        rationale.append({"rule": "api_missing", "finding_ids": missing_ids, "note": "API required but no server-side or client-side files found"})
        return {"score": 0.0, "rationale": rationale, "summaries": summaries}

    evidence_findings = [f for f in findings if f.id == "API.EVIDENCE"]
    php_api_evidence = [ev for ev in evidence_findings if ev.evidence.get("file_type") == "php" and ev.evidence.get("is_api_endpoint")]
    js_api_evidence = [ev for ev in evidence_findings if ev.evidence.get("file_type") == "js" and ev.evidence.get("has_api_patterns")]
    has_server_endpoint = len(php_api_evidence) > 0
    has_client_calls = len(js_api_evidence) > 0
    has_static_evidence = has_server_endpoint or has_client_calls

    weighted_rule_score, rule_details = calculate_weighted_rule_score(findings, "api")
    if rule_details:
        rationale.extend(rule_details)
        summaries["required_rules_weighted_score"] = weighted_rule_score

    base_score = 0.0

    if has_static_evidence:
        base_score = 0.5
        rationale.append({
            "rule": "api_static_evidence",
            "finding_ids": [f.id for f in evidence_findings],
            "evidence": {
                "server_endpoint_detected": has_server_endpoint,
                "client_calls_detected": has_client_calls,
                "evidence_file_count": len(evidence_findings),
            },
        })

    if weighted_rule_score > 0:
        base_score = max(base_score, weighted_rule_score)

    beh_view = behavioural_view(behavioural_evidence_list)
    summaries["behavioural_summary"] = beh_view
    api_exec = beh_view.get("api_exec")

    if api_exec == "pass":
        base_score = max(base_score, 1.0) if has_static_evidence else max(base_score, 0.5)
        rationale.append({"rule": "api_exec_pass", "finding_ids": ["BEHAVIOUR.API_EXEC_PASS"], "note": "API endpoint executed and returned valid JSON"})
    elif api_exec in {"fail", "timeout", "error"}:
        if has_static_evidence:
            base_score = min(base_score, 0.5)
        else:
            base_score = 0.0
        rationale.append({"rule": "api_exec_fail", "finding_ids": ["BEHAVIOUR.API_EXEC_FAIL"], "note": "API endpoint execution failed or returned invalid response"})
    elif api_exec == "skipped":
        rationale.append({"rule": "api_exec_skipped", "finding_ids": ["BEHAVIOUR.API_EXEC_SKIPPED"], "note": "API behavioural test skipped (no endpoint or php unavailable)"})

    if not rationale:
        rationale.append({"rule": "api_no_evidence", "finding_ids": [], "note": "No API evidence detected"})

    return {"score": base_score, "rationale": rationale, "summaries": summaries}
