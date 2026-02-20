"""Base class for language-specific required rule assessors (Phase 3: DRY).

Consolidates common patterns across HTMLRequiredElementsAssessor,
CSSRequiredRulesAssessor, JSRequiredFeaturesAssessor, PHPRequiredFeaturesAssessor,
and SQLRequiredFeaturesAssessor.

Extracted Patterns:
- File reading with error handling
- Snippet extraction from content
- Finding creation (eliminates ~100 lines of duplication)
- Skipped/missing file finding generation
"""
from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import List, Tuple

from ams.assessors import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, RequiredRule, get_profile_spec


class BaseRequiredAssessor(Assessor):
    """Abstract base for all required rule assessors (HTML, CSS, JS, PHP, SQL).
    
    Consolidates common patterns:
    - File discovery and reading
    - Snippet extraction
    - Finding creation
    - Missing file / not required handling
    
    Subclasses must implement:
    - `component_name` property: e.g., "html", "css", "js"
    - `required_rules` property: return rules from profile_spec
    - `_evaluate_rule_impl()`: Rule evaluation logic, return (count, passed)
    - `_build_message()`: Human-readable rule message
    - `_get_finding_id_pass()`, `_get_finding_id_fail()`: Finding IDs
    """
    
    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile
    
    @property
    @abstractmethod
    def component_name(self) -> str:
        """E.g., "html", "css", "js", "php", "sql"."""
        pass
    
    @property
    @abstractmethod
    def required_rules(self) -> List[RequiredRule]:
        """Return list of required rules for this component from profile_spec."""
        pass
    
    @abstractmethod
    def _evaluate_rule_impl(self, rule: RequiredRule, content: str) -> Tuple[int, bool]:
        """Evaluate single rule. Return (occurrence_count, passed: bool)."""
        pass
    
    @abstractmethod
    def _build_message(self, rule: RequiredRule, passed: bool, count: int) -> str:
        """Build human-readable message for this rule outcome."""
        pass
    
    @abstractmethod
    def _get_finding_id_pass(self) -> str:
        """Return finding ID for passing rule (e.g., 'HTML.REQ_PASS')."""
        pass
    
    @abstractmethod
    def _get_finding_id_fail(self) -> str:
        """Return finding ID for failing rule (e.g., 'HTML.REQ_FAIL')."""
        pass
    
    @abstractmethod
    def _get_finding_id_skipped(self) -> str:
        """Return finding ID for skipped rule."""
        pass
    
    @abstractmethod
    def _get_finding_id_missing_files(self) -> str:
        """Return finding ID when required files are missing."""
        pass
    
    def run(self, context: SubmissionContext) -> List[Finding]:
        """Unified pipeline for all required assessors.
        
        Handles:
        - Component not required → per-rule SKIPPED findings
        - No rules defined → single SKIPPED finding
        - No files found → per-rule FAIL or SKIPPED findings
        - Files found → evaluate each rule, generate appropriate findings
        """
        findings: List[Finding] = []
        
        # Check if component is required and has rules
        is_required = self.profile_spec.is_component_required(self.component_name)
        has_rules = self.profile_spec.has_required_rules(self.component_name)
        
        # If no rules defined, return SKIPPED finding
        if not has_rules:
            findings.append(
                Finding(
                    id=self._get_finding_id_skipped(),
                    category=self.component_name,
                    message=f"No {self.component_name.upper()} checks defined for this profile.",
                    severity=Severity.SKIPPED,
                    evidence={
                        "profile": self.profile_spec.name,
                        "skip_reason": "no_rules_defined",
                    },
                    source=self.name,
                    finding_category=FindingCategory.OTHER,
                    profile=self.profile_spec.name,
                    required=is_required,
                )
            )
            return findings
        
        # If component not required, return per-rule SKIPPED findings
        if not is_required:
            for rule in self.required_rules:
                findings.append(
                    Finding(
                        id=self._get_finding_id_skipped(),
                        category=self.component_name,
                        message=f"Rule '{rule.id}' skipped: {self.component_name.upper()} not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_id": rule.id,
                            "description": getattr(rule, "description", ""),
                            "selector": getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                            "weight": getattr(rule, "weight", 0),
                            "skip_reason": "component_not_required",
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=False,
                    )
                )
            return findings
        
        # Get files for this component
        files = sorted(context.discovered_files.get(self.component_name, []))
        
        # No files found → generate per-rule FAIL findings (required component missing files)
        if not files:
            for rule in self.required_rules:
                findings.append(
                    Finding(
                        id=self._get_finding_id_missing_files(),
                        category=self.component_name,
                        message=f"Rule '{rule.id}' not evaluated: No {self.component_name.upper()} files found in submission.",
                        severity=Severity.FAIL,
                        evidence={
                            "rule_id": rule.id,
                            "description": getattr(rule, "description", ""),
                            "selector": getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                            "weight": getattr(rule, "weight", 0),
                            "skip_reason": "no_files_found",
                            "profile": self.profile_spec.name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=self.profile_spec.name,
                        required=True,
                    )
                )
            return findings
        
        # Evaluate each file and rule
        for path in files:
            content = self._read_file_safe(path)
            for rule in self.required_rules:
                count, passed = self._evaluate_rule_impl(rule, content)
                snippet = self._extract_snippet(
                    content,
                    getattr(rule, "selector", "") or getattr(rule, "needle", ""),
                    rule.id,
                )
                
                finding = self._create_finding(
                    rule=rule,
                    path=path,
                    passed=passed,
                    count=count,
                    snippet=snippet,
                    content=content,
                )
                findings.append(finding)
        
        return findings
    
    # ========================================================================
    # Consolidated Helpers (eliminates ~250 lines of duplication)
    # ========================================================================
    
    def _read_file_safe(self, path: Path) -> str:
        """Read file with error handling."""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    
    def _extract_snippet(
        self,
        content: str,
        needle: str,
        rule_id: str,
        context_lines: int = 2,
    ) -> str:
        """Extract relevant code snippet around needle/selector.
        
        Args:
            content: File content.
            needle: Search term (e.g., HTML tag name, CSS property).
            rule_id: Rule ID for context in error messages.
            context_lines: Lines before/after match to include.
            
        Returns:
            Formatted snippet string.
        """
        if not content or not content.strip():
            return "(file is empty)"
        
        lines = content.splitlines()
        needle_lower = needle.lower()
        
        # Handle CSS/HTML selectors: extract tag name
        # e.g., "form", "input[type=text]", ".container" → search for tag
        tag_name = needle_lower.split("[")[0].split(".")[0].split("#")[0].strip()
        search_term = f"<{tag_name}" if tag_name and self._is_html_like() else needle_lower
        
        # Find first matching line
        for i, line in enumerate(lines):
            if search_term in line.lower():
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet_lines = [f"{j + 1:>4} | {lines[j]}" for j in range(start, end)]
                return "\n".join(snippet_lines)
        
        # No match — show first 10 lines as context
        preview_lines = [f"{j + 1:>4} | {lines[j]}" for j in range(min(10, len(lines)))]
        return "\n".join(preview_lines)
    
    def _is_html_like(self) -> bool:
        """Return True if this assessor deals with HTML-like content."""
        return self.component_name in ("html", "php")
    
    def _create_finding(
        self,
        rule: RequiredRule,
        path: Path,
        passed: bool,
        count: int,
        snippet: str,
        content: str,
    ) -> Finding:
        """Unified Finding creation (eliminates ~20 lines per assessor).
        
        Args:
            rule: The RequiredRule being evaluated.
            path: Path to the file being assessed.
            passed: Whether the rule passed.
            count: Number of occurrences found.
            snippet: Code snippet for evidence.
            content: Full file content (truncated for evidence).
            
        Returns:
            Constructed Finding object.
        """
        finding_id = self._get_finding_id_pass() if passed else self._get_finding_id_fail()
        severity = Severity.INFO if passed else Severity.WARN
        
        # Extract rule selector/needle for evidence (generic approach)
        selector = getattr(rule, "selector", None) or getattr(rule, "needle", "unknown")
        
        return Finding(
            id=finding_id,
            category=self.component_name,
            message=self._build_message(rule, passed, count),
            severity=severity,
            evidence={
                "path": str(path),
                "rule_id": rule.id,
                "selector": selector,
                "min_count": getattr(rule, "min_count", 0),
                "count": count,
                "weight": getattr(rule, "weight", 0),
                "snippet": snippet,
                "content": content[:500],
            },
            source=self.name,
            finding_category=FindingCategory.STRUCTURE if passed else FindingCategory.MISSING,
            profile=self.profile_spec.name,
            required=True,
        )


__all__ = ["BaseRequiredAssessor"]
