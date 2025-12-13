"""Scoring utilities with deterministic aggregation."""

from __future__ import annotations

from typing import Iterable

from .models import AssessmentReport, StepResult, SubmissionContext


class ScoreAggregator:
    def aggregate(self, submission: SubmissionContext, steps: Iterable[StepResult]) -> AssessmentReport:
        steps = list(steps)
        if not steps:
            total = 0.0
        else:
            ratio = sum(step.score for step in steps) / (len(steps))
            if ratio >= 0.75:
                total = 1.0
            elif ratio >= 0.35:
                total = 0.5
            else:
                total = 0.0
        return AssessmentReport(submission=submission, steps=steps, total_score=total)
