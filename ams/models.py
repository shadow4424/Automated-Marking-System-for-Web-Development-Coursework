"""Core data models for the AMS pipeline.

The models capture step-level results, deterministic reasons, and aggregated scores.
All scoring is discrete: 1.0 (good attempt), 0.5 (partial), 0.0 (poor).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence


Score = float


@dataclass
class StepResult:
    """Deterministic output from a pipeline stage.

    Attributes:
        name: Identifier for the pipeline stage.
        score: Discrete mark in {1.0, 0.5, 0.0}.
        reasons: Human-readable justification strings (deterministic).
        artifacts: Optional mapping of artifact names to paths or inline content.
    """

    name: str
    score: Score
    reasons: List[str] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        reasons = "; ".join(self.reasons) if self.reasons else "No reasons recorded"
        return f"{self.name}: score={self.score} reasons={reasons}"


@dataclass
class SubmissionContext:
    """Normalized submission metadata."""

    submission_zip: Path
    extracted_path: Path
    normalized_files: List[Path]
    log: List[str] = field(default_factory=list)

    def add_log(self, entry: str) -> None:
        self.log.append(entry)


@dataclass
class AssessmentReport:
    """Aggregate report for a single submission."""

    submission: SubmissionContext
    steps: Sequence[StepResult]
    total_score: Score

    def to_dict(self) -> Dict[str, object]:
        return {
            "submission_zip": str(self.submission.submission_zip),
            "extracted_path": str(self.submission.extracted_path),
            "logs": list(self.submission.log),
            "steps": [
                {"name": step.name, "score": step.score, "reasons": step.reasons, "artifacts": step.artifacts}
                for step in self.steps
            ],
            "total_score": self.total_score,
        }


@dataclass
class DeterministicRule:
    """Rule describing a check and its contribution to a score.

    The evaluator emits reasons when rules pass or fail. Each rule may target a
    specific file pattern or property of the submission.
    """

    name: str
    description: str
    weight: float
    # ``evaluate`` returns (passed, reason)
    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:  # pragma: no cover - interface contract
        raise NotImplementedError
