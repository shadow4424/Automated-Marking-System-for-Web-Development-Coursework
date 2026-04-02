from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Optional

# Standardised severity levels for findings.
class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"
    THREAT = "THREAT"
    SKIPPED = "SKIPPED"

# Standardised finding categories.
class FindingCategory(str, Enum):
    MISSING = "missing"  # Required artefact/files missing
    SYNTAX = "syntax"  # Syntax errors or issues
    STRUCTURE = "structure"  # Structural issues (e.g., unbalanced braces)
    BEHAVIORAL = "behavioral"  # Behavioural/runtime issues
    CONFIG = "config"  # Configuration issues (marker setup problems)
    EVIDENCE = "evidence"  # Informational evidence collected
    VISUAL = "visual"  # Phase C: Visual/layout issues from vision analysis
    SECURITY = "security"  # Sandbox threat detections
    OTHER = "other"  # Other issues

# Data models for the marking system.
@dataclass
class SubmissionContext:
    """Runtime context shared across assessors during one submission run."""
    submission_path: Path   # Absolute path to the root of the submission being assessed.
    workspace_path: Path    # Absolute path to the workspace for this submission.
    discovered_files: MutableMapping[str, List[Path]] = field(default_factory=dict) # Mapping of component to discovered files.
    scoring_files: MutableMapping[str, List[Path]] = field(default_factory=dict)    # Mapping of component to files selected for scoring.
    metadata: MutableMapping[str, object] = field(default_factory=dict) # Arbitrary metadata that assessors can read/write to share information.
    behavioural_evidence: List["BehaviouralEvidence"] = field(default_factory=list) # Collected evidence from deterministic behavioural tests.
    browser_evidence: List["BrowserEvidence"] = field(default_factory=list) # Collected evidence from browser automation checks.
    manifest: Optional["SubmissionManifest"] = None # Collected manifest metadata for the submission.
    artefact_inventory: Optional["ArtefactInventory"] = None    # Grouped artefact discovery results for the submission.
    role_mapping: Optional["RoleMappedSubmission"] = None   # Role-based selection of relevant files from the submission.
    resolved_config: object | None = None   # Fully resolved marking configuration for the submission.
    requirement_results: List["RequirementEvaluationResult"] = field(default_factory=list)  # Results of evaluating requirement rules against submission evidence.
    confidence_summary: Optional["ConfidenceSummary"] = None    # Confidence and review signals attached to the marking results.
    review_recommendation: Optional["ReviewRecommendation"] = None  # Manual review recommendation generated for the submission.

    # Helper method to get files for a component.
    def files_for(self, component: str, *, relevant_only: bool = True) -> List[Path]:
        """Return discovered files for one component, preferring scoring files."""
        if relevant_only and self.scoring_files.get(component):
            return list(self.scoring_files.get(component, []))
        return list(self.discovered_files.get(component, []))

# Context models for the marking system.
@dataclass
class SubmissionManifestEntry:
    """One discovered file entry in the submission manifest."""
    path: str
    absolute_path: str
    component: str
    size_bytes: int
    reachable: bool = False
    orphan: bool = False
    duplicate: bool = False
    backup: bool = False

    def to_dict(self) -> Dict[str, object]:
        """Serialise the manifest entry to a plain dictionary."""
        return {
            "path": self.path,
            "absolute_path": self.absolute_path,
            "component": self.component,
            "size_bytes": self.size_bytes,
            "reachable": self.reachable,
            "orphan": self.orphan,
            "duplicate": self.duplicate,
            "backup": self.backup,
        }

