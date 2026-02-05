from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from ams.assessors.base import Assessor
from ams.assessors.behavioral import DeterministicTestEngine, HTMLBehavioralAssessor
from ams.assessors.browser import PlaywrightAssessor
from ams.assessors.consistency import ConsistencyAssessor
from ams.assessors.required import (
    CSSRequiredRulesAssessor,
    HTMLRequiredElementsAssessor,
    JSRequiredFeaturesAssessor,
    PHPRequiredFeaturesAssessor,
    SQLRequiredFeaturesAssessor,
)
from ams.assessors.static import (
    CSSStaticAssessor,
    HTMLStaticAssessor,
    JSStaticAssessor,
    PHPStaticAssessor,
    SQLStaticAssessor,
)
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec
from ams.core.scoring import ScoringEngine
from ams.io.reporting import ReportWriter
from ams.io.submission import SubmissionProcessor


class AssessmentPipeline:
    """Orchestrates assessors, scoring, and reporting."""

    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
    ) -> None:
        self.assessors: Optional[List[Assessor]] = list(assessors) if assessors is not None else None
        self.scoring_engine = scoring_engine or ScoringEngine()

    def run(
        self, 
        submission_path: Path, 
        workspace_path: Path, 
        profile: str = "frontend",
        metadata: Mapping[str, object] | None = None
    ) -> Path:
        context = self._prepare_context(submission_path, workspace_path, profile)
        context.metadata["profile"] = profile
        
        # Add submission metadata to context
        if metadata:
            context.metadata["submission_metadata"] = metadata
        
        profile_spec = get_profile_spec(profile)
        assessors = self.assessors or _default_assessors(profile_spec)

        findings: List[Finding] = []
        for assessor in assessors:
            findings.extend(assessor.run(context))

        # Add CONFIG warnings for required components with no required rules
        findings.extend(self._check_config_warnings(profile_spec, context))

        scores, score_evidence = self.scoring_engine.score_with_evidence(
            findings,
            profile=profile,
            behavioural_evidence=context.behavioural_evidence,
            browser_evidence=context.browser_evidence,
        )
        report_path = workspace_path / "report.json"
        ReportWriter(report_path).write(context, findings, scores, score_evidence=score_evidence, metadata=metadata)
        return report_path

    def _prepare_context(self, submission_path: Path, workspace_path: Path, profile: str) -> SubmissionContext:
        return SubmissionProcessor().prepare(submission_path, workspace_path, profile=profile)

    def _check_config_warnings(self, profile_spec: ProfileSpec, context: SubmissionContext) -> List[Finding]:
        """Check for configuration issues: required components with no required rules."""
        warnings: List[Finding] = []
        components = ["html", "css", "js", "php", "sql"]
        
        for component in components:
            is_required = profile_spec.is_component_required(component)
            has_required_rules = profile_spec.has_required_rules(component)
            
            if is_required and not has_required_rules:
                # Component is required but has no required rules configured
                warnings.append(
                    Finding(
                        id=f"CONFIG.MISSING_REQUIRED_RULES.{component.upper()}",
                        category="config",
                        message=f"Component '{component}' is required for profile '{profile_spec.name}' but has no required rules configured. This may indicate a marker configuration issue.",
                        severity=Severity.WARN,
                        evidence={
                            "component": component,
                            "profile": profile_spec.name,
                            "required": True,
                            "has_required_rules": False,
                        },
                        source="pipeline",
                        finding_category=FindingCategory.CONFIG,
                        profile=profile_spec.name,
                        required=True,
                    )
                )
        
        return warnings


def _default_assessors(profile_spec: ProfileSpec) -> List[Assessor]:
    """Return the default ordered assessor pipeline for a profile."""
    return [
        HTMLStaticAssessor(),
        CSSStaticAssessor(),
        JSStaticAssessor(),
        PHPStaticAssessor(),
        SQLStaticAssessor(),
        HTMLRequiredElementsAssessor(profile=profile_spec),
        CSSRequiredRulesAssessor(profile=profile_spec),
        JSRequiredFeaturesAssessor(profile=profile_spec),
        PHPRequiredFeaturesAssessor(profile=profile_spec),
        SQLRequiredFeaturesAssessor(profile=profile_spec),
        ConsistencyAssessor(),  # Cross-file consistency checks after static/required
        HTMLBehavioralAssessor(),
        DeterministicTestEngine(),
        PlaywrightAssessor(),
    ]


__all__ = ["AssessmentPipeline"]
