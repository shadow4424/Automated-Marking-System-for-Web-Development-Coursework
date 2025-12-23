from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from .assessors import Assessor
from .css_static import CSSStaticAssessor
from .html_static import HTMLStaticAssessor
from .html_required import HTMLRequiredElementsAssessor
from .css_required import CSSRequiredRulesAssessor
from .js_static import JSStaticAssessor
from .js_required import JSRequiredFeaturesAssessor
from .php_static import PHPStaticAssessor
from .php_required import PHPRequiredFeaturesAssessor
from .sql_static import SQLStaticAssessor
from .sql_required import SQLRequiredFeaturesAssessor
from .models import Finding, SubmissionContext
from .reporting import ReportWriter
from .scoring import ScoringEngine
from .submission import SubmissionProcessor
from .profiles import get_profile_spec, ProfileSpec

class AssessmentPipeline:
    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
    ) -> None:
        self.assessors: Optional[List[Assessor]] = list(assessors) if assessors is not None else None
        self.scoring_engine = scoring_engine or ScoringEngine()

    def run(self, submission_path: Path, workspace_path: Path, profile: str = "frontend") -> Path:
        context = self._prepare_context(submission_path, workspace_path)
        context.metadata["profile"] = profile
        findings: List[Finding] = []
        profile_spec = get_profile_spec(profile)
        assessors = self.assessors or _default_assessors(profile_spec)
        for assessor in assessors:
            findings.extend(assessor.run(context))
        # Pass profile into scoring engine
        scores = self.scoring_engine.score(findings, profile=profile)
        report_path = workspace_path / "report.json"
        ReportWriter(report_path).write(context, findings, scores)
        return report_path

    def _prepare_context(self, submission_path: Path, workspace_path: Path) -> SubmissionContext:
        return SubmissionProcessor().prepare(submission_path, workspace_path)


def _default_assessors(profile_spec: ProfileSpec) -> List[Assessor]:
    """Return the default ordered assessor pipeline for a profile."""
    return [
        HTMLStaticAssessor(),
        HTMLRequiredElementsAssessor(profile=profile_spec),
        CSSStaticAssessor(),
        CSSRequiredRulesAssessor(profile=profile_spec),
        JSStaticAssessor(),
        JSRequiredFeaturesAssessor(profile=profile_spec),
        PHPStaticAssessor(),
        PHPRequiredFeaturesAssessor(profile=profile_spec),
        SQLStaticAssessor(),
        SQLRequiredFeaturesAssessor(profile=profile_spec),
    ]
