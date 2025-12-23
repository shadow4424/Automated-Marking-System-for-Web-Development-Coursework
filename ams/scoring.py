from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Tuple

from .models import Finding


class ScoringEngine:
    """Deterministic scoring engine producing explainable scores."""

    COMPONENTS = ["html", "css", "js", "php", "sql"]

    def score(self, findings: Iterable[Finding], profile: str = None) -> Mapping[str, object]:
        from .profiles import PROFILES
        findings_list = list(findings)
        by_category: Dict[str, List[Finding]] = {c: [] for c in self.COMPONENTS}
        for finding in findings_list:
            if finding.category in by_category:
                by_category[finding.category].append(finding)
        # Determine relevant components per profile
        relevant_components = self.COMPONENTS
        if profile is not None and profile in PROFILES:
            relevant_components = PROFILES[profile]["relevant_artefacts"]
        component_results: Dict[str, dict] = {}
        for component in self.COMPONENTS:
            if component not in relevant_components:
                component_results[component] = {
                    "score": "SKIPPED",
                    "rationale": [
                        {"rule": "component_skipped_profile", "finding_ids": [], "note": f"Skipped by profile [{profile}]"}
                    ],
                }
                continue
            score_value, rationale = self._score_component(component, by_category.get(component, []))
            component_results[component] = {
                "score": score_value,
                "rationale": sorted(rationale, key=lambda r: r.get("rule", "")),
            }
        # Calculate denominator and overall
        n_relevant = len([c for c in self.COMPONENTS if c in relevant_components])
        total = sum(
            (result["score"] if isinstance(result["score"], (float, int)) else 0.0)
            for c, result in component_results.items() if c in relevant_components
        )
        overall = total / n_relevant if n_relevant > 0 else 0.0
        return {
            "overall": overall,
            "by_component": component_results,
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def _score_component(self, component: str, findings: List[Finding]) -> Tuple[float, List[dict]]:
        dispatcher = {
            "html": self._score_html,
            "css": self._score_css,
            "js": self._score_js,
            "php": self._score_php,
            "sql": self._score_sql,
        }
        scorer = dispatcher.get(component)
        if scorer is None:
            return 0.0, []
        return scorer(findings)

    def _score_html(self, findings: List[Finding]) -> Tuple[float, List[dict]]:
        rationale: List[dict] = []
        ids = {f.id for f in findings}
        if "HTML.MISSING" in ids:
            rationale.append({"rule": "html_missing", "finding_ids": ["HTML.MISSING"]})
            return 0.0, rationale

        parse_ok = "HTML.PARSE_OK" in ids
        parse_suspect = "HTML.PARSE_SUSPECT" in ids
        evidence_findings = [f for f in findings if f.id == "HTML.ELEMENT_EVIDENCE"]

        if parse_ok:
            rationale.append(
                {
                    "rule": "html_structure_ok",
                    "finding_ids": ["HTML.PARSE_OK"],
                    "evidence": evidence_findings[0].evidence if evidence_findings else {},
                }
            )
            return 1.0, rationale

        if parse_suspect or evidence_findings:
            rationale.append(
                {
                    "rule": "html_structure_suspect_or_minimal",
                    "finding_ids": [fid for fid in ids],
                    "evidence": evidence_findings[0].evidence if evidence_findings else {},
                }
            )
            return 0.5, rationale

        rationale.append({"rule": "html_default_partial", "finding_ids": [fid for fid in ids]})
        return 0.5, rationale

    def _score_css(self, findings: List[Finding]) -> Tuple[float, List[dict]]:
        rationale: List[dict] = []
        ids = {f.id for f in findings}
        if "CSS.MISSING" in ids:
            rationale.append({"rule": "css_missing", "finding_ids": ["CSS.MISSING"]})
            return 0.0, rationale

        balanced = "CSS.BRACES_BALANCED" in ids
        unbalanced = "CSS.BRACES_UNBALANCED" in ids
        no_rules = "CSS.NO_RULES" in ids
        selectors_approx = sum(int(f.evidence.get("selectors_approx", 0)) for f in findings if f.id == "CSS.EVIDENCE")

        if balanced and selectors_approx >= 1:
            rationale.append(
                {
                    "rule": "css_balanced_with_selectors",
                    "finding_ids": ["CSS.BRACES_BALANCED", "CSS.EVIDENCE"],
                    "evidence": {"selectors_approx": selectors_approx},
                }
            )
            return 1.0, rationale

        if unbalanced or no_rules or selectors_approx == 0:
            rationale.append(
                {
                    "rule": "css_partial_or_suspect",
                    "finding_ids": [fid for fid in ids],
                    "evidence": {"selectors_approx": selectors_approx},
                }
            )
            return 0.5, rationale

        rationale.append({"rule": "css_default_partial", "finding_ids": [fid for fid in ids]})
        return 0.5, rationale

    def _score_js(self, findings: List[Finding]) -> Tuple[float, List[dict]]:
        rationale: List[dict] = []
        ids = {f.id for f in findings}
        if "JS.MISSING" in ids:
            rationale.append({"rule": "js_missing", "finding_ids": ["JS.MISSING"]})
            return 0.0, rationale

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

        if syntax_ok and has_activity:
            rationale.append(
                {
                    "rule": "js_syntax_ok_with_activity",
                    "finding_ids": ["JS.SYNTAX_OK", "JS.EVIDENCE"],
                    "evidence": evidence_totals,
                }
            )
            return 1.0, rationale

        if syntax_suspect or not has_activity:
            rationale.append(
                {
                    "rule": "js_partial_or_no_activity",
                    "finding_ids": [fid for fid in ids],
                    "evidence": evidence_totals,
                }
            )
            return 0.5, rationale

        rationale.append({"rule": "js_default_partial", "finding_ids": [fid for fid in ids]})
        return 0.5, rationale

    def _score_php(self, findings: List[Finding]) -> Tuple[float, List[dict]]:
        rationale: List[dict] = []
        ids = {f.id for f in findings}
        if "PHP.MISSING" in ids:
            rationale.append({"rule": "php_missing", "finding_ids": ["PHP.MISSING"]})
            return 0.0, rationale

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

        if tag_ok and (syntax_ok or has_usage):
            rationale.append(
                {
                    "rule": "php_tag_and_syntax_or_usage",
                    "finding_ids": [fid for fid in ids if fid.startswith("PHP.")],
                    "evidence": evidence_totals,
                }
            )
            return 1.0, rationale

        if tag_missing or syntax_partial or findings:
            rationale.append(
                {
                    "rule": "php_partial",
                    "finding_ids": [fid for fid in ids],
                    "evidence": evidence_totals,
                }
            )
            return 0.5, rationale

        rationale.append({"rule": "php_default_partial", "finding_ids": [fid for fid in ids]})
        return 0.5, rationale

    def _score_sql(self, findings: List[Finding]) -> Tuple[float, List[dict]]:
        rationale: List[dict] = []
        ids = {f.id for f in findings}
        if "SQL.MISSING" in ids:
            rationale.append({"rule": "sql_missing", "finding_ids": ["SQL.MISSING"]})
            return 0.0, rationale

        structure_ok = "SQL.STRUCTURE_OK" in ids
        no_semicolons = "SQL.NO_SEMICOLONS" in ids
        empty = "SQL.EMPTY" in ids
        evidence_entries = [f for f in findings if f.id == "SQL.EVIDENCE"]
        evidence_totals = {"create_table": 0, "insert_into": 0, "select": 0}
        for entry in evidence_entries:
            for key in list(evidence_totals.keys()):
                evidence_totals[key] += int(entry.evidence.get(key, 0))

        has_activity = any(value > 0 for value in evidence_totals.values())

        if structure_ok and has_activity:
            rationale.append(
                {
                    "rule": "sql_structure_ok_with_activity",
                    "finding_ids": ["SQL.STRUCTURE_OK", "SQL.EVIDENCE"],
                    "evidence": evidence_totals,
                }
            )
            return 1.0, rationale

        if no_semicolons or empty or not has_activity:
            rationale.append(
                {
                    "rule": "sql_partial_or_empty",
                    "finding_ids": [fid for fid in ids],
                    "evidence": evidence_totals,
                }
            )
            return 0.5, rationale

        rationale.append({"rule": "sql_default_partial", "finding_ids": [fid for fid in ids]})
        return 0.5, rationale
