"""Human-readable and machine-readable report generation."""

from __future__ import annotations

import json
from pathlib import Path

from .models import AssessmentReport


class ReportWriter:
    def render_text(self, report: AssessmentReport) -> str:
        lines = [
            f"Submission: {report.submission.submission_zip}",
            f"Extracted to: {report.submission.extracted_path}",
            "Logs:",
        ]
        lines.extend(f"  - {entry}" for entry in report.submission.log)
        lines.append("Steps:")
        for step in report.steps:
            lines.append(f"  - {step.name}: score={step.score}")
            for reason in step.reasons:
                lines.append(f"      * {reason}")
        lines.append(f"Total score: {report.total_score}")
        return "\n".join(lines)

    def write_json(self, report: AssessmentReport, path: Path | str) -> None:
        Path(path).write_text(json.dumps(report.to_dict(), indent=2))
