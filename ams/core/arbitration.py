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
from typing import Dict, List

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
    static_findings: List[Finding] = []
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
            # Static finding - preserve ALL findings, don't deduplicate by ID
            # Multiple findings can share the same ID but represent different rules
            # (e.g., HTML.REQ.PASS is used for all passing HTML rules)
            static_findings.append(finding)
    
    # Resolve conflicts
    resolved: List[Finding] = []
    overridden_static_ids: set = set()
    
    # Process visual findings and check for conflicts with static findings
    for base_rule_id, v_findings in visual_findings.items():
        # Check if there are corresponding static findings with this base rule ID
        matching_static = [f for f in static_findings if f.id == base_rule_id]
        
        if matching_static:
            # Check for actual conflict: static PASS vs visual FAIL
            for static_finding in matching_static:
                static_passed = static_finding.severity == Severity.INFO
                visual_failed = any(f.severity in (Severity.FAIL, Severity.WARN) for f in v_findings)
                
                if static_passed and visual_failed:
                    # Visual OVERRIDES static: code exists but doesn't work visually
                    logger.info(
                        f"Conflict resolution: VISUAL.{base_rule_id} overrides static pass. "
                        f"Code exists but visual check failed."
                    )
                    # Mark this specific static finding as overridden using its identity
                    overridden_static_ids.add(id(static_finding))
            
            # Add all visual findings for this rule
            resolved.extend(v_findings)
        else:
            # No static finding - just keep visual findings
            resolved.extend(v_findings)
    
    # Add static findings that weren't overridden
    for static_finding in static_findings:
        if id(static_finding) not in overridden_static_ids:
            resolved.append(static_finding)
    
    # Add other findings that don't fit the pattern
    resolved.extend(other_findings)
    
    logger.debug(f"Conflict resolution: {len(findings)} input → {len(resolved)} output, {len(overridden_static_ids)} overrides")
    
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