# Higher-level context models for sharing structured information across assessors and report generation.
@dataclass
class SubmissionManifest:
    """Collected manifest metadata for a submission."""
    entries: List[SubmissionManifestEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the submission manifest to a plain dictionary."""
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }

# Baseline Hardening addition.
@dataclass
class ArtefactRelation:
    """A relationship between two discovered submission artefacts."""
    source: str
    target: str
    relation: str

    def to_dict(self) -> Dict[str, object]:
        """Serialise the artefact relation to a plain dictionary."""
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
        }

# Baseline Hardening addition.
@dataclass
class ArtefactInventory:
    """Grouped artefact discovery results for a submission."""
    artefacts: Dict[str, List[str]] = field(default_factory=dict)
    relations: List[ArtefactRelation] = field(default_factory=list)
    orphan_files: List[str] = field(default_factory=list)
    duplicate_files: List[str] = field(default_factory=list)
    backup_files: List[str] = field(default_factory=list)
    candidate_execution_map: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the artefact inventory to a plain dictionary."""
        return {
            "artefacts": {key: list(value) for key, value in self.artefacts.items()},
            "relations": [relation.to_dict() for relation in self.relations],
            "orphan_files": list(self.orphan_files),
            "duplicate_files": list(self.duplicate_files),
            "backup_files": list(self.backup_files),
            "candidate_execution_map": {
                key: list(value) for key, value in self.candidate_execution_map.items()
            },
        }

# Baseline Hardening addition.
@dataclass
class RoleMappedSubmission:
    """Role-based selection of relevant files from a submission."""
    roles: Dict[str, List[str]] = field(default_factory=dict)
    relevant_files: Dict[str, List[str]] = field(default_factory=dict)
    selection_trace: List[Dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the role-mapped submission to a plain dictionary."""
        return {
            "roles": {key: list(value) for key, value in self.roles.items()},
            "relevant_files": {key: list(value) for key, value in self.relevant_files.items()},
            "selection_trace": [dict(item) for item in self.selection_trace],
        }

# Higher-level result models for structured representation of marking outcomes.
@dataclass
class RequirementEvaluationResult:
    """Result of evaluating one requirement rule against submission evidence."""
    requirement_id: str
    component: str
    description: str
    stage: str
    aggregation_mode: str
    score: float | str
    status: str
    weight: float = 1.0
    required: bool = True
    evidence: Mapping[str, object] = field(default_factory=dict)
    contributing_paths: List[str] = field(default_factory=list)
    skipped_reason: str | None = None
    confidence_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the requirement result to a plain dictionary."""
        return {
            "requirement_id": self.requirement_id,
            "component": self.component,
            "description": self.description,
            "stage": self.stage,
            "aggregation_mode": self.aggregation_mode,
            "score": self.score,
            "status": self.status,
            "weight": self.weight,
            "required": self.required,
            "evidence": dict(self.evidence),
            "contributing_paths": list(self.contributing_paths),
            "skipped_reason": self.skipped_reason,
            "confidence_flags": list(self.confidence_flags),
        }

# Baseline Hardening addition.
@dataclass
class ComponentScoreSummary:
    """Aggregate scoring summary for one assessed component."""
    component: str
    score: float | str
    weight: float
    requirement_count: int
    met: int
    partial: int
    failed: int
    skipped: int

    def to_dict(self) -> Dict[str, object]:
        """Serialise the component summary to a plain dictionary."""
        return {
            "component": self.component,
            "score": self.score,
            "weight": self.weight,
            "requirement_count": self.requirement_count,
            "met": self.met,
            "partial": self.partial,
            "failed": self.failed,
            "skipped": self.skipped,
        }

# Baseline Hardening addition.
@dataclass
class ConfidenceSummary:
    """Confidence and review signals attached to a marking result."""
    level: str
    reasons: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    skipped_checks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the confidence summary to a plain dictionary."""
        return {
            "level": self.level,
            "reasons": list(self.reasons),
            "flags": list(self.flags),
            "skipped_checks": list(self.skipped_checks),
        }

# Baseline Hardening addition.
@dataclass
class ReviewRecommendation:
    """Manual review recommendation generated for a submission."""
    recommended: bool
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """Serialise the review recommendation to a plain dictionary."""
        return {
            "recommended": self.recommended,
            "reasons": list(self.reasons),
        }

# Baseline Hardening additions.
@dataclass
class Finding:
    """Standardised finding with consistent schema for auditability."""
    id: str  # Unique finding code
    category: str  # Component category: "html", "css", "js", "php", "sql", "config", "visual"
    message: str  # Human-readable message
    severity: Severity  # Severity level
    evidence: Mapping[str, object]  # Structured evidence dict
    source: str  # Source assessor name

    # Standardised fields for auditability
    finding_category: FindingCategory = field(default=FindingCategory.OTHER)  # Type: missing/syntax/structure/etc
    profile: str | None = None  # Profile name if applicable
    required: bool | None = None  # Whether component is required for profile

    # Baseline Hardening additions
    score_delta: Optional[float] = None  # Deterministic score impact (e.g., -5.0)
    tags: List[str] = field(default_factory=list)  # Descriptive tags
    timestamp: float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

# Baseline Hardening additions.
@dataclass
class ScoreEvidenceBundle:
    """Serialisable bundle of scoring evidence used in reports."""
    profile: str    # Profile name or identifier this evidence relates to.
    generated_at: str   # ISO timestamp of when the evidence was generated.
    environment: Mapping[str, str]  # Environment details
    components: Dict[str, Mapping[str, object]] # Component-level evidence and scores
    overall: Mapping[str, object]   # Overall scoring evidence and breakdown
    requirements: List[Mapping[str, object]] = field(default_factory=list)  # Detailed requirement-level evidence
    assignment_profile: Mapping[str, object] = field(default_factory=dict)  # Assignment profile details
    role_mapping: Mapping[str, object] = field(default_factory=dict)  # Role mapping details
    confidence: Mapping[str, object] = field(default_factory=dict)  # Confidence and review signals
    review: Mapping[str, object] = field(default_factory=dict)  # Manual review recommendation details
    manifest: Mapping[str, object] = field(default_factory=dict)    # Submission manifest details
    artefact_inventory: Mapping[str, object] = field(default_factory=dict)  # Artefact inventory details

    def to_dict(self) -> Dict[str, object]:
        """Serialise the scoring evidence bundle to a report-friendly dictionary."""
        component_scores = {
            name: {
                "score": data.get("score"),
                "weight": data.get("weight", 0.0),
            }
            for name, data in self.components.items()
        }
        final_score = self.overall.get("final", self.overall.get("raw_average", 0.0))
        return {
            "profile": self.profile,
            "generated_at": self.generated_at,
            "environment": dict(self.environment),
            "components": dict(self.components),
            "overall": dict(self.overall),
            "final_score": final_score,
            "max_score": 1.0,
            "component_scores": component_scores,
            "requirements": list(self.requirements),
            "assignment_profile": dict(self.assignment_profile),
            "role_mapping": dict(self.role_mapping),
            "confidence": dict(self.confidence),
            "review": dict(self.review),
            "manifest": dict(self.manifest),
            "artefact_inventory": dict(self.artefact_inventory),
        }

# Baseline Hardening additions.
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
        """Serialise behavioural evidence to a plain dictionary."""
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
        """Serialise browser evidence to a plain dictionary."""
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
        """Serialise report metadata to a plain dictionary."""
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
        """Serialise the full report to a plain dictionary."""
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
    "SubmissionManifestEntry",
    "SubmissionManifest",
    "ArtefactRelation",
    "ArtefactInventory",
    "RoleMappedSubmission",
    "RequirementEvaluationResult",
    "ComponentScoreSummary",
    "ConfidenceSummary",
    "ReviewRecommendation",
    "Finding",
    "ScoreEvidenceBundle",
    "BehaviouralEvidence",
    "BrowserEvidence",
    "Report",
    "ReportMetadata",
]
