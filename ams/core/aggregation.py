"""Aggregate raw findings into logical rubric checks."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ams.core.finding_ids import CSS as CID, HTML as HID, JS as JID, PHP as PID, SQL as SID

# Sources whose findings are diagnostic by default (not rubric checks).
_DIAGNOSTIC_SOURCES: frozenset[str] = frozenset(
    {
        "deterministic_test_engine",
        "browser_automation",
    }
)

# Finding ID prefixes that are always diagnostic.
_DIAGNOSTIC_ID_PREFIXES: tuple[str, ...] = (
    "BEHAVIOUR.",
    "BROWSER.",
)

# Finding ID prefixes that are enrichment (LLM / Vision meta).
_ENRICHMENT_ID_PREFIXES: tuple[str, ...] = (
    "VISUAL.",
)

# Finding IDs that are evidence collectors (static assessors), not checks.
_EVIDENCE_IDS: frozenset[str] = frozenset(
    {
        HID.ELEMENT_EVIDENCE,
        CID.EVIDENCE,
        JID.EVIDENCE,
        PID.EVIDENCE,
        SID.EVIDENCE,
    }
)

# Static assessor structural findings – informational, not rubric checks.
_STATIC_DIAGNOSTIC_IDS: frozenset[str] = frozenset(
    {
        HID.PARSE_OK,
        HID.PARSE_SUSPECT,
        CID.BRACES_BALANCED,
        CID.BRACES_UNBALANCED,
        JID.SYNTAX_OK,
        JID.SYNTAX_SUSPECT,
        PID.TAG_OK,
        PID.TAG_MISSING,
        PID.SYNTAX_OK,
        PID.SYNTAX_SUSPECT,
        SID.STRUCTURE_OK,
        SID.STRUCTURE_SUSPECT,
    }
)

# Logger that debugs the aggregation process, especially conflict resolution.
@dataclass
class CheckResult:
    """Aggregated result for a single logical rubric check."""

    check_id: str           # Stable identifier for the check (e.g., rule ID or finding ID)
    component: str          # Html / css / js / php / sql / consistency / behavioural
    status: str             # PASS / WARN / FAIL / SKIPPED
    occurrences: int = 1    # Number of findings that contributed to this check
    weight: Optional[float] = None  # Optional weight for scoring, if provided by evidence
    messages: List[str] = field(default_factory=list)   # Unique messages from contributing findings
    evidence: List[Dict[str, Any]] = field(default_factory=list)    # Collected evidence dicts from findings
    sources: List[str] = field(default_factory=list)    # Unique sources (assessors) that contributed findings to this check

    # Method to convert this CheckResult into a dictionary format
    def to_dict(self) -> Dict[str, Any]:
        """Return this check result as a dictionary."""
        return {
            "check_id": self.check_id,
            "component": self.component,
            "status": self.status,
            "occurrences": self.occurrences,
            "weight": self.weight,
            "messages": self.messages,
            "evidence": self.evidence,
            "sources": self.sources,
        }

# Function to map a Finding severity string to a CheckResult status.
def _severity_to_status(severity: str) -> str:
    """Map a Finding severity string to a CheckResult status."""
    s = severity.upper()
    if s == "INFO":
        return "PASS"
    if s in ("WARN", "FAIL", "SKIPPED"):
        return s
    return "PASS"

# Function to classify findings and deriving check keys for aggregation.
def is_diagnostic(finding: dict) -> bool:
    """Return True if *finding* is a diagnostic/infrastructure event."""
    fid: str = finding.get("id", "")
    source: str = finding.get("source", "")

    # Enrichment / vision events
    for prefix in _ENRICHMENT_ID_PREFIXES:
        if fid.startswith(prefix):
            return True

    # Browser / deterministic test engine events
    for prefix in _DIAGNOSTIC_ID_PREFIXES:
        if fid.startswith(prefix):
            return True
    if source in _DIAGNOSTIC_SOURCES:
        return True

    # Evidence collector findings (static assessors)
    if fid in _EVIDENCE_IDS:
        return True

    # Static structural diagnostics
    if fid in _STATIC_DIAGNOSTIC_IDS:
        return True

    # Security / quality warnings from static assessors — these are diagnostic
    if ".QUALITY." in fid or ".SECURITY." in fid:
        return True

    return False

# Function to derive a stable, unique check key for aggregation.
def get_check_key(finding: dict) -> str:
    """Derive a stable, unique check key for aggregation."""
    fid: str = finding.get("id", "")
    evidence: dict = finding.get("evidence", {}) or {}

    # Required assessor findings carry rule identity in evidence
    rule_id = evidence.get("rule_id")
    if rule_id:
        return str(rule_id)

    return fid

# Status merging logic: 
# when multiple findings contribute to the same check, 
# the most severe status should win. Priority: FAIL > WARN > PASS > SKIPPED.
_STATUS_PRIORITY = {"SKIPPED": 0, "PASS": 1, "WARN": 2, "FAIL": 3}

# Function to merge two statuses based on severity priority.
def _merge_status(existing: str, incoming: str) -> str:
    """Merge two statuses; the more severe one wins. Priority: FAIL > WARN > PASS > SKIPPED."""
    return existing if _STATUS_PRIORITY.get(existing, 0) >= _STATUS_PRIORITY.get(incoming, 0) else incoming

# Main aggregation function: takes raw findings and produces aggregated checks and diagnostics.
def aggregate_findings_to_checks(
    findings: List[dict],
) -> tuple[List[CheckResult], List[dict]]:
    """Aggregate raw findings into logical rubric checks."""
    checks_map: Dict[str, CheckResult] = {}
    diagnostics: List[dict] = []

    # Classify each finding as diagnostic or check.
    for f in findings:
        if is_diagnostic(f):
            diagnostics.append(f)
            continue
        
        # For check findings, derive the check key and aggregate results.
        key = get_check_key(f)
        severity = f.get("severity", "INFO")
        status = _severity_to_status(severity)
        component = f.get("category", "unknown")
        evidence = f.get("evidence", {}) or {}
        message = f.get("message", "")
        source = f.get("source", "")
        weight = evidence.get("weight")
        # Normalise weight to a float if possible, otherwise ignore it
        if weight is not None:
            try:
                weight = float(weight)
            except (TypeError, ValueError):
                weight = None

        # Aggregate into checks_map
        if key in checks_map:
            cr = checks_map[key]
            cr.occurrences += 1
            cr.status = _merge_status(cr.status, status)
            if message and message not in cr.messages:
                cr.messages.append(message)
            cr.evidence.append(evidence)
            if source and source not in cr.sources:
                cr.sources.append(source)
            # Keep highest weight
            if weight is not None and (cr.weight is None or weight > cr.weight):
                cr.weight = weight
        # If this is the first time we see this check key, create a new CheckResult
        else:
            checks_map[key] = CheckResult(
                check_id=key,
                component=component,
                status=status,
                occurrences=1,
                weight=weight,
                messages=[message] if message else [],
                evidence=[evidence] if evidence else [],
                sources=[source] if source else [],
            )

    return list(checks_map.values()), diagnostics

# Function to compute summary statistics from aggregated checks.
def compute_check_stats(checks: List[CheckResult]) -> Dict[str, int]:
    """Compute summary statistics from aggregated checks. Returns a dict with keys: total, passed, failed, warnings, skipped."""
    stats: Dict[str, int] = {
        "total": len(checks),
        "passed": 0,
        "failed": 0,
        "warnings": 0,
        "skipped": 0,
    }
    for cr in checks:
        s = cr.status.upper()
        if s == "PASS":
            stats["passed"] += 1
        elif s == "FAIL":
            stats["failed"] += 1
        elif s == "WARN":
            stats["warnings"] += 1
        elif s == "SKIPPED":
            stats["skipped"] += 1
    return stats

# Defines the public API of this module.
__all__ = [
    "CheckResult",
    "aggregate_findings_to_checks",
    "compute_check_stats",
    "is_diagnostic",
    "get_check_key",
    "resolve_conflicts",
]

# Import here to avoid circular imports
from ams.core.models import Finding, FindingCategory, Severity 

_conflict_logger = logging.getLogger(__name__ + ".arbitration") # Logger for conflict resolution

# Function to resolve conflicts between Static and Visual findings.
def resolve_conflicts(findings: List[Finding]) -> List[Finding]:
    """Resolve conflicts between Static and Visual findings."""
    if not findings:
        return []

    # Classify findings into visual, static, and other categories for conflict resolution.
    visual_findings: Dict[str, List[Finding]] = defaultdict(list)
    static_findings: List[Finding] = []
    other_findings: List[Finding] = []
    
    # Characterise findings by source and ID patterns
    for finding in findings:
        if finding.id.startswith("VISUAL."):
            base_rule_id = finding.id[len("VISUAL."):]
            visual_findings[base_rule_id].append(finding)
        elif finding.category == "visual" or finding.finding_category == FindingCategory.VISUAL:
            original_rule = None
            if isinstance(finding.evidence, dict):
                original_rule = finding.evidence.get("original_rule")
            if original_rule:
                visual_findings[original_rule].append(finding)
            else:
                other_findings.append(finding)
        else:
            static_findings.append(finding)

    resolved: List[Finding] = []
    overridden_static_ids: set = set()

    # For each visual finding, check for corresponding static findings and apply conflict resolution logic.
    for base_rule_id, v_findings in visual_findings.items():
        matching_static = [f for f in static_findings if f.id == base_rule_id]

        # If there is a matching static finding that passed but the visual finding failed, 
        # we consider the visual finding to override the static one.
        if matching_static:
            for static_finding in matching_static:
                static_passed = static_finding.severity == Severity.INFO
                visual_failed = any(
                    f.severity in (Severity.FAIL, Severity.WARN) for f in v_findings
                )
                if static_passed and visual_failed:
                    _conflict_logger.info(
                        f"Conflict resolution: VISUAL.{base_rule_id} overrides static pass. "
                        f"Code exists but visual check failed."
                    )
                    overridden_static_ids.add(id(static_finding))
            resolved.extend(v_findings)
        else:
            resolved.extend(v_findings)

    # Add back static findings that were not overridden by visual findings.
    for static_finding in static_findings:
        if id(static_finding) not in overridden_static_ids:
            resolved.append(static_finding)

    # Add any other findings that were not classified as visual or static.
    resolved.extend(other_findings)
    _conflict_logger.debug(
        f"Conflict resolution: {len(findings)} input → {len(resolved)} output, "
        f"{len(overridden_static_ids)} overrides"
    )
    return resolved
