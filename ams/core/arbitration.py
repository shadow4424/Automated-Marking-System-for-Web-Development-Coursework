"""Phase D: Conflict Resolution Between Static and Visual Findings.

This module implements conflict resolution logic when Static analysis
and Vision analysis produce contradictory results for the same feature.

Design Policy:
- Visual findings (from screenshots) can OVERRIDE static findings
- If code exists but layout is broken → penalize the visual failure
- Grouped by rule ID prefix to detect related findings
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from ams.core.models import Finding, FindingCategory, Severity

logger = logging.getLogger(__name__)


def resolve_conflicts(findings: List[Finding]) -> List[Finding]:
    """Resolve conflicts between Static and Visual findings.
    
    Policy: Visual failures override static passes for the same feature.
    
    Example:
        - CSS.MEDIA_QUERY passes (code exists)
        - VISUAL.CSS.MEDIA_QUERY fails (layout broken on mobile)
        → Result: Penalize the component by keeping the VISUAL finding
        
    Args:
        findings: List of all findings from both Static and Vision analysis.
        
    Returns:
        List of findings with conflicts resolved.
    """
    if not findings:
        return []
    
    # Separate visual and static findings
    visual_findings: Dict[str, List[Finding]] = defaultdict(list)
    static_findings: Dict[str, Finding] = {}
    other_findings: List[Finding] = []
    
    for finding in findings:
        if finding.id.startswith("VISUAL."):
            # Extract the base rule ID: "VISUAL.CSS.MEDIA_QUERY" → "CSS.MEDIA_QUERY"
            base_rule_id = finding.id[len("VISUAL."):]
            visual_findings[base_rule_id].append(finding)
        elif finding.category == "visual" or finding.finding_category == FindingCategory.VISUAL:
            # Visual finding without VISUAL. prefix - extract from evidence
            original_rule = None
            if isinstance(finding.evidence, dict):
                original_rule = finding.evidence.get("original_rule")
            if original_rule:
                visual_findings[original_rule].append(finding)
            else:
                other_findings.append(finding)
        else:
            # Static finding - key by ID
            if finding.id not in static_findings:
                static_findings[finding.id] = finding
            else:
                # Keep the more severe finding
                existing = static_findings[finding.id]
                if _severity_rank(finding.severity) > _severity_rank(existing.severity):
                    static_findings[finding.id] = finding
    
    # Resolve conflicts
    resolved: List[Finding] = []
    overridden_static: set = set()
    
    for base_rule_id, v_findings in visual_findings.items():
        # Check if there's a corresponding static finding
        static_finding = static_findings.get(base_rule_id)
        
        if static_finding:
            # Conflict detected: both static and visual findings exist
            static_passed = static_finding.severity == Severity.INFO
            visual_failed = any(f.severity in (Severity.FAIL, Severity.WARN) for f in v_findings)
            
            if static_passed and visual_failed:
                # Visual OVERRIDES static: code exists but doesn't work visually
                logger.info(
                    f"Conflict resolution: VISUAL.{base_rule_id} overrides static pass. "
                    f"Code exists but visual check failed."
                )
                # Mark static as overridden
                overridden_static.add(base_rule_id)
                # Add visual findings as the authoritative result
                resolved.extend(v_findings)
            else:
                # No overriding conflict - keep both
                resolved.extend(v_findings)
        else:
            # No static finding - just keep visual findings
            resolved.extend(v_findings)
    
    # Add static findings that weren't overridden
    for rule_id, static_finding in static_findings.items():
        if rule_id not in overridden_static:
            resolved.append(static_finding)
    
    # Add other findings that don't fit the pattern
    resolved.extend(other_findings)
    
    logger.debug(f"Conflict resolution: {len(findings)} input → {len(resolved)} output, {len(overridden_static)} overrides")
    
    return resolved


def _severity_rank(severity: Severity) -> int:
    """Get numeric rank for severity (higher = more severe)."""
    ranks = {
        Severity.INFO: 0,
        Severity.SKIPPED: 1,
        Severity.WARN: 2,
        Severity.FAIL: 3,
    }
    return ranks.get(severity, 0)


def group_findings_by_component(findings: List[Finding]) -> Dict[str, List[Finding]]:
    """Group findings by their component category.
    
    Useful for calculating per-component scores after conflict resolution.
    
    Args:
        findings: List of resolved findings.
        
    Returns:
        Dict mapping component name to list of findings.
    """
    grouped: Dict[str, List[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.category].append(finding)
    return dict(grouped)


__all__ = [
    "resolve_conflicts",
    "group_findings_by_component",
]
