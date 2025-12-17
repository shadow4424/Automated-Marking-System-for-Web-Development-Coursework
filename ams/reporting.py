from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self._write_summary(context, scores)
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

    def _write_summary(self, context: SubmissionContext, scores: object) -> None:
        summary_path = self.output_path.with_name("summary.txt")
        lines = []
        submission_name = context.metadata.get("submission_name", "submission")
        lines.append(f"Submission: {submission_name}")
        overall_score = scores.get("overall") if isinstance(scores, dict) else None
        if overall_score is not None:
            lines.append(f"Overall score: {overall_score:.2f}")

        by_component = scores.get("by_component", {}) if isinstance(scores, dict) else {}
        if by_component:
            lines.append("Component scores:")
            for component in sorted(by_component.keys()):
                component_score = by_component[component].get("score")
                rationale = by_component[component].get("rationale", [])
                rationale_bits = []
                for entry in rationale[:2]:
                    rule = entry.get("rule")
                    evidence = entry.get("evidence") or {}
                    evidence_note = ""
                    if evidence:
                        evidence_note = " " + ", ".join(
                            f"{k}={v}" for k, v in sorted(evidence.items())
                        )
                    rationale_bits.append(f"{rule}{evidence_note}")
                lines.append(
                    f"- {component}: {component_score}"
                    + (" (" + "; ".join(rationale_bits) + ")" if rationale_bits else "")
                )

        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
