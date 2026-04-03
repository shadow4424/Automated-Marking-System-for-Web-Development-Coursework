"""LLM hybrid scoring integration, extracted from ScoringEngine."""
from __future__ import annotations

import platform
import sys
from typing import TYPE_CHECKING, Dict, List, Mapping, Tuple

from ams.core.models import (
    ComponentScoreSummary,
    Finding,
    RequirementEvaluationResult,
    ScoreEvidenceBundle,
)
from ams.core import component_scorers as _cs

if TYPE_CHECKING:
    from ams.core.scoring import ScoringEngine


def _extract_component_result(report: Mapping[str, object], component_name: str) -> Dict[str, object]:
    """Extract the component result."""
    component_result = report[component_name]
    return {
        "rationale": component_result.get("rationale", []),
        "static_summary": component_result.get("static_summary"),
        "behavioural_summary": component_result.get("behavioural_summary"),
        "browser_summary": component_result.get("browser_summary"),
    }


def apply_llm_hybrid_to_requirement_results(
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


def enrich_with_llm_hybrid(
    engine: "ScoringEngine",
    static_results: Mapping[str, object],
    findings: List[Finding],
) -> Tuple[Mapping[str, object], ScoreEvidenceBundle]:
    """Hybrid scoring: apply LLM adjustments, score from requirements, build evidence."""
    requirement_results = list(static_results["requirement_results"])
    apply_llm_hybrid_to_requirement_results(requirement_results, findings)
    relevant_components = list(static_results["relevant_components"])
    generated_at = str(static_results["generated_at"])
    findings_by_category = dict(static_results["findings_by_category"])
    resolved_config = static_results["resolved_config"]
    context = static_results["context"]
    behavioural_evidence = list(static_results["behavioural_evidence"])
    browser_evidence = list(static_results["browser_evidence"])
    component_results: Dict[str, dict] = {}
    component_summaries: Dict[str, ComponentScoreSummary] = {}
    for component in engine.COMPONENTS:
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

        score_value, penalty, summary = engine._score_component_from_requirements(
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

    overall_raw = engine._weighted_overall(
        component_results,
        relevant_components,
        resolved_config.component_weights,
    )
    confidence_summary = engine._build_confidence_summary(requirement_results)
    review = engine._build_review_recommendation(overall_raw, confidence_summary, component_summaries)
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
    for component in engine.COMPONENTS:
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
