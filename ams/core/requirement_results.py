from __future__ import annotations

from collections.abc import Mapping, Sequence

from ams.core.models import RequirementEvaluationResult
from ams.core.profiles import RequirementDefinition


def build_requirement_result(
    definition: RequirementDefinition,
    *,
    score: float | str,
    status: str,
    evidence: Mapping[str, object],
    component: str | None = None,
    required: bool | None = None,
    contributing_paths: Sequence[str] | None = None,
    skipped_reason: str | None = None,
    confidence_flags: Sequence[str] | None = None,
) -> RequirementEvaluationResult:
    """Build a requirement result using values from the definition by default."""
    return RequirementEvaluationResult(
        requirement_id=definition.id,
        component=component or definition.component,
        description=definition.description,
        stage=definition.stage,
        aggregation_mode=definition.aggregation_mode,
        score=score,
        status=status,
        weight=definition.weight,
        required=definition.required if required is None else required,
        evidence=evidence,
        contributing_paths=list(contributing_paths or []),
        skipped_reason=skipped_reason,
        confidence_flags=list(confidence_flags or []),
    )


def build_skipped_requirement_result(
    definition: RequirementDefinition,
    *,
    reason: str,
    component: str | None = None,
    required: bool | None = None,
    confidence_flags: Sequence[str] | None = None,
    evidence: Mapping[str, object] | None = None,
) -> RequirementEvaluationResult:
    """Build a standard skipped result."""
    skipped_evidence = dict(evidence or {})
    skipped_evidence.setdefault("reason", reason)
    return build_requirement_result(
        definition,
        component=component,
        score="SKIPPED",
        status="SKIPPED",
        required=required,
        evidence=skipped_evidence,
        skipped_reason=reason,
        confidence_flags=confidence_flags,
    )


__all__ = [
    "build_requirement_result",
    "build_skipped_requirement_result",
]
