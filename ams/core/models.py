from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class FindingCategory(str, Enum):
    """Category of finding for grouping and filtering."""
    MISSING = "missing"  # Required artefact/files missing
    SYNTAX = "syntax"  # Syntax errors or issues
    STRUCTURE = "structure"  # Structural issues (e.g., unbalanced braces)
    BEHAVIORAL = "behavioral"  # Behavioral/runtime issues
    CONFIG = "config"  # Configuration issues (marker setup problems)
    EVIDENCE = "evidence"  # Informational evidence collected
    VISUAL = "visual"  # Phase C: Visual/layout issues from vision analysis
    OTHER = "other"  # Other issues


@dataclass
class SubmissionContext:
    submission_path: Path
    workspace_path: Path
    discovered_files: MutableMapping[str, List[Path]] = field(default_factory=dict)
    metadata: MutableMapping[str, object] = field(default_factory=dict)
    behavioural_evidence: List["BehaviouralEvidence"] = field(default_factory=list)
    browser_evidence: List["BrowserEvidence"] = field(default_factory=list)


@dataclass
class Finding:
    """Standardised finding with consistent schema for auditability."""
    id: str  # Unique finding code (e.g., "HTML.MISSING_FILES", "CSS.SYNTAX_ERROR")
    category: str  # Component category: "html", "css", "js", "php", "sql", "config", "visual"
    message: str  # Human-readable message
    severity: Severity  # Severity level
    evidence: Mapping[str, object]  # Structured evidence dict
    source: str  # Source assessor name
    
    # Standardised fields for auditability
    finding_category: FindingCategory = field(default=FindingCategory.OTHER)  # Type: missing/syntax/structure/etc
    profile: str | None = None  # Profile name if applicable
    required: bool | None = None  # Whether component is required for profile
    
    # Phase A: Baseline Hardening additions
    score_delta: Optional[float] = None  # Deterministic score impact (e.g., -5.0)
    tags: List[str] = field(default_factory=list)  # Descriptive tags
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())


@dataclass
class ScoreEvidenceBundle:
    profile: str
    generated_at: str
    environment: Mapping[str, str]
    components: Dict[str, Mapping[str, object]]
    overall: Mapping[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "profile": self.profile,
            "generated_at": self.generated_at,
            "environment": dict(self.environment),
            "components": dict(self.components),
            "overall": dict(self.overall),
        }


@dataclass
class BehaviouralEvidence:
    """Structured evidence for deterministic behavioural tests."""

    test_id: str
    component: str
    status: str
    stage: str = "behavioural"
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    inputs: Mapping[str, object] = field(default_factory=dict)
    outputs: Mapping[str, object] = field(default_factory=dict)
    artifacts: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "test_id": self.test_id,
            "stage": self.stage,
            "component": self.component,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "artifacts": dict(self.artifacts),
        }


@dataclass
class BrowserEvidence:
    """Structured evidence for browser automation checks."""

    test_id: str
    stage: str = "browser"
    status: str = "skipped"
    duration_ms: int = 0
    url: str = ""
    actions: List[Mapping[str, object]] = field(default_factory=list)
    dom_before: str = ""
    dom_after: str = ""
    console_errors: List[str] = field(default_factory=list)
    network_errors: List[str] = field(default_factory=list)
    screenshot_paths: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "test_id": self.test_id,
            "stage": self.stage,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "url": self.url,
            "actions": list(self.actions),
            "dom_before": self.dom_before,
            "dom_after": self.dom_after,
            "console_errors": list(self.console_errors),
            "network_errors": list(self.network_errors),
            "screenshot_paths": list(self.screenshot_paths),
            "notes": self.notes,
        }


@dataclass
class ReportMetadata:
    """Metadata for a report run."""
    timestamp: str
    pipeline_version: str = "unknown"  # Placeholder for git hash
    scoring_mode: str = "unknown"
    profile: str = "unknown"
    provider: Optional[str] = None
    cache_stats: Optional[Dict[str, int]] = None
    submission_metadata: Optional[Mapping[str, object]] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "pipeline_version": self.pipeline_version,
            "scoring_mode": self.scoring_mode,
            "profile": self.profile,
            "provider": self.provider,
            "cache_stats": self.cache_stats,
            "submission_metadata": dict(self.submission_metadata) if self.submission_metadata else None,
        }


@dataclass
class Report:
    """Top-level report structure."""
    metadata: ReportMetadata
    submission_path: str
    workspace_path: str
    findings: List[Finding]
    scores: Dict[str, object]
    score_evidence: Optional[Dict[str, object]]
    behavioural_evidence: List[Dict[str, object]]
    browser_evidence: List[Dict[str, object]]
    environment: Dict[str, bool]
    marking_policy: Dict[str, object]
    generated_at: str
    report_version: str = "1.0"
    
    def to_dict(self) -> Dict[str, object]:
        from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats

        serialized_findings = [
            {
                "id": f.id,
                "category": f.category,
                "message": f.message,
                "severity": f.severity.value,
                "evidence": dict(f.evidence),
                "source": f.source,
                "finding_category": f.finding_category.value,
                "profile": f.profile,
                "required": f.required,
                "score_delta": f.score_delta,
                "tags": f.tags,
                "timestamp": f.timestamp,
            }
            for f in self.findings
        ]

        checks, diagnostics = aggregate_findings_to_checks(serialized_findings)
        check_stats = compute_check_stats(checks)

        return {
            "report_version": self.report_version,
            "generated_at": self.generated_at,
            "metadata": self.metadata.to_dict(),
            "submission_path": self.submission_path,
            "workspace_path": self.workspace_path,
            "findings": serialized_findings,
            "checks": [c.to_dict() for c in checks],
            "check_stats": check_stats,
            "diagnostics": [d for d in diagnostics],
            "scores": self.scores,
            "score_evidence": self.score_evidence,
            "behavioural_evidence": self.behavioural_evidence,
            "browser_evidence": self.browser_evidence,
            "environment": self.environment,
            "marking_policy": self.marking_policy,
        }


__all__ = [
    "Severity",
    "FindingCategory",
    "SubmissionContext",
    "Finding",
    "ScoreEvidenceBundle",
    "BehaviouralEvidence",
    "BrowserEvidence",
    "Report",
    "ReportMetadata",
]
