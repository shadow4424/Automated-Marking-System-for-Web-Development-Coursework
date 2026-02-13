"""Aggregate raw findings into logical rubric checks.

The pipeline emits one Finding per rule evaluation, per occurrence, and per
diagnostic event.  The UI should report *distinct logical checks* rather than
raw event counts.  This module provides the translation layer.

Key concepts
------------
* **Check** – one rubric-aligned unit (e.g., ``sql.has_insert``).
* **Diagnostic** – a runtime/infrastructure event that is informational but
  does *not* represent a rubric check (e.g., ``BROWSER.PAGE_LOAD_PASS``).
* **Enrichment** – LLM / Vision meta-events (``VISUAL.*``); not checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
        "HTML.ELEMENT_EVIDENCE",
        "CSS.EVIDENCE",
        "JS.EVIDENCE",
        "PHP.EVIDENCE",
        "SQL.EVIDENCE",
    }
)

# Static assessor structural findings – informational, not rubric checks.
_STATIC_DIAGNOSTIC_IDS: frozenset[str] = frozenset(
    {
        "HTML.PARSE_OK",
        "HTML.PARSE_SUSPECT",
        "CSS.BRACES_BALANCED",
        "CSS.BRACES_UNBALANCED",
        "JS.SYNTAX_OK",
        "JS.SYNTAX_SUSPECT",
        "PHP.TAG_OK",
        "PHP.TAG_MISSING",
        "PHP.SYNTAX_OK",
        "PHP.SYNTAX_SUSPECT",
        "SQL.STRUCTURE_OK",
        "SQL.STRUCTURE_SUSPECT",
    }
)


# ---------------------------------------------------------------------------
# CheckResult data class
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Aggregated result for a single logical rubric check."""

    check_id: str
    component: str          # html / css / js / php / sql / consistency / behavioral
    status: str             # PASS / WARN / FAIL / SKIPPED
    occurrences: int = 1
    weight: Optional[float] = None
    messages: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _severity_to_status(severity: str) -> str:
    """Map a Finding severity string to a CheckResult status."""
    s = severity.upper()
    if s == "INFO":
        return "PASS"
    if s in ("WARN", "FAIL", "SKIPPED"):
        return s
    return "PASS"


def is_diagnostic(finding: dict) -> bool:
    """Return True if *finding* is a diagnostic/infrastructure event.

    Diagnostic events are kept for transparency but do **not** count toward
    the rubric check totals.
    """
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


def get_check_key(finding: dict) -> str:
    """Derive a stable, unique check key for aggregation.

    For required-assessor findings (``HTML.REQ.PASS``, ``SQL.REQ.FAIL``, …)
    the actual rule identity lives in ``evidence.rule_id``.  We use that to
    get one check per rubric rule.

    For consistency findings the key is the finding *type* ID (e.g.
    ``CONSISTENCY.CSS_MISSING_HTML_ID``), **not** the per-occurrence
    selector_value.

    For everything else the finding ``id`` itself is the key.
    """
    fid: str = finding.get("id", "")
    evidence: dict = finding.get("evidence", {}) or {}

    # Required assessor findings carry rule identity in evidence
    rule_id = evidence.get("rule_id")
    if rule_id:
        return str(rule_id)

    return fid


# ---------------------------------------------------------------------------
# Status merging
# ---------------------------------------------------------------------------

_STATUS_PRIORITY = {"SKIPPED": 0, "PASS": 1, "WARN": 2, "FAIL": 3}


def _merge_status(existing: str, incoming: str) -> str:
    """Merge two statuses; the more severe one wins.

    Priority: FAIL > WARN > PASS > SKIPPED.
    """
    return existing if _STATUS_PRIORITY.get(existing, 0) >= _STATUS_PRIORITY.get(incoming, 0) else incoming


# ---------------------------------------------------------------------------
# Main aggregation function
# ---------------------------------------------------------------------------

def aggregate_findings_to_checks(
    findings: List[dict],
) -> tuple[List[CheckResult], List[dict]]:
    """Aggregate raw findings into logical rubric checks.

    Parameters
    ----------
    findings:
        List of serialised finding dicts (as stored in ``report.json``).

    Returns
    -------
    (checks, diagnostics)
        *checks* – one ``CheckResult`` per distinct rubric check.
        *diagnostics* – findings classified as diagnostic/enrichment events.
    """
    checks_map: Dict[str, CheckResult] = {}
    diagnostics: List[dict] = []

    for f in findings:
        if is_diagnostic(f):
            diagnostics.append(f)
            continue

        key = get_check_key(f)
        severity = f.get("severity", "INFO")
        status = _severity_to_status(severity)
        component = f.get("category", "unknown")
        evidence = f.get("evidence", {}) or {}
        message = f.get("message", "")
        source = f.get("source", "")
        weight = evidence.get("weight")
        if weight is not None:
            try:
                weight = float(weight)
            except (TypeError, ValueError):
                weight = None

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


def compute_check_stats(checks: List[CheckResult]) -> Dict[str, int]:
    """Compute summary statistics from aggregated checks.

    Returns a dict with keys: total, passed, failed, warnings, skipped.
    """
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


__all__ = [
    "CheckResult",
    "aggregate_findings_to_checks",
    "compute_check_stats",
    "is_diagnostic",
    "get_check_key",
]
