from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ams.assessors.base import Assessor
from ams.assessors.shared.html_parser import TagCountingParser
from ams.core.finding_ids import HTML as HID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, RequiredHTMLRule, get_profile_spec


class HTMLRequiredElementsAssessor(Assessor):
    """Checks required HTML elements based on profile spec using a real HTML parser."""

    name = "html_required"

    def __init__(self, profile: str | ProfileSpec = "frontend") -> None:
        if isinstance(profile, str):
            self.profile_spec = get_profile_spec(profile)
        else:
            self.profile_spec = profile

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.discovered_files.get("html", []))

        # Check if HTML is required for this profile
        is_required = self.profile_spec.is_component_required("html")
        has_required_rules = self.profile_spec.has_required_rules("html")

        # If no rules are defined for this component, still generate a SKIPPED finding to show it wasn't checked
        if not has_required_rules:
            findings.append(
                Finding(
                    id=HID.REQ_SKIPPED,
                    category="html",
                    message="No HTML checks defined for this profile.",
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

        # If HTML is not required for this profile, generate per-rule SKIPPED findings
        if not is_required:
            for rule in self.profile_spec.required_html:
                findings.append(
                    Finding(
                        id=HID.REQ_SKIPPED,
                        category="html",
                        message=f"Rule '{rule.id}' skipped: HTML not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "rule_id": rule.id,
                            "description": rule.description,
                            "selector": rule.selector,
                            "weight": rule.weight,
                            "skip_reason": "component_not_required",
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=self.profile_spec.name,
                        required=False,
                    )
                )
            return findings

        if not html_files:
            if is_required and has_required_rules:
                # Required for profile but missing - generate per-rule FAIL findings
                for rule in self.profile_spec.required_html:
                    findings.append(
                        Finding(
                            id=HID.REQ_MISSING_FILES,
                            category="html",
                            message=f"Rule '{rule.id}' not evaluated: No HTML files found in submission.",
                            severity=Severity.FAIL,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "selector": rule.selector,
                                "weight": rule.weight,
                                "skip_reason": "no_html_files_found",
                                "expected_selectors": [rule.selector],
                                "profile": self.profile_spec.name,
                                "required": True,
                            },
                            source=self.name,
                            finding_category=FindingCategory.MISSING,
                            profile=self.profile_spec.name,
                            required=True,
                        )
                    )
            else:
                # Not required or no rules defined - generate per-rule SKIPPED findings
                for rule in self.profile_spec.required_html:
                    findings.append(
                        Finding(
                            id=HID.REQ_SKIPPED,
                            category="html",
                            message=f"Rule '{rule.id}' not evaluated: {rule.description}. HTML not required for this profile or no files found.",
                            severity=Severity.SKIPPED,
                            evidence={
                                "rule_id": rule.id,
                                "description": rule.description,
                                "selector": rule.selector,
                                "weight": rule.weight,
                                "skip_reason": "component_not_required" if not is_required else "no_files_found",
                                "profile": self.profile_spec.name,
                                "required": is_required,
                                "has_required_rules": has_required_rules,
                            },
                            source=self.name,
                            finding_category=FindingCategory.OTHER,
                            profile=self.profile_spec.name,
                            required=is_required,
                        )
                    )
            return findings

        for path in html_files:
            content = self._read_file(path)
            parser = TagCountingParser()
            parser.feed(content)
            for rule in self.profile_spec.required_html:
                count, passed = self._evaluate_rule(rule, parser)
                # Extract snippet for evidence
                snippet = self._extract_snippet(content, rule.selector, rule.id)
                
                finding_id = HID.REQ_PASS if passed else HID.REQ_FAIL
                severity = Severity.INFO if passed else Severity.WARN
                findings.append(
                    Finding(
                        id=finding_id,
                        category="html",
                        message=self._build_message(rule, passed, count),
                        severity=severity,
                        evidence={
                            "path": str(path),
                            "rule_id": rule.id,
                            "selector": rule.selector,
                            "min_count": rule.min_count,
                            "count": count,
                            "weight": rule.weight,
                            "snippet": snippet,
                            "content": content[:500],
                        },
                        source=self.name,
                    )
                )
        return findings

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _extract_snippet(self, content: str, selector: str, rule_id: str) -> str:
        """Extract a relevant HTML snippet around the matched selector."""
        if not content or not content.strip():
            return "(file is empty)"
        
        lines = content.splitlines()
        selector_lower = selector.lower()
        
        # Build search terms from the selector
        # e.g., "form" -> "<form", "input[type=text]" -> "<input"
        tag_name = selector_lower.split("[")[0].split(".")[0].split("#")[0].strip()
        search_term = f"<{tag_name}" if tag_name else selector_lower
        
        # Find first matching line
        for i, line in enumerate(lines):
            if search_term in line.lower():
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                snippet_lines = []
                for j in range(start, end):
                    snippet_lines.append(f"{j + 1:>4} | {lines[j]}")
                return "\n".join(snippet_lines)
        
        # No match — show first 10 lines as context
        preview_lines = []
        for j in range(min(10, len(lines))):
            preview_lines.append(f"{j + 1:>4} | {lines[j]}")
        return "\n".join(preview_lines)

    def _evaluate_rule(
        self, rule: RequiredHTMLRule, parser: TagCountingParser
    ) -> tuple[int, bool]:
        """Evaluate a single rule against the parsed HTML content.
        
        Returns:
            A tuple of (count, passed) where count is the number of matches found
            and passed indicates whether the rule requirement is satisfied.
        """
        selector = rule.selector.lower()

        # === DOCTYPE ===
        if selector == "!doctype" or rule.id == "html.has_doctype":
            count = 1 if parser.has_doctype else 0
            return count, count >= rule.min_count

        # === SEMANTIC STRUCTURE ===
        if selector == "semantic" or rule.id == "html.has_semantic_structure":
            count = 1 if parser.has_semantic else 0
            return count, count >= rule.min_count
        
        # === HEADING HIERARCHY ===
        if selector == "heading" or rule.id == "html.has_heading_hierarchy":
            count = 1 if parser.has_heading else 0
            return count, count >= rule.min_count
        
        # === LIST ELEMENTS ===
        if selector == "list" or rule.id == "html.has_lists":
            count = 1 if parser.has_list else 0
            return count, count >= rule.min_count
        
        # === META CHARSET ===
        if selector == "meta_charset" or rule.id == "html.has_meta_charset":
            count = 1 if parser.has_meta_charset else 0
            return count, count >= rule.min_count
        
        # === META VIEWPORT ===
        if selector == "meta_viewport" or rule.id == "html.has_meta_viewport":
            count = 1 if parser.has_meta_viewport else 0
            return count, count >= rule.min_count
        
        # === HTML LANG ATTRIBUTE ===
        if selector == "html_lang" or rule.id == "html.has_lang_attribute":
            count = 1 if parser.has_html_lang else 0
            return count, count >= rule.min_count
        
        # === IMAGE ALT ATTRIBUTES ===
        if selector == "img_alt" or rule.id == "html.has_alt_attributes":
            # Pass if all images have alt attributes, or if no images exist
            if parser.img_count == 0:
                return 1, True  # No images means requirement is satisfied
            count = parser.img_with_alt
            passed = parser.img_with_alt == parser.img_count
            return count, passed
        
        # === LABELS ===
        if selector == "label" or rule.id == "html.has_labels":
            count = parser.label_count
            return count, count >= rule.min_count
        
        # === STANDARD TAG COUNTING ===
        # For simple selectors like html, head, body, title, form, input, a
        count = parser.counts.get(selector, 0)
        return count, count >= rule.min_count

    def _build_message(self, rule: RequiredHTMLRule, passed: bool, count: int) -> str:
        status = "PASS" if passed else "FAIL"
        return f"Rule {rule.id} ({rule.description}) {status}: found {count}, required {rule.min_count}"


__all__ = ["HTMLRequiredElementsAssessor"]
