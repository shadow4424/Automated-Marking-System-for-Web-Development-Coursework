from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from .assessors import Assessor
from .models import Finding, SubmissionContext
from .reporting import ReportWriter
from .scoring import ScoringEngine


class AssessmentPipeline:
    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
    ) -> None:
        self.assessors: List[Assessor] = list(assessors or [])
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
        workspace_path.mkdir(parents=True, exist_ok=True)
        submission_path = submission_path.resolve()
        workspace_path = workspace_path.resolve()
        metadata = {
            "submission_name": submission_path.name,
        }
        return SubmissionContext(
            submission_path=submission_path,
            workspace_path=workspace_path,
            discovered_files={},
            metadata=metadata,
        )
