"""Cross-file consistency checks for HTML, CSS, JS, and PHP."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Set

from ams.assessors.base import Assessor
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class _HTMLElementExtractor(HTMLParser):
    """Extract IDs and classes from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: Set[str] = set()
        self.classes: Set[str] = set()
        self.form_fields: Dict[str, str] = {}  # name -> form context
        self.links: List[tuple[str, str]] = []  # (href, referring_file)
        self.form_actions: List[tuple[str, str]] = []  # (action, referring_file)
        self.current_form_context: str | None = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        
        # Extract IDs
        if "id" in attrs_dict and attrs_dict["id"]:
            self.ids.add(attrs_dict["id"])
        
        # Extract classes
        if "class" in attrs_dict and attrs_dict["class"]:
            for cls in attrs_dict["class"].split():
                if cls:
                    self.classes.add(cls)
        
        # Extract form fields
        if tag in ("input", "select", "textarea"):
            if "name" in attrs_dict and attrs_dict["name"]:
                field_name = attrs_dict["name"]
                form_context = self.current_form_context or "unknown"
                self.form_fields[field_name] = form_context
        
        # Track form context
        if tag == "form":
            self.current_form_context = attrs_dict.get("action", "default")
        
        # Extract links
        if tag == "a" and "href" in attrs_dict:
            href = attrs_dict["href"]
            if href and not href.startswith(("http://", "https://", "mailto:", "#")):
                self.links.append((href, ""))
        
        # Extract form actions
        if tag == "form" and "action" in attrs_dict:
            action = attrs_dict["action"]
            if action and not action.startswith(("http://", "https://", "mailto:", "#")):
                self.form_actions.append((action, ""))


