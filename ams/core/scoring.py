from __future__ import annotations

from datetime import datetime, timezone
import platform
import sys
from typing import Dict, Iterable, List, Mapping, Tuple

from ams.core.assignment_config import ResolvedAssignmentConfig
from ams.core.models import (
    BrowserEvidence,
    BehaviouralEvidence,
    ComponentScoreSummary,
    ConfidenceSummary,
    Finding,
    RequirementEvaluationResult,
    ReviewRecommendation,
    ScoreEvidenceBundle,
    SubmissionContext,
)
from ams.core import component_scorers as _cs
from ams.core.profiles import get_relevant_components


def _extract_component_result(report: Mapping[str, object], component_name: str) -> Dict[str, object]:
    """Extract the component result."""
    component_result = report[component_name]
    return {
        "rationale": component_result.get("rationale", []),
        "static_summary": component_result.get("static_summary"),
        "behavioural_summary": component_result.get("behavioural_summary"),
        "browser_summary": component_result.get("browser_summary"),
    }


class ScoringEngine:
    """Deterministic scoring engine producing explainable scores."""

    COMPONENTS = ["html", "css", "js", "php", "sql", "api"]

    def score(
        self,
        findings: Iterable[Finding],
        profile: str = None,
        context: SubmissionContext | None = None,
        resolved_config: ResolvedAssignmentConfig | None = None,
        behavioural_evidence: Iterable[BehaviouralEvidence] | None = None,
        browser_evidence: Iterable[BrowserEvidence] | None = None,
    ) -> Mapping[str, object]:
        """Score the submission."""
        scores, _ = self.score_with_evidence(
            findings,
            profile=profile,
            context=context,
            resolved_config=resolved_config,
            behavioural_evidence=behavioural_evidence,
            browser_evidence=browser_evidence,
        )
        return scores

    def score_with_evidence(
        self,
        findings: Iterable[Finding],
        profile: str = None,
        context: SubmissionContext | None = None,
        resolved_config: ResolvedAssignmentConfig | None = None,
        behavioural_evidence: Iterable[BehaviouralEvidence] | None = None,
        browser_evidence: Iterable[BrowserEvidence] | None = None,
    ) -> Tuple[Mapping[str, object], ScoreEvidenceBundle]:
        """Score the submission and retain evidence."""
        findings_list = list(findings)
        behavioural_evidence_list = list(behavioural_evidence or [])
        browser_evidence_list = list(browser_evidence or [])
        if context is not None and resolved_config is not None:
            return self._score_from_requirements(
                findings_list,
                context=context,
                resolved_config=resolved_config,
                behavioural_evidence=behavioural_evidence_list,
                browser_evidence=browser_evidence_list,
            )
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
                **_extract_component_result(component_results, component),
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

    def _run_static_evaluation(
        self,
        findings: List[Finding],
        *,
        context: SubmissionContext,
        resolved_config: ResolvedAssignmentConfig,
        behavioural_evidence: List[BehaviouralEvidence],
        browser_evidence: List[BrowserEvidence],
    ) -> Dict[str, object]:
        """Run the static evaluation."""
        requirement_results = list(context.requirement_results or [])
        relevant_components = list(resolved_config.required_components)
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        findings_by_category: Dict[str, List[Finding]] = {c: [] for c in self.COMPONENTS}
        for finding in findings:
            if finding.category in findings_by_category:
                findings_by_category[finding.category].append(finding)
        return {
            "behavioural_evidence": behavioural_evidence,
            "browser_evidence": browser_evidence,
            "context": context,
            "findings_by_category": findings_by_category,
            "generated_at": generated_at,
            "relevant_components": relevant_components,
            "requirement_results": requirement_results,
            "resolved_config": resolved_config,
        }

    def _enrich_with_llm_hybrid(
        self,
        static_results: Mapping[str, object],
        findings: List[Finding],
    ) -> Tuple[Mapping[str, object], ScoreEvidenceBundle]:
        """Return with llm hybrid."""
        requirement_results = list(static_results["requirement_results"])
        self._apply_llm_hybrid_to_requirement_results(requirement_results, findings)
        relevant_components = list(static_results["relevant_components"])
        generated_at = str(static_results["generated_at"])
        findings_by_category = dict(static_results["findings_by_category"])
        resolved_config = static_results["resolved_config"]
        context = static_results["context"]
        behavioural_evidence = list(static_results["behavioural_evidence"])
        browser_evidence = list(static_results["browser_evidence"])
        component_results: Dict[str, dict] = {}
        component_summaries: Dict[str, ComponentScoreSummary] = {}
        for component in self.COMPONENTS:
            component_requirements = [
                result
                for result in requirement_results
                if result.component == component and result.required
            ]
            if component not in relevant_components:
                component_results[component] = {
                    "score": "SKIPPED",
                    "rationale": [
                        {
                            "rule": "component_skipped_profile",
                            "finding_ids": [],
                            "note": f"Component not required for profile [{resolved_config.profile_name}]",
                        }
                    ],
                    "requirement_summary": {
                        "requirement_count": 0,
                        "met": 0,
                        "partial": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                }
                continue

            score_value, penalty, summary = self._score_component_from_requirements(
                component_requirements,
            )
            rationale = [
                {
                    "rule": result.requirement_id,
                    "score": result.score,
                    "status": result.status,
                    "stage": result.stage,
                    "aggregation_mode": result.aggregation_mode,
                }
                for result in component_requirements
            ]
            if penalty > 0:
                rationale.append(
                    {
                        "rule": f"{component}.quality.capped_penalty",
                        "score": max(0.0, 1.0 - penalty),
                        "status": "PARTIAL" if penalty < 0.5 else "FAIL",
                        "stage": "quality",
                        "aggregation_mode": "CAPPED_PENALTY",
                        "note": f"Penalty applied: {penalty:.2f}",
                    }
                )
            component_results[component] = {
                "score": score_value,
                "rationale": rationale,
                "requirement_summary": summary.to_dict(),
                "static_summary": _cs.static_summary(component, findings_by_category.get(component, [])),
                "behavioural_summary": _cs.behavioural_view(behavioural_evidence),
                "browser_summary": _cs.browser_view(browser_evidence),
            }
            component_summaries[component] = summary

        overall_raw = self._weighted_overall(
            component_results,
            relevant_components,
            resolved_config.component_weights,
        )
        confidence_summary = self._build_confidence_summary(requirement_results)
        review = self._build_review_recommendation(overall_raw, confidence_summary, component_summaries)
        context.confidence_summary = confidence_summary
        context.review_recommendation = review
        scores = {
            "overall": round(overall_raw, 2),
            "by_component": component_results,
            "generated_at": generated_at,
            "confidence": confidence_summary.to_dict(),
            "review": review.to_dict(),
        }

        components_evidence: Dict[str, Mapping[str, object]] = {}
        for component in self.COMPONENTS:
            component_requirements = [
                result.to_dict()
                for result in requirement_results
                if result.component == component
            ]
            components_evidence[component] = {
                "score": component_results[component]["score"],
                "required": component in relevant_components,
                "weight": float(resolved_config.component_weights.get(component, 0.0)),
                "requirements": component_requirements,
                **_extract_component_result(component_results, component),
            }

        evidence = ScoreEvidenceBundle(
            profile=resolved_config.profile_name,
            generated_at=generated_at,
            environment={
                "python_version": sys.version.split()[0],
                "platform": platform.platform(),
            },
            components=components_evidence,
            overall={
                "raw_average": overall_raw,
                "final": round(overall_raw, 2),
                "required_components": list(relevant_components),
                "component_weights": dict(resolved_config.component_weights),
                "rationale": [
                    f"Weighted overall score across required components: {overall_raw:.2f}.",
                    f"Confidence level: {confidence_summary.level}.",
                ],
            },
            requirements=[result.to_dict() for result in requirement_results],
            assignment_profile=resolved_config.to_dict(),
            role_mapping=context.role_mapping.to_dict() if context.role_mapping else {},
            confidence=confidence_summary.to_dict(),
            review=review.to_dict(),
            manifest=context.manifest.to_dict() if context.manifest else {},
            artefact_inventory=context.artefact_inventory.to_dict() if context.artefact_inventory else {},
        )
        return scores, evidence

    def _score_from_requirements(
        self,
        findings: List[Finding],
        *,
        context: SubmissionContext,
        resolved_config: ResolvedAssignmentConfig,
        behavioural_evidence: List[BehaviouralEvidence],
        browser_evidence: List[BrowserEvidence],
    ) -> Tuple[Mapping[str, object], ScoreEvidenceBundle]:
        """Score the from requirements."""
        static_results = self._run_static_evaluation(
            findings,
            context=context,
            resolved_config=resolved_config,
            behavioural_evidence=behavioural_evidence,
            browser_evidence=browser_evidence,
        )
        return self._enrich_with_llm_hybrid(static_results, findings)

    def _apply_llm_hybrid_to_requirement_results(
        self,
        requirement_results: List[RequirementEvaluationResult],
        findings: List[Finding],
    ) -> None:
        """Fold LLM hybrid partial credit into requirement results before weighted scoring."""
        hybrid_scores_by_rule: Dict[str, float] = {}
        for finding in findings:
            if not finding.id.endswith(".REQ.FAIL"):
                continue
            evidence = finding.evidence if isinstance(finding.evidence, Mapping) else {}
            rule_id = str(evidence.get("rule_id") or "").strip()
            if not rule_id:
                continue
            hybrid = evidence.get("hybrid_score")
            if not isinstance(hybrid, Mapping):
                continue
            final_score = hybrid.get("final_score")
            if not isinstance(final_score, (int, float)):
                continue
            clamped = max(0.0, min(1.0, float(final_score)))
            existing = hybrid_scores_by_rule.get(rule_id)
            if existing is None or clamped > existing:
                hybrid_scores_by_rule[rule_id] = clamped

        if not hybrid_scores_by_rule:
            return

        for result in requirement_results:
            if result.requirement_id not in hybrid_scores_by_rule:
                continue
            if result.aggregation_mode == "CAPPED_PENALTY":
                continue
            if not isinstance(result.score, (int, float)):
                continue
            if result.status not in {"FAIL", "PARTIAL"}:
                continue

            llm_score = round(hybrid_scores_by_rule[result.requirement_id], 2)
            result.score = llm_score
            result.status = "PASS" if llm_score >= 1.0 else "PARTIAL"
            evidence = dict(result.evidence or {})
            evidence["llm_adjusted"] = True
            evidence["llm_score"] = llm_score
            result.evidence = evidence

    def _score_component_from_requirements(
        self,
        requirement_results: List[RequirementEvaluationResult],
    ) -> tuple[float, float, ComponentScoreSummary]:
        """Score the component from requirements."""
        numeric_requirements = [
            result
            for result in requirement_results
            if isinstance(result.score, (int, float))
            and result.aggregation_mode != "CAPPED_PENALTY"
        ]
        penalty_requirements = [
            result
            for result in requirement_results
            if result.aggregation_mode == "CAPPED_PENALTY"
        ]
        total_weight = sum(float(result.weight or 1.0) for result in numeric_requirements)
        weighted_score = (
            sum(float(result.score) * float(result.weight or 1.0) for result in numeric_requirements) / total_weight
            if total_weight > 0
            else 0.0
        )
        penalty = max(
            (
                float(result.evidence.get("penalty", 0.0))
                for result in penalty_requirements
                if isinstance(result.evidence, Mapping)
            ),
            default=0.0,
        )
        final_score = max(0.0, round(weighted_score - penalty, 2))
        summary = ComponentScoreSummary(
            component=(
                requirement_results[0].component
                if requirement_results
                else "unknown"
            ),
            score=final_score,
            weight=total_weight,
            requirement_count=len(requirement_results),
            met=sum(1 for result in requirement_results if result.status == "PASS"),
            partial=sum(1 for result in requirement_results if result.status == "PARTIAL"),
            failed=sum(1 for result in requirement_results if result.status == "FAIL"),
            skipped=sum(1 for result in requirement_results if result.status == "SKIPPED"),
        )
        return final_score, penalty, summary

    def _weighted_overall(
        self,
        component_results: Mapping[str, Mapping[str, object]],
        relevant_components: List[str],
        component_weights: Mapping[str, float],
    ) -> float:
        """Return overall."""
        total_weight = 0.0
        total_score = 0.0
        for component in relevant_components:
            score = component_results.get(component, {}).get("score")
            if not isinstance(score, (int, float)):
                continue
            weight = float(component_weights.get(component, 0.0) or 0.0)
            total_weight += weight
            total_score += float(score) * weight
        if total_weight == 0:
            return 0.0
        return total_score / total_weight

    def _build_confidence_summary(
        self,
        requirement_results: List[RequirementEvaluationResult],
    ) -> ConfidenceSummary:
        """Build the confidence summary."""
        flags: List[str] = []
        reasons: List[str] = []
        skipped_checks: List[str] = []
        for result in requirement_results:
            for flag in result.confidence_flags:
                if flag not in flags:
                    flags.append(flag)
            if result.status == "SKIPPED" and result.stage in {"runtime", "browser", "layout"}:
                skipped_checks.append(result.requirement_id)
        if any(flag in {"runtime_failure", "browser_failure", "browser_console_errors"} for flag in flags):
            level = "low"
            reasons.append("Runtime or browser failures reduced confidence in the automated result.")
        elif skipped_checks:
            level = "medium"
            reasons.append("Some runtime, browser, or layout checks were skipped.")
        else:
            level = "high"
            reasons.append("All enabled deterministic scoring layers produced usable evidence.")
        return ConfidenceSummary(
            level=level,
            reasons=reasons,
            flags=flags,
            skipped_checks=skipped_checks,
        )

    def _build_review_recommendation(
        self,
        overall_score: float,
        confidence: ConfidenceSummary,
        component_summaries: Mapping[str, ComponentScoreSummary],
    ) -> ReviewRecommendation:
        """Build the review recommendation."""
        reasons: List[str] = []
        if confidence.level != "high":
            reasons.append("Confidence was reduced by skipped or failing runtime/browser checks.")
        if overall_score < 0.5:
            reasons.append("Overall score is below 50%.")
        if any(summary.failed > 0 for summary in component_summaries.values()):
            reasons.append("One or more required components have failing requirements.")
        return ReviewRecommendation(
            recommended=bool(reasons),
            reasons=reasons,
        )

    def _determine_relevant_components(self, profile: str | None) -> List[str]:
        """Return relevant components."""
        if profile is None:
            return self.COMPONENTS
        return get_relevant_components(profile)

    def _quantize_overall(
        self,
        component_results: Mapping[str, Mapping[str, object]],
        relevant_components: List[str],
        overall_raw: float,
    ) -> Tuple[float, List[str]]:
        """Return overall."""
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
        """Score the component by delegating to component_scorers."""
        dispatcher = {
            "html": lambda f: _cs.analyse_html(f, browser_evidence),
            "css": lambda f: _cs.analyse_css(f),
            "js": lambda f: _cs.analyse_js(f, browser_evidence),
            "php": lambda f: _cs.analyse_php(f, behavioural_evidence),
            "sql": lambda f: _cs.analyse_sql(f, behavioural_evidence),
            "api": lambda f: _cs.analyse_api(f, behavioural_evidence),
        }
        scorer = dispatcher.get(component)
        if scorer is None:
            return 0.0, [], {}
        result = scorer(findings)
        return float(result["score"]), list(result["rationale"]), dict(result["summaries"])


__all__ = ["ScoringEngine"]
