from __future__ import annotations

from datetime import datetime, timezone
import platform
import sys
from typing import Dict, Iterable, List, Mapping, Tuple

from ams.core.models import BrowserEvidence, BehaviouralEvidence, Finding, ScoreEvidenceBundle, Severity
from ams.core.profiles import get_relevant_components


class ScoringEngine:
    """Deterministic scoring engine producing explainable scores."""

    COMPONENTS = ["html", "css", "js", "php", "sql"]

    def score(
        self,
        findings: Iterable[Finding],
        profile: str = None,
        behavioural_evidence: Iterable[BehaviouralEvidence] | None = None,
        browser_evidence: Iterable[BrowserEvidence] | None = None,
    ) -> Mapping[str, object]:
        scores, _ = self.score_with_evidence(
            findings,
            profile=profile,
            behavioural_evidence=behavioural_evidence,
            browser_evidence=browser_evidence,
        )
        return scores

    def score_with_evidence(
        self,
        findings: Iterable[Finding],
        profile: str = None,
        behavioural_evidence: Iterable[BehaviouralEvidence] | None = None,
        browser_evidence: Iterable[BrowserEvidence] | None = None,
    ) -> Tuple[Mapping[str, object], ScoreEvidenceBundle]:
        findings_list = list(findings)
        behavioural_evidence_list = list(behavioural_evidence or [])
        browser_evidence_list = list(browser_evidence or [])
        by_category: Dict[str, List[Finding]] = {c: [] for c in self.COMPONENTS}
        for finding in findings_list:
            if finding.category in by_category:
                by_category[finding.category].append(finding)
        relevant_components = self._determine_relevant_components(profile)

        component_results: Dict[str, dict] = {}
        for component in self.COMPONENTS:
            if component not in relevant_components:
                component_results[component] = {
                    "score": "SKIPPED",
                    "rationale": [
                        {"rule": "component_skipped_profile", "finding_ids": [], "note": f"Component not required for profile [{profile}]"}
                    ],
                }
                continue
            score_value, rationale, summaries = self._score_component(
                component,
                by_category.get(component, []),
                behavioural_evidence_list,
                browser_evidence_list,
            )
            component_results[component] = {
                "score": score_value,
                "rationale": sorted(rationale, key=lambda r: r.get("rule", "")),
                **summaries,
            }

        n_relevant = len([c for c in self.COMPONENTS if c in relevant_components])
        total = sum(
            (result["score"] if isinstance(result["score"], (float, int)) else 0.0)
            for c, result in component_results.items() if c in relevant_components
        )
        overall_raw = total / n_relevant if n_relevant > 0 else 0.0
        overall_final, overall_rationale = self._quantize_overall(
            component_results, relevant_components, overall_raw
        )
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        scores = {
            "overall": overall_final,
            "by_component": component_results,
            "generated_at": generated_at,
        }

        components_evidence: Dict[str, Mapping[str, object]] = {}
        for component in self.COMPONENTS:
            component_findings = by_category.get(component, [])
            finding_ids = sorted({f.id for f in component_findings})
            components_evidence[component] = {
                "score": component_results[component]["score"],
                "required": component in relevant_components,
                "finding_ids": finding_ids,
                "rationale": component_results[component].get("rationale", []),
                "static_summary": component_results[component].get("static_summary"),
                "behavioural_summary": component_results[component].get("behavioural_summary"),
                "browser_summary": component_results[component].get("browser_summary"),
            }

        evidence = ScoreEvidenceBundle(
            profile=profile or "default",
            generated_at=generated_at,
            environment={
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
            },
            components=components_evidence,
            overall={
                "raw_average": overall_raw,
                "final": overall_final,
                "decision": "full" if overall_final == 1.0 else "partial" if overall_final == 0.5 else "none",
                "required_components": list(relevant_components),
                "rationale": overall_rationale,
            },
        )
        return scores, evidence

    def _determine_relevant_components(self, profile: str | None) -> List[str]:
        if profile is None:
            return self.COMPONENTS
        return get_relevant_components(profile)

    def _quantize_overall(
        self,
        component_results: Mapping[str, Mapping[str, object]],
        relevant_components: List[str],
        overall_raw: float,
    ) -> Tuple[float, List[str]]:
        numeric_scores: List[float] = []
        for comp in relevant_components:
            score = component_results.get(comp, {}).get("score")
            if isinstance(score, (float, int)):
                numeric_scores.append(float(score))
        
        all_required_full = bool(numeric_scores) and all(score == 1.0 for score in numeric_scores)
        no_attempt = not any(score > 0 for score in numeric_scores)
        
        rationale: List[str] = [f"Raw overall average: {overall_raw:.2f}."]
        
        if all_required_full:
            rationale.append("All required components scored 1.0.")
            return 1.0, rationale
        
        if no_attempt:
            rationale.append("No meaningful attempt evidence detected in required components.")
            return 0.0, rationale
        
        # Use the raw average rounded to 2 decimal places
        # This gives more granular and accurate overall scores
        final_score = round(overall_raw, 2)
        
        not_full = [comp for comp in relevant_components if component_results.get(comp, {}).get("score") != 1.0]
        if not_full:
            rationale.append(f"Components below full: {', '.join(sorted(not_full))}.")
        
        return final_score, rationale

    def _score_component(
        self,
        component: str,
        findings: List[Finding],
        behavioural_evidence: List[BehaviouralEvidence],
        browser_evidence: List[BrowserEvidence],
    ) -> Tuple[float, List[dict], Dict[str, object]]:
        dispatcher = {
            "html": lambda f: self._score_html(f, browser_evidence),
            "css": lambda f: self._score_css(f),
            "js": lambda f: self._score_js(f, browser_evidence),
            "php": lambda f: self._score_php(f, behavioural_evidence),
            "sql": lambda f: self._score_sql(f, behavioural_evidence),
        }
        scorer = dispatcher.get(component)
        if scorer is None:
            return 0.0, [], {}
        score, rationale, summaries = scorer(findings)
        return score, rationale, summaries

    def _calculate_weighted_rule_score(self, findings: List[Finding], component: str) -> Tuple[float, List[dict]]:
        """Calculate weighted score from required rule findings (HTML.REQ.PASS/FAIL, etc.)."""
        req_pass_findings = [f for f in findings if f.id.endswith(".REQ.PASS")]
        req_fail_findings = [f for f in findings if f.id.endswith(".REQ.FAIL")]
        
        if not req_pass_findings and not req_fail_findings:
            return 0.0, []  # No required rule findings
        
        total_weight = 0.0
        passed_weight = 0.0
        rule_details = []
        
        # Process pass findings
        for finding in req_pass_findings:
            weight = float(finding.evidence.get("weight", 1.0))
            rule_id = finding.evidence.get("rule_id", "unknown")
            total_weight += weight
            passed_weight += weight
            rule_details.append({
                "rule": rule_id,
                "status": "pass",
                "weight": weight,
                "finding_ids": [finding.id],
            })
        
        # Process fail findings
        for finding in req_fail_findings:
            weight = float(finding.evidence.get("weight", 1.0))
            rule_id = finding.evidence.get("rule_id", "unknown")
            total_weight += weight
            rule_details.append({
                "rule": rule_id,
                "status": "fail",
                "weight": weight,
                "finding_ids": [finding.id],
            })
        
        if total_weight == 0:
            return 0.0, []
        
        weighted_score = passed_weight / total_weight
        return weighted_score, rule_details

    def _score_html(self, findings: List[Finding], browser_evidence: List[BrowserEvidence]) -> Tuple[float, List[dict], Dict[str, object]]:
        rationale: List[dict] = []
        summaries: Dict[str, object] = {}
        summaries["static_summary"] = self._static_summary("html", findings)
        ids = {f.id for f in findings}
        if "HTML.SKIPPED" in ids:
            rationale.append({"rule": "html_skipped", "finding_ids": ["HTML.SKIPPED"], "note": "HTML not required for this profile"})
            return 0.0, rationale, summaries
        if "HTML.MISSING_FILES" in ids or "HTML.REQ.MISSING_FILES" in ids:
            missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
            rationale.append({"rule": "html_missing", "finding_ids": missing_ids, "note": "HTML required but files missing"})
            return 0.0, rationale, summaries

        parse_ok = "HTML.PARSE_OK" in ids
        parse_suspect = "HTML.PARSE_SUSPECT" in ids
        evidence_findings = [f for f in findings if f.id == "HTML.ELEMENT_EVIDENCE"]

        browser_view = self._browser_view(browser_evidence)
        summaries["browser_summary"] = browser_view

        # Calculate weighted rule score
        weighted_rule_score, rule_details = self._calculate_weighted_rule_score(findings, "html")
        if rule_details:
            rationale.extend(rule_details)
            summaries["required_rules_weighted_score"] = weighted_rule_score

        base_score = 0.5
        if parse_ok:
            rationale.append(
                {
                    "rule": "html_structure_ok",
                    "finding_ids": ["HTML.PARSE_OK"],
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

        # Use 100% required rules weight - rules now include structure checks
        if weighted_rule_score > 0:
            base_score = weighted_rule_score

        # Apply code quality penalties
        quality_penalty = self._calculate_quality_penalty(findings, "html")
        if quality_penalty > 0:
            base_score = max(0.0, base_score - quality_penalty)
            rationale.append({
                "rule": "html_quality_penalty",
                "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
                "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
            })

        if browser_view:
            if browser_view.get("page_status") in {"fail", "timeout"}:
                adjusted = 0.5 if base_score >= 0.5 else 0.0
                rationale.append(
                    {
                        "rule": "browser_page_issue",
                        "finding_ids": ["BROWSER.PAGE_LOAD_FAIL" if browser_view.get("page_status") == "fail" else "BROWSER.PAGE_LOAD_TIMEOUT"],
                        "note": "Browser page load did not complete",
                    }
                )
                base_score = min(base_score, adjusted)
            elif browser_view.get("page_status") == "pass" and base_score >= 0.5:
                rationale.append(
                    {
                        "rule": "browser_page_pass",
                        "finding_ids": ["BROWSER.PAGE_LOAD_PASS"],
                        "note": "Browser page load succeeded",
                    }
                )

        return base_score, rationale, summaries

    def _score_css(self, findings: List[Finding]) -> Tuple[float, List[dict], Dict[str, object]]:
        rationale: List[dict] = []
        summaries: Dict[str, object] = {}
        summaries["static_summary"] = self._static_summary("css", findings)
        ids = {f.id for f in findings}
        if "CSS.SKIPPED" in ids:
            rationale.append({"rule": "css_skipped", "finding_ids": ["CSS.SKIPPED"], "note": "CSS not required for this profile"})
            return 0.0, rationale, summaries  # SKIPPED components don't contribute to score
        if "CSS.MISSING_FILES" in ids or "CSS.REQ.MISSING_FILES" in ids:
            missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
            rationale.append({"rule": "css_missing", "finding_ids": missing_ids, "note": "CSS required but files missing"})
            return 0.0, rationale, summaries

        balanced = "CSS.BRACES_BALANCED" in ids
        unbalanced = "CSS.BRACES_UNBALANCED" in ids
        no_rules = "CSS.NO_RULES" in ids
        selectors_approx = sum(int(f.evidence.get("selectors_approx", 0)) for f in findings if f.id == "CSS.EVIDENCE")

        # Calculate weighted rule score
        weighted_rule_score, rule_details = self._calculate_weighted_rule_score(findings, "css")
        if rule_details:
            rationale.extend(rule_details)
            summaries["required_rules_weighted_score"] = weighted_rule_score

        base_score = 0.5
        if balanced and selectors_approx >= 1:
            rationale.append(
                {
                    "rule": "css_balanced_with_selectors",
                    "finding_ids": ["CSS.BRACES_BALANCED", "CSS.EVIDENCE"],
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

        # Use 100% required rules weight - rules now include structure checks
        if weighted_rule_score > 0:
            base_score = weighted_rule_score

        # Apply code quality penalties
        quality_penalty = self._calculate_quality_penalty(findings, "css")
        if quality_penalty > 0:
            base_score = max(0.0, base_score - quality_penalty)
            rationale.append({
                "rule": "css_quality_penalty",
                "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
                "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
            })

        return base_score, rationale, summaries

    def _score_js(self, findings: List[Finding], browser_evidence: List[BrowserEvidence]) -> Tuple[float, List[dict], Dict[str, object]]:
        rationale: List[dict] = []
        summaries: Dict[str, object] = {}
        summaries["static_summary"] = self._static_summary("js", findings)
        ids = {f.id for f in findings}
        if "JS.SKIPPED" in ids:
            rationale.append({"rule": "js_skipped", "finding_ids": ["JS.SKIPPED"], "note": "JS not required for this profile"})
            return 0.0, rationale, summaries  # SKIPPED components don't contribute to score
        if "JS.MISSING_FILES" in ids or "JS.REQ.MISSING_FILES" in ids:
            missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
            rationale.append({"rule": "js_missing", "finding_ids": missing_ids, "note": "JS required but files missing"})
            return 0.0, rationale, summaries

        syntax_ok = "JS.SYNTAX_OK" in ids
        syntax_suspect = "JS.SYNTAX_SUSPECT" in ids or "JS.NO_CODE" in ids
        evidence_entries = [f for f in findings if f.id == "JS.EVIDENCE"]
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
        browser_view = self._browser_view(browser_evidence)
        summaries["browser_summary"] = browser_view
        
        # Calculate weighted rule score
        weighted_rule_score, rule_details = self._calculate_weighted_rule_score(findings, "js")
        if rule_details:
            rationale.extend(rule_details)
            summaries["required_rules_weighted_score"] = weighted_rule_score
        
        base_score = 0.5

        if syntax_ok and has_activity:
            rationale.append(
                {
                    "rule": "js_syntax_ok_with_activity",
                    "finding_ids": ["JS.SYNTAX_OK", "JS.EVIDENCE"],
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

        # Use 100% required rules weight - rules now include structure checks
        if weighted_rule_score > 0:
            base_score = weighted_rule_score

        # Apply code quality penalties
        quality_penalty = self._calculate_quality_penalty(findings, "js")
        if quality_penalty > 0:
            base_score = max(0.0, base_score - quality_penalty)
            rationale.append({
                "rule": "js_quality_penalty",
                "finding_ids": [f.id for f in findings if "QUALITY" in f.id],
                "note": f"Code quality issues reduced score by {quality_penalty:.2f}",
            })

        # Browser adjustments
        if browser_view:
            console_errors = browser_view.get("console_errors", 0)
            page_status = browser_view.get("page_status")
            interaction_status = browser_view.get("interaction_status")
            interacted = browser_view.get("interacted")

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
        
        # Functional test adjustments
        functional_test_score = self._calculate_functional_test_score(findings, browser_evidence)
        if functional_test_score is not None:
            # Blend functional test results: 70% base score, 30% functional tests
            base_score = 0.7 * base_score + 0.3 * functional_test_score
            rationale.append({
                "rule": "functional_tests_score",
                "finding_ids": [f.id for f in findings if "BROWSER.FUNCTIONAL" in f.id],
                "note": f"Functional tests contributed {functional_test_score:.2f} to score",
            })
        
        # Performance and error penalties
        performance_penalty = self._calculate_performance_penalty(findings)
        error_penalty = self._calculate_error_penalty(findings)
        
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
        
        # Missing required features penalty
        missing_features = [f for f in findings if "REQUIRED_FEATURES_MISSING" in f.id]
        if missing_features:
            base_score = max(0.0, base_score - 0.3)  # 30% penalty for missing required features
            rationale.append({
                "rule": "missing_required_features",
                "finding_ids": [f.id for f in missing_features],
                "note": "Missing required features reduced score by 0.30",
            })

        return base_score, rationale, summaries

    def _score_php(self, findings: List[Finding], behavioural_evidence: List[BehaviouralEvidence]) -> Tuple[float, List[dict], Dict[str, object]]:
        rationale: List[dict] = []
        summaries: Dict[str, object] = {}
        summaries["static_summary"] = self._static_summary("php", findings)
        ids = {f.id for f in findings}
        if "PHP.SKIPPED" in ids:
            rationale.append({"rule": "php_skipped", "finding_ids": ["PHP.SKIPPED"], "note": "PHP not required for this profile"})
            return 0.0, rationale, summaries  # SKIPPED components don't contribute to score
        if "PHP.MISSING_FILES" in ids or "PHP.REQ.MISSING_FILES" in ids:
            missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
            rationale.append({"rule": "php_missing", "finding_ids": missing_ids, "note": "PHP required but files missing"})
            return 0.0, rationale, summaries

        tag_ok = "PHP.TAG_OK" in ids
        tag_missing = "PHP.TAG_MISSING" in ids
        syntax_ok = "PHP.SYNTAX_OK" in ids
        syntax_partial = "PHP.SYNTAX_SUSPECT" in ids or "PHP.NO_CODE" in ids
        evidence_entries = [f for f in findings if f.id == "PHP.EVIDENCE"]
        evidence_totals = {"echo_usage": 0, "request_usage": 0, "db_usage": 0}
        for entry in evidence_entries:
            for key in list(evidence_totals.keys()):
                evidence_totals[key] += int(entry.evidence.get(key, 0))

        has_usage = any(value > 0 for value in evidence_totals.values())
        
        # Calculate weighted rule score
        weighted_rule_score, rule_details = self._calculate_weighted_rule_score(findings, "php")
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

        # Use 100% required rules weight - rules now include structure checks
        if weighted_rule_score > 0:
            base_score = weighted_rule_score

        # Apply code quality and security penalties (security issues are critical for PHP)
        quality_penalty = self._calculate_quality_penalty(findings, "php")
        if quality_penalty > 0:
            base_score = max(0.0, base_score - quality_penalty)
            security_findings = [f.id for f in findings if "SECURITY" in f.id and f.severity == Severity.FAIL]
            quality_findings = [f.id for f in findings if "QUALITY" in f.id]
            rationale.append({
                "rule": "php_quality_security_penalty",
                "finding_ids": security_findings + quality_findings,
                "note": f"Code quality and security issues reduced score by {quality_penalty:.2f}",
            })

        behavioural_view = self._behavioural_view(behavioural_evidence)
        summaries["behavioural_summary"] = behavioural_view
        if behavioural_view.get("php_skipped_env"):
            rationale.append(
                {
                    "rule": "php_behavioural_skipped",
                    "finding_ids": ["BEHAVIOUR.PHP_FORM_RUN_SKIPPED"],
                    "note": "Behavioural PHP checks skipped",
                }
            )

        php_smoke = behavioural_view.get("php_smoke")
        php_form = behavioural_view.get("php_form")
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

        return base_score, rationale, summaries

    def _score_sql(self, findings: List[Finding], behavioural_evidence: List[BehaviouralEvidence]) -> Tuple[float, List[dict], Dict[str, object]]:
        rationale: List[dict] = []
        summaries: Dict[str, object] = {}
        summaries["static_summary"] = self._static_summary("sql", findings)
        ids = {f.id for f in findings}
        if "SQL.SKIPPED" in ids:
            rationale.append({"rule": "sql_skipped", "finding_ids": ["SQL.SKIPPED"], "note": "SQL not required for this profile"})
            return 0.0, rationale, summaries  # SKIPPED components don't contribute to score
        if "SQL.MISSING_FILES" in ids or "SQL.REQ.MISSING_FILES" in ids:
            missing_ids = [fid for fid in ids if "MISSING_FILES" in fid]
            rationale.append({"rule": "sql_missing", "finding_ids": missing_ids, "note": "SQL required but files missing"})
            return 0.0, rationale, summaries

        structure_ok = "SQL.STRUCTURE_OK" in ids
        no_semicolons = "SQL.NO_SEMICOLONS" in ids
        empty = "SQL.EMPTY" in ids
        evidence_entries = [f for f in findings if f.id == "SQL.EVIDENCE"]
        evidence_totals = {"create_table": 0, "insert_into": 0, "select": 0}
        for entry in evidence_entries:
            for key in list(evidence_totals.keys()):
                evidence_totals[key] += int(entry.evidence.get(key, 0))

        has_activity = any(value > 0 for value in evidence_totals.values())
        
        # Calculate weighted rule score
        weighted_rule_score, rule_details = self._calculate_weighted_rule_score(findings, "sql")
        if rule_details:
            rationale.extend(rule_details)
            summaries["required_rules_weighted_score"] = weighted_rule_score
        
        base_score = 0.5

        if structure_ok and has_activity:
            rationale.append(
                {
                    "rule": "sql_structure_ok_with_activity",
                    "finding_ids": ["SQL.STRUCTURE_OK", "SQL.EVIDENCE"],
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

        # Use 100% required rules weight - rules now include structure checks
        if weighted_rule_score > 0:
            base_score = weighted_rule_score

        # Apply code quality and security penalties
        quality_penalty = self._calculate_quality_penalty(findings, "sql")
        if quality_penalty > 0:
            base_score = max(0.0, base_score - quality_penalty)
            security_findings = [f.id for f in findings if "SECURITY" in f.id]
            quality_findings = [f.id for f in findings if "QUALITY" in f.id]
            rationale.append({
                "rule": "sql_quality_security_penalty",
                "finding_ids": security_findings + quality_findings,
                "note": f"Code quality and security issues reduced score by {quality_penalty:.2f}",
            })

        behavioural_view = self._behavioural_view(behavioural_evidence)
        summaries["behavioural_summary"] = behavioural_view
        sql_exec = behavioural_view.get("sql_exec")
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

        return base_score, rationale, summaries

    def _static_summary(self, component: str, findings: List[Finding]) -> Dict[str, object]:
        ids = [f.id for f in findings]
        return {
            "component": component,
            "missing": any("MISSING_FILES" in fid for fid in ids),
            "skipped": any("SKIPPED" in fid for fid in ids),
            "finding_ids": ids,
        }

    def _behavioural_view(self, evidence: List[BehaviouralEvidence]) -> Dict[str, object]:
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
            if status == "skipped" and getattr(ev, "component", "") == "php":
                view["php_skipped_env"] = True
        return view

    def _browser_view(self, evidence: List[BrowserEvidence]) -> Dict[str, object]:
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

    def _calculate_quality_penalty(self, findings: List[Finding], component: str) -> float:
        """Calculate penalty for code quality and security issues."""
        penalty = 0.0
        quality_findings = [f for f in findings if "QUALITY" in f.id or "SECURITY" in f.id]
        
        for finding in quality_findings:
            severity = finding.severity
            # FAIL severity (security issues) = 0.2 penalty
            # WARN severity (quality issues) = 0.1 penalty
            if severity == Severity.FAIL:
                penalty += 0.2
            elif severity == Severity.WARN:
                penalty += 0.1
        
        # Cap penalty at 0.5 (50% reduction)
        return min(0.5, penalty)

    def _calculate_functional_test_score(
        self, 
        findings: List[Finding], 
        browser_evidence: List[BrowserEvidence]
    ) -> float | None:
        """Calculate score based on functional test results."""
        functional_findings = [f for f in findings if "BROWSER.FUNCTIONAL" in f.id and not f.id.endswith(".SUMMARY")]
        
        if not functional_findings:
            return None
        
        passed = sum(1 for f in functional_findings if f.severity == Severity.INFO and "passed" in f.message.lower())
        failed = sum(1 for f in functional_findings if f.severity == Severity.WARN and "failed" in f.message.lower())
        total = len(functional_findings)
        
        if total == 0:
            return None
        
        # Score is percentage of passed tests
        score = passed / total if total > 0 else 0.0
        return score
    
    def _calculate_performance_penalty(self, findings: List[Finding]) -> float:
        """Calculate penalty for performance issues."""
        penalty = 0.0
        performance_findings = [f for f in findings if "BROWSER.PERFORMANCE" in f.id and f.severity == Severity.FAIL]
        
        for finding in performance_findings:
            # Each failed performance check = 0.1 penalty
            penalty += 0.1
        
        # Cap penalty at 0.3 (30% reduction)
        return min(0.3, penalty)
    
    def _calculate_error_penalty(self, findings: List[Finding]) -> float:
        """Calculate penalty for runtime errors."""
        penalty = 0.0
        error_findings = [f for f in findings if "BROWSER.ERROR" in f.id]
        
        for finding in error_findings:
            if finding.severity == Severity.FAIL:
                # Critical errors = 0.15 penalty each
                penalty += 0.15
            elif finding.severity == Severity.WARN:
                # Warnings = 0.05 penalty each
                penalty += 0.05
        
        # Cap penalty at 0.4 (40% reduction)
        return min(0.4, penalty)


__all__ = ["ScoringEngine"]
