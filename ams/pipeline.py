"""Pipeline orchestration for the Automated Marking System."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .browser_automation import BrowserAutomationRunner
from .deterministic_tests import DeterministicTestRunner
from .models import AssessmentReport, StepResult
from .normalisation import SubmissionNormaliser
from .reporting import ReportWriter
from .scoring import ScoreAggregator
from .static_analysis import StaticAnalyser


class AssessmentPipeline:
    def __init__(self, workspace: Path | str = "submissions", enable_playwright: bool = False) -> None:
        self.normaliser = SubmissionNormaliser(workspace)
        self.static_analyser = StaticAnalyser()
        self.det_runner = DeterministicTestRunner()
        self.browser_runner = BrowserAutomationRunner(enable_playwright=enable_playwright)
        self.aggregator = ScoreAggregator()
        self.reporter = ReportWriter()

    def assess(self, submission_zip: Path | str) -> AssessmentReport:
        context = self.normaliser.normalize(submission_zip)
        steps: List[StepResult] = []

        steps.append(self.static_analyser.run(context))
        steps.append(self.det_runner.run(context))
        steps.append(self.browser_runner.run(context))

        report = self.aggregator.aggregate(context, steps)
        return report

    def assess_and_write_reports(self, submission_zip: Path | str, output_dir: Path | str) -> AssessmentReport:
        report = self.assess(submission_zip)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / f"{Path(submission_zip).stem}_report.txt"
        json_path = output_dir / f"{Path(submission_zip).stem}_report.json"
        text_path.write_text(self.reporter.render_text(report))
        self.reporter.write_json(report, json_path)
        return report
