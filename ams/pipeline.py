from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from .assessors import Assessor
from .css_static import CSSStaticAssessor
from .html_static import HTMLStaticAssessor
from .models import Finding, SubmissionContext
from .reporting import ReportWriter
from .scoring import ScoringEngine
from .submission import SubmissionProcessor

class AssessmentPipeline:
    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
    ) -> None:
        if assessors is None:
            self.assessors = [HTMLStaticAssessor(), CSSStaticAssessor()]
        else:
            self.assessors: List[Assessor] = list(assessors)
        self.scoring_engine = scoring_engine or ScoringEngine()

    def run(self, submission_path: Path, workspace_path: Path) -> Path:
        context = self._prepare_context(submission_path, workspace_path)
        findings: List[Finding] = []
        for assessor in self.assessors:
            findings.extend(assessor.run(context))
        scores = self.scoring_engine.score(findings)
        report_path = workspace_path / "report.json"
        ReportWriter(report_path).write(context, findings, scores)
        return report_path

    def _prepare_context(self, submission_path: Path, workspace_path: Path) -> SubmissionContext:
        return SubmissionProcessor().prepare(submission_path, workspace_path)
