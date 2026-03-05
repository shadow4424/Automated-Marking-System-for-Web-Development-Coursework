from __future__ import annotations

import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional

from ams.core.models import (
    BehaviouralEvidence,
    BrowserEvidence,
    Finding,
    SubmissionContext,
    ScoreEvidenceBundle,
    Report,
    ReportMetadata,
)


class ReportWriter:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path

    def write(
        self,
        context: SubmissionContext,
        findings: Iterable[Finding],
        scores: object,
        score_evidence: Optional[ScoreEvidenceBundle] = None,
        metadata: Optional[Mapping[str, object]] = None,
        llm_evidence: Optional[dict] = None,
    ) -> Path:
        profile = str(context.metadata.get("profile", "unknown"))  # Ensure string
        scoring_mode = str(context.metadata.get("scoring_mode", "unknown"))
        
        behavioural = [self._serialize_behavioural(e) for e in getattr(context, "behavioural_evidence", [])]
        browser = [self._serialize_browser(e) for e in getattr(context, "browser_evidence", [])]
        environment = self._environment_summary(context, behavioural, browser, score_evidence)
        
        # Merge metadata
        merged_metadata = dict(context.metadata)
        if metadata:
            merged_metadata.update(metadata) # flatten
            # context.metadata might already have submission_metadata if passed in pipeline.run

        # Create Report Metadata
        try:
            _version = importlib.metadata.version("ams")
        except importlib.metadata.PackageNotFoundError:
            _version = "0.1.0-dev"

        report_meta = ReportMetadata(
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            pipeline_version=_version,
            scoring_mode=scoring_mode,
            profile=profile,
            provider=merged_metadata.get("llm_provider"),
            submission_metadata=metadata, 
        )

        # Create Report
        report = Report(
            metadata=report_meta,
            submission_path=str(context.submission_path),
            workspace_path=str(context.workspace_path),
            findings=list(findings),
            scores=scores if isinstance(scores, dict) else {},
            score_evidence=self._merge_score_evidence(score_evidence, llm_evidence),
            behavioural_evidence=behavioural,
            browser_evidence=browser,
            environment=environment,
            marking_policy=self._policy_notes(profile),
            generated_at=report_meta.timestamp,
        )
        
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        self._write_summary(context, scores, profile, behavioural, browser, metadata)
        return self.output_path

    def _serialize_finding(self, finding: Finding) -> dict:
        """Serialize finding with all standardised fields."""
        result = {
            "id": finding.id,
            "category": finding.category,
            "message": finding.message,
            "severity": finding.severity.value,
            "evidence": dict(finding.evidence),
            "source": finding.source,
            "finding_category": finding.finding_category.value,
        }
        # Add optional fields if present
        if finding.profile is not None:
            result["profile"] = finding.profile
        if finding.required is not None:
            result["required"] = finding.required
        return result

    def _serialize_behavioural(self, evidence: BehaviouralEvidence) -> dict:
        if hasattr(evidence, "to_dict"):
            return evidence.to_dict()
        return dict(evidence)

    def _serialize_browser(self, evidence: BrowserEvidence) -> dict:
        if hasattr(evidence, "to_dict"):
            return evidence.to_dict()
        return dict(evidence)

    def _policy_notes(self, profile: str) -> dict:
        """Generate marking policy notes for clarity."""
        return {
            "profile": profile,
            "notes": [
                "SKIPPED = Component not applicable to this profile; no impact on marks.",
                "MISSING = Component required for this profile but absent; component scores 0 and affects overall score.",
                "CONFIG warnings = Marker configuration issues; do not affect student scores but flag setup problems.",
            ],
            "component_scoring": {
                "skipped": "Not counted in overall score calculation",
                "missing": "Scores 0.0 and reduces overall score",
                "present": "Scored based on evidence (1.0, 0.5, or 0.0)",
            },
        }

    def _merge_score_evidence(
        self, 
        score_evidence: Optional[ScoreEvidenceBundle], 
        llm_evidence: Optional[dict]
    ) -> Optional[dict]:
        """Merge ScoreEvidenceBundle with LLM evidence into a single dict."""
        if score_evidence is None:
            return {"llm_analysis": llm_evidence} if llm_evidence else None
        
        result = score_evidence.to_dict()
        if llm_evidence:
            result["llm_analysis"] = llm_evidence
        return result

    def _write_summary(
        self,
        context: SubmissionContext,
        scores: object,
        profile: str,
        behavioural: list[dict],
        browser: list[dict],
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        summary_path = self.output_path.with_name("summary.txt")
        lines = []
        submission_name = context.metadata.get("submission_name", "submission")
        lines.append(f"Submission: {submission_name}")
        lines.append(f"Profile: {profile}")
        
        # Add metadata if available
        if metadata:
            lines.append("")
            lines.append("Submission Metadata:")
            if metadata.get("student_id"):
                lines.append(f"  Student ID: {metadata.get('student_id')}")
            if metadata.get("assignment_id"):
                lines.append(f"  Assignment ID: {metadata.get('assignment_id')}")
            if metadata.get("original_filename"):
                lines.append(f"  Original Filename: {metadata.get('original_filename')}")
            if metadata.get("timestamp"):
                lines.append(f"  Upload Timestamp: {metadata.get('timestamp')}")
        
        lines.append("")
        overall_score = scores.get("overall") if isinstance(scores, dict) else None
        if overall_score is not None:
            lines.append(f"Overall score: {overall_score:.2f}")
            lines.append("")

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
            lines.append("")

        # Add policy notes
        lines.append("Marking Policy Notes:")
        lines.append("- SKIPPED = Component not applicable to this profile; no impact on marks.")
        lines.append("- MISSING = Component required for this profile but absent; component scores 0 and affects overall.")
        lines.append("- CONFIG warnings = Marker configuration issues; do not affect student scores.")

        # Behavioural summary
        if behavioural:
            lines.append("")
            lines.append("Behavioural tests:")
            for entry in behavioural:
                status = entry.get("status")
                test_id = entry.get("test_id")
                diag = entry.get("stderr") or entry.get("stdout") or ""
                diag_first = diag.splitlines()[0] if diag else ""
                lines.append(f"- {test_id}: {status.upper() if isinstance(status, str) else status}" + (f" ({diag_first})" if diag_first else ""))

        if browser:
            lines.append("")
            lines.append("Browser tests:")
            for entry in browser:
                status = entry.get("status")
                test_id = entry.get("test_id")
                console_list = entry.get("console_errors") or []
                diag = entry.get("notes") or (console_list[0] if console_list else "")
                diag_first = diag.splitlines()[0] if isinstance(diag, str) and diag else ""
                lines.append(f"- {test_id}: {status.upper() if isinstance(status, str) else status}" + (f" ({diag_first})" if diag_first else ""))

        summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _environment_summary(
        self,
        context: SubmissionContext,
        behavioural: list[dict],
        browser: list[dict],
        score_evidence: ScoreEvidenceBundle | None,
    ) -> dict:
        def _skipped_due_to_php(entries: list[dict]) -> bool:
            for ev in entries:
                if ev.get("status") == "skipped":
                    reason_text = " ".join(str(v) for v in ev.values())
                    if "php unavailable" in reason_text or "php binary not available" in reason_text or "not installed" in reason_text:
                        return True
            return False

        php_available = not _skipped_due_to_php(behavioural)
        browser_available = not any(ev.get("status") == "error" and "php" in (ev.get("notes") or "").lower() for ev in browser)
        behavioural_run = any(ev.get("status") in {"pass", "fail", "timeout"} for ev in behavioural)
        browser_run = any(ev.get("status") in {"pass", "fail", "timeout"} for ev in browser)
        env = {
            "php_available": php_available,
            "browser_available": browser_available,
            "behavioural_tests_run": behavioural_run,
            "browser_tests_run": browser_run,
        }
        if score_evidence:
            env["runtime"] = dict(score_evidence.environment)
        return env


__all__ = ["ReportWriter"]