class ConsistencyAssessor(Assessor):
    """Cross-file consistency checks."""

    name = "consistency"

    def run(self, context: SubmissionContext) -> List[Finding]:
        """Run all consistency checks."""
        findings: List[Finding] = []
        profile_name = context.metadata.get("profile", "frontend")
        
        try:
            profile_spec = get_profile_spec(profile_name)
        except ValueError:
            profile_spec = None

        # Extract HTML data
        html_data = self._extract_html_data(context)
        
        # B1: HTML ↔ JS DOM selector consistency
        if profile_spec and (profile_spec.is_component_required("html") or profile_spec.is_component_required("js")):
            findings.extend(self._check_js_html_consistency(context, html_data, profile_name))
        
        # B2: HTML ↔ CSS selector consistency
        if profile_spec and (profile_spec.is_component_required("html") or profile_spec.is_component_required("css")):
            findings.extend(self._check_css_html_consistency(context, html_data, profile_name))
        
        # B3: Form field ↔ PHP variable consistency (fullstack only)
        if profile_name == "fullstack":
            findings.extend(self._check_php_form_consistency(context, html_data, profile_name))
        
        # B4: Link/action target existence
        findings.extend(self._check_link_targets(context, html_data, profile_name))
        
        return findings

    def _extract_html_data(self, context: SubmissionContext) -> Dict[str, object]:
        """Extract IDs, classes, form fields, and links from HTML files."""
        html_files = sorted(context.discovered_files.get("html", []))
        all_ids: Set[str] = set()
        all_classes: Set[str] = set()
        all_form_fields: Dict[str, str] = {}
        all_links: List[tuple[str, str]] = []
        all_form_actions: List[tuple[str, str]] = []
        
        # Get submission root for resolving relative paths
        submission_root = Path(context.metadata.get("resolved_root", context.workspace_path / "submission"))
        
        for html_file in html_files:
            try:
                content = html_file.read_text(encoding="utf-8", errors="replace")
                parser = _HTMLElementExtractor()
                parser.feed(content)
                
                all_ids.update(parser.ids)
                all_classes.update(parser.classes)
                all_form_fields.update(parser.form_fields)
                
                # Store file context for links/actions
                html_file_str = str(html_file)
                for href, _ in parser.links:
                    all_links.append((href, html_file_str))
                for action, _ in parser.form_actions:
                    all_form_actions.append((action, html_file_str))
            except Exception:
                # Ignore parse errors, continue with other files
                pass
        
        return {
            "ids": all_ids,
            "classes": all_classes,
            "form_fields": all_form_fields,
            "links": all_links,
            "form_actions": all_form_actions,
            "submission_root": submission_root,
        }

    def _check_js_html_consistency(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check JS DOM selectors against HTML IDs and classes."""
        findings: List[Finding] = []
        js_files = sorted(context.discovered_files.get("js", []))
        html_ids: Set[str] = html_data.get("ids", set())
        html_classes: Set[str] = html_data.get("classes", set())
        
        # Patterns for JS DOM selector calls
        id_patterns = [
            r'getElementById\s*\(\s*["\']([^"\']+)["\']',
            r'querySelector\s*\(\s*["\']#([^"\']+)["\']',
            r'querySelectorAll\s*\(\s*["\']#([^"\']+)["\']',
        ]
        class_patterns = [
            r'querySelector\s*\(\s*["\']\.([^"\']+)["\']',
            r'querySelectorAll\s*\(\s*["\']\.([^"\']+)["\']',
        ]
        
        for js_file in js_files:
            try:
                content = js_file.read_text(encoding="utf-8", errors="replace")
                
                # Check IDs
                for pattern in id_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        selector_value = match.group(1)
                        if selector_value and selector_value not in html_ids:
                            # Get context snippet
                            start = max(0, match.start() - 20)
                            end = min(len(content), match.end() + 20)
                            snippet = content[start:end].replace("\n", " ").strip()
                            
                            findings.append(
                                Finding(
                                    id="CONSISTENCY.JS_MISSING_HTML_ID",
                                    category="consistency",
                                    message=f"JS references HTML ID '{selector_value}' that does not exist in HTML.",
                                    severity=Severity.WARN,
                                    evidence={
                                        "selector_type": "id",
                                        "selector_value": selector_value,
                                        "js_file": str(js_file),
                                        "snippet": snippet,
                                    },
                                    source=self.name,
                                    finding_category=FindingCategory.STRUCTURE,
                                    profile=profile_name,
                                )
                            )
                
                # Check classes
                for pattern in class_patterns:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        selector_value = match.group(1)
                        if selector_value and selector_value not in html_classes:
                            # Get context snippet
                            start = max(0, match.start() - 20)
                            end = min(len(content), match.end() + 20)
                            snippet = content[start:end].replace("\n", " ").strip()
                            
                            findings.append(
                                Finding(
                                    id="CONSISTENCY.JS_MISSING_HTML_CLASS",
                                    category="consistency",
                                    message=f"JS references HTML class '{selector_value}' that does not exist in HTML.",
                                    severity=Severity.WARN,
                                    evidence={
                                        "selector_type": "class",
                                        "selector_value": selector_value,
                                        "js_file": str(js_file),
                                        "snippet": snippet,
                                    },
                                    source=self.name,
                                    finding_category=FindingCategory.STRUCTURE,
                                    profile=profile_name,
                                )
                            )
            except Exception:
                # Ignore parse errors
                pass
        
        return findings

    def _check_css_html_consistency(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check CSS selectors against HTML IDs and classes."""
        findings: List[Finding] = []
        css_files = sorted(context.discovered_files.get("css", []))
        html_ids: Set[str] = html_data.get("ids", set())
        html_classes: Set[str] = html_data.get("classes", set())
        
        # Simple patterns: #id and .class (at start of selector or after space/comma)
        id_pattern = r'(?:^|[\s,])#([a-zA-Z_][a-zA-Z0-9_-]*)'
        class_pattern = r'(?:^|[\s,])\.([a-zA-Z_][a-zA-Z0-9_-]*)'
        
        for css_file in css_files:
            try:
                content = css_file.read_text(encoding="utf-8", errors="replace")
                
                # Check IDs
                for match in re.finditer(id_pattern, content):
                    selector_value = match.group(1)
                    if selector_value and selector_value not in html_ids:
                        # Count occurrences
                        count = content.count(f"#{selector_value}")
                        
                        findings.append(
                            Finding(
                                id="CONSISTENCY.CSS_MISSING_HTML_ID",
                                category="consistency",
                                message=f"CSS references HTML ID '#{selector_value}' that does not exist in HTML.",
                                severity=Severity.WARN,
                                evidence={
                                    "selector_value": selector_value,
                                    "css_file": str(css_file),
                                    "count_occurrences": count,
                                },
                                source=self.name,
                                finding_category=FindingCategory.STRUCTURE,
                                profile=profile_name,
                            )
                        )
                
                # Check classes
                for match in re.finditer(class_pattern, content):
                    selector_value = match.group(1)
                    if selector_value and selector_value not in html_classes:
                        # Count occurrences
                        count = content.count(f".{selector_value}")
                        
                        findings.append(
                            Finding(
                                id="CONSISTENCY.CSS_MISSING_HTML_CLASS",
                                category="consistency",
                                message=f"CSS references HTML class '.{selector_value}' that does not exist in HTML.",
                                severity=Severity.WARN,
                                evidence={
                                    "selector_value": selector_value,
                                    "css_file": str(css_file),
                                    "count_occurrences": count,
                                },
                                source=self.name,
                                finding_category=FindingCategory.STRUCTURE,
                                profile=profile_name,
                            )
                        )
            except Exception:
                # Ignore parse errors
                pass
        
        return findings

    def _check_php_form_consistency(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check PHP form variable access against HTML form fields."""
        findings: List[Finding] = []
        php_files = sorted(context.discovered_files.get("php", []))
        html_form_fields: Set[str] = set(html_data.get("form_fields", {}).keys())
        
        # Patterns for PHP form variable access
        post_pattern = r'\$_POST\s*\[\s*["\']([^"\']+)["\']'
        get_pattern = r'\$_GET\s*\[\s*["\']([^"\']+)["\']'
        request_pattern = r'\$_REQUEST\s*\[\s*["\']([^"\']+)["\']'
        
        php_accessed_keys: Set[str] = set()
        php_file_map: Dict[str, str] = {}  # key -> file
        
        for php_file in php_files:
            try:
                content = php_file.read_text(encoding="utf-8", errors="replace")
                
                for pattern in [post_pattern, get_pattern, request_pattern]:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        key = match.group(1)
                        if key:
                            php_accessed_keys.add(key)
                            php_file_map[key] = str(php_file)
            except Exception:
                # Ignore parse errors
                pass
        
        # Check: PHP expects key that doesn't exist in HTML
        for key in php_accessed_keys:
            if key not in html_form_fields:
                findings.append(
                    Finding(
                        id="CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD",
                        category="consistency",
                        message=f"PHP accesses form field '{key}' that is not defined in HTML forms.",
                        severity=Severity.WARN,
                        evidence={
                            "field_name": key,
                            "php_file": php_file_map.get(key, "unknown"),
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                        profile=profile_name,
                    )
                )
        
        # Check: HTML form field not used in PHP (lower priority)
        for field_name in html_form_fields:
            if field_name not in php_accessed_keys:
                findings.append(
                    Finding(
                        id="CONSISTENCY.FORM_FIELD_UNUSED_IN_PHP",
                        category="consistency",
                        message=f"HTML form field '{field_name}' is not accessed in PHP code.",
                        severity=Severity.INFO,
                        evidence={
                            "field_name": field_name,
                            "form_context": html_data.get("form_fields", {}).get(field_name, "unknown"),
                        },
                        source=self.name,
                        finding_category=FindingCategory.EVIDENCE,
                        profile=profile_name,
                    )
                )
        
        return findings

    def _check_link_targets(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check that link and form action targets exist."""
        findings: List[Finding] = []
        
        # Get submission root from html_data or context
        submission_root = html_data.get("submission_root")
        if not submission_root:
            submission_root = Path(context.metadata.get("resolved_root", context.workspace_path / "submission"))
        if isinstance(submission_root, str):
            submission_root = Path(submission_root)
        
        # Check links
        links = html_data.get("links", [])
        for href, referring_file in links:
            # Remove anchor fragments
            target = href.split("#")[0]
            if not target:
                continue
            
            # Resolve relative path
            if referring_file:
                base_dir = Path(referring_file).parent
            else:
                base_dir = submission_root
            
            target_path = (base_dir / target).resolve()
            
            # Check if file exists within submission
            try:
                if not target_path.exists() or not str(target_path).startswith(str(submission_root.resolve())):
                    findings.append(
                        Finding(
                            id="CONSISTENCY.MISSING_LINK_TARGET",
                            category="consistency",
                            message=f"Link target '{href}' does not exist.",
                            severity=Severity.WARN,
                            evidence={
                                "target": href,
                                "referring_file": referring_file or "unknown",
                            },
                            source=self.name,
                            finding_category=FindingCategory.STRUCTURE,
                            profile=profile_name,
                        )
                    )
            except Exception:
                # Path resolution failed, emit warning
                findings.append(
                    Finding(
                        id="CONSISTENCY.MISSING_LINK_TARGET",
                        category="consistency",
                        message=f"Link target '{href}' could not be resolved.",
                        severity=Severity.WARN,
                        evidence={
                            "target": href,
                            "referring_file": referring_file or "unknown",
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                        profile=profile_name,
                    )
                )
        
        # Check form actions
        form_actions = html_data.get("form_actions", [])
        for action, referring_file in form_actions:
            # Remove anchor fragments
            target = action.split("#")[0]
            if not target:
                continue
            
            # Resolve relative path
            if referring_file:
                base_dir = Path(referring_file).parent
            else:
                base_dir = submission_root
            
            target_path = (base_dir / target).resolve()
            
            # Check if file exists within submission
            try:
                if not target_path.exists() or not str(target_path).startswith(str(submission_root.resolve())):
                    findings.append(
                        Finding(
                            id="CONSISTENCY.MISSING_FORM_ACTION_TARGET",
                            category="consistency",
                            message=f"Form action target '{action}' does not exist.",
                            severity=Severity.WARN,
                            evidence={
                                "target": action,
                                "referring_file": referring_file or "unknown",
                            },
                            source=self.name,
                            finding_category=FindingCategory.STRUCTURE,
                            profile=profile_name,
                        )
                    )
            except Exception:
                # Path resolution failed
                findings.append(
                    Finding(
                        id="CONSISTENCY.MISSING_FORM_ACTION_TARGET",
                        category="consistency",
                        message=f"Form action target '{action}' could not be resolved.",
                        severity=Severity.WARN,
                        evidence={
                            "target": action,
                            "referring_file": referring_file or "unknown",
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                        profile=profile_name,
                    )
                )
        
        return findings

