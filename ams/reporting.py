from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone

from .models import Finding, SubmissionContext


class ReportWriter:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def write(
        self,
        context: SubmissionContext,
        findings: Iterable[Finding],
        scores: object,
    ) -> Path:
        report = {
            "metadata": dict(context.metadata),
            "submission_path": str(context.submission_path),
            "workspace_path": str(context.workspace_path),
            "findings": [self._serialize_finding(f) for f in findings],
            "scores": scores,
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return self.output_path

    def _serialize_finding(self, finding: Finding) -> dict:
        return {
            "id": finding.id,
            "category": finding.category,
            "message": finding.message,
            "severity": finding.severity.value,
            "evidence": dict(finding.evidence),
            "source": finding.source,
        }
