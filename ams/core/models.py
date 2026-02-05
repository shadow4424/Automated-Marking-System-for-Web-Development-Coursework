from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping


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
    """Standardized finding with consistent schema for auditability."""
    id: str  # Unique finding code (e.g., "HTML.MISSING_FILES", "CSS.SYNTAX_ERROR")
    category: str  # Component category: "html", "css", "js", "php", "sql", "config"
    message: str  # Human-readable message
    severity: Severity  # Severity level
    evidence: Mapping[str, object]  # Structured evidence dict
    source: str  # Source assessor name
    
    # Standardized fields for auditability
    finding_category: FindingCategory = field(default=FindingCategory.OTHER)  # Type: missing/syntax/structure/etc
    profile: str | None = None  # Profile name if applicable
    required: bool | None = None  # Whether component is required for profile


@dataclass
class RuleResult:
    """Enhanced rule result with LLM-ready evidence fields.
    
    This dataclass captures richer evidence for each rule evaluation,
    preparing for future LLM integration. It includes context and
    line numbers to support LLM reasoning about partial credit.
    """
    rule_id: str
    passed: bool
    confidence: float = 1.0  # Static assessor confidence (1.0 unless parsing errors)
    evidence_snippet: str = ""  # Code snippet showing the match
    context_before: str = ""  # Lines before the match
    context_after: str = ""  # Lines after the match
    line_numbers: list[int] = field(default_factory=list)  # Line numbers of matches
    category: str = ""  # Rule category (e.g., "Accessibility", "Security")
    severity: str = "medium"  # Rule severity: "low", "medium", "high"
    
    def to_dict(self) -> dict:
        """Convert to JSON-serialisable dictionary."""
        return {
            "rule_id": self.rule_id,
            "passed": self.passed,
            "confidence": self.confidence,
            "evidence_snippet": self.evidence_snippet,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "line_numbers": list(self.line_numbers),
            "category": self.category,
            "severity": self.severity,
        }



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


__all__ = [
    "Severity",
    "FindingCategory",
    "SubmissionContext",
    "Finding",
    "RuleResult",
    "ScoreEvidenceBundle",
    "BehaviouralEvidence",
    "BrowserEvidence",
]

