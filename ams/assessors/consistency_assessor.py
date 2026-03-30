"""Cross-file consistency checks for HTML, CSS, JS, and PHP."""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Set

from ams.assessors import Assessor
from ams.core.finding_ids import CONSISTENCY as COID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec

logger = logging.getLogger(__name__)


class _HTMLElementExtractor(HTMLParser):
    """Extract IDs and classes from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: Set[str] = set()
        self.classes: Set[str] = set()
        self.form_fields: Dict[str, str] = {}  # Name -> form context
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

    def _assess_js_css_consistency(
        self,
        context: SubmissionContext,
        html_data: Dict[str, object],
        profile_name: str,
        profile_spec,
    ) -> List[Finding]:
        findings: List[Finding] = []
        if profile_spec and (profile_spec.is_component_required("html") or profile_spec.is_component_required("js")):
            findings.extend(self._check_js_html_consistency(context, html_data, profile_name))
        if profile_spec and (profile_spec.is_component_required("html") or profile_spec.is_component_required("css")):
            findings.extend(self._check_css_html_consistency(context, html_data, profile_name))
        return findings

    def _assess_php_routing_consistency(
        self,
        context: SubmissionContext,
        html_data: Dict[str, object],
        profile_name: str,
        profile_spec,
    ) -> List[Finding]:
        findings: List[Finding] = []
        if profile_spec and profile_spec.is_component_required("php"):
            findings.extend(self._check_php_form_consistency(context, html_data, profile_name))
        findings.extend(self._check_link_targets(context, html_data, profile_name))
        return findings

    def _aggregate_consistency_results(self, *groups: List[Finding]) -> List[Finding]:
        findings: List[Finding] = []
        for group in groups:
            findings.extend(group)
        return findings

    def run(self, context: SubmissionContext) -> List[Finding]:
        """Run all consistency checks."""
        profile_name = context.metadata.get("profile", "frontend")

        try:
            profile_spec = get_profile_spec(profile_name)
        except ValueError:
            profile_spec = None

        html_data = self._extract_html_data(context)
        js_css_findings = self._assess_js_css_consistency(context, html_data, profile_name, profile_spec)
        php_routing_findings = self._assess_php_routing_consistency(context, html_data, profile_name, profile_spec)

        cross_file_results: Dict[str, object] = {}
        if profile_spec and profile_spec.is_component_required("php"):
            cross_file_results["php_form_alignment"] = self._compute_php_form_alignment(
                context, html_data
            )
        if profile_spec and profile_spec.is_component_required("sql") and profile_spec.is_component_required("php"):
            cross_file_results["sql_alignment"] = self._compute_sql_application_alignment(context)
        if profile_spec and profile_spec.is_component_required("api"):
            cross_file_results["api_alignment"] = self._compute_api_client_alignment(context)
        if cross_file_results:
            context.metadata["cross_file_results"] = cross_file_results

        return self._aggregate_consistency_results(js_css_findings, php_routing_findings)

    def _extract_html_data(self, context: SubmissionContext) -> Dict[str, object]:
        """Extract IDs, classes, form fields, and links from HTML files."""
        html_files = sorted(context.files_for("html", relevant_only=True))
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
            except Exception as e:
                logger.debug("Failed to parse HTML file %s: %s", html_file, e)

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
        js_files = sorted(context.files_for("js", relevant_only=True))
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
                                    id=COID.JS_MISSING_HTML_ID,
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
                                    id=COID.JS_MISSING_HTML_CLASS,
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
            except Exception as e:
                logger.debug("Error checking JS-HTML consistency for %s: %s", js_file, e)

        return findings

    def _check_css_html_consistency(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check CSS selectors against HTML IDs and classes."""
        findings: List[Finding] = []
        css_files = sorted(context.files_for("css", relevant_only=True))
        html_ids: Set[str] = html_data.get("ids", set())
        html_classes: Set[str] = html_data.get("classes", set())

        # Simple patterns: #id and.class (at start of selector or after space/comma)
        id_pattern = r'(?:^|[\s,])#([a-zA-Z_][a-zA-Z0-9_-]*)'
        class_pattern = r'(?:^|[\s,])\.([a-zA-Z_][a-zA-Z0-9_-]*)'
        # Hex colour codes look like valid IDs but aren't selectors (e.g. #fff, #f4f4f4)
        _HEX_COLOR = re.compile(r'^[0-9a-fA-F]{3,8}$')

        for css_file in css_files:
            try:
                content = css_file.read_text(encoding="utf-8", errors="replace")

                # Check IDs
                for match in re.finditer(id_pattern, content):
                    selector_value = match.group(1)
                    if not selector_value or selector_value in html_ids:
                        continue
                    # Skip CSS hex colour values (e.g. #fff, #f4f4f4, #aabbcc)
                    if _HEX_COLOR.match(selector_value):
                        continue
                    # Count occurrences
                    count = content.count(f"#{selector_value}")

                    findings.append(
                        Finding(
                            id=COID.CSS_MISSING_HTML_ID,
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
                                id=COID.CSS_MISSING_HTML_CLASS,
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
            except Exception as e:
                logger.debug("Error checking CSS-HTML consistency for %s: %s", css_file, e)

        return findings

    def _check_php_form_consistency(
        self, context: SubmissionContext, html_data: Dict[str, object], profile_name: str
    ) -> List[Finding]:
        """Check PHP form variable access against HTML form fields."""
        findings: List[Finding] = []
        php_files = sorted(context.files_for("php", relevant_only=True))
        html_form_fields: Set[str] = set(html_data.get("form_fields", {}).keys())

        # Patterns for PHP form variable access
        post_pattern = r'\$_POST\s*\[\s*["\']([^"\']+)["\']'
        get_pattern = r'\$_GET\s*\[\s*["\']([^"\']+)["\']'
        request_pattern = r'\$_REQUEST\s*\[\s*["\']([^"\']+)["\']'

        php_accessed_keys: Set[str] = set()
        php_file_map: Dict[str, str] = {}  # Key -> file

        for php_file in php_files:
            try:
                content = php_file.read_text(encoding="utf-8", errors="replace")

                for pattern in [post_pattern, get_pattern, request_pattern]:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        key = match.group(1)
                        if key:
                            php_accessed_keys.add(key)
                            php_file_map[key] = str(php_file)
            except Exception as e:
                logger.debug("Error reading PHP file %s: %s", php_file, e)

        # Check: PHP expects key that doesn't exist in HTML
        for key in php_accessed_keys:
            if key not in html_form_fields:
                findings.append(
                    Finding(
                        id=COID.PHP_EXPECTS_MISSING_FORM_FIELD,
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
                        id=COID.FORM_FIELD_UNUSED_IN_PHP,
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

    def _compute_php_form_alignment(
        self, context: SubmissionContext, html_data: Dict[str, object]
    ) -> Dict[str, object]:
        """Compute PHP ↔ HTML form field alignment score for RequirementEvaluationEngine."""
        html_form_fields: Set[str] = set(html_data.get("form_fields", {}).keys())  # type: ignore[arg-type]
        php_files = sorted(context.files_for("php", relevant_only=True))
        post_pattern = r'\$_POST\s*\[\s*["\']([^"\']+)["\']'
        get_pattern = r'\$_GET\s*\[\s*["\']([^"\']+)["\']'
        php_accessed_keys: Set[str] = set()
        for php_file in php_files:
            try:
                content = php_file.read_text(encoding="utf-8", errors="replace")
                for pattern in [post_pattern, get_pattern]:
                    for match in re.finditer(pattern, content, re.IGNORECASE):
                        php_accessed_keys.add(match.group(1))
            except Exception:
                pass
        if not php_accessed_keys and not html_form_fields:
            return {"score": "SKIPPED", "status": "SKIPPED", "evidence": {"reason": "no_form_fields"}}
        matched = php_accessed_keys & html_form_fields
        total = len(php_accessed_keys | html_form_fields)
        ratio = len(matched) / total if total > 0 else 0.0
        if ratio >= 0.8:
            return {"score": 1.0, "status": "PASS", "evidence": {"matched": list(matched), "php_keys": list(php_accessed_keys), "html_fields": list(html_form_fields)}}
        elif ratio >= 0.4:
            return {"score": 0.5, "status": "PARTIAL", "evidence": {"matched": list(matched), "php_keys": list(php_accessed_keys), "html_fields": list(html_form_fields)}}
        return {"score": 0.0, "status": "FAIL", "evidence": {"matched": list(matched), "php_keys": list(php_accessed_keys), "html_fields": list(html_form_fields)}}

    def _compute_sql_application_alignment(self, context: SubmissionContext) -> Dict[str, object]:
        """Compute SQL table ↔ PHP/JS application reference alignment score."""
        sql_files = sorted(context.files_for("sql", relevant_only=True))
        php_files = sorted(context.files_for("php", relevant_only=True))
        # Extract SQL table names from CREATE TABLE statements
        sql_tables: Set[str] = set()
        create_pattern = re.compile(r'create\s+table\s+(?:if\s+not\s+exists\s+)?[`"\']?(\w+)[`"\']?', re.IGNORECASE)
        for sql_file in sql_files:
            try:
                content = sql_file.read_text(encoding="utf-8", errors="replace")
                for match in create_pattern.finditer(content):
                    sql_tables.add(match.group(1).lower())
            except Exception:
                pass
        if not sql_tables:
            return {"score": "SKIPPED", "status": "SKIPPED", "evidence": {"reason": "no_sql_tables"}}
        # Check PHP/JS references to those table names
        app_references: Set[str] = set()
        for php_file in php_files:
            try:
                content = php_file.read_text(encoding="utf-8", errors="replace").lower()
                for table in sql_tables:
                    if table in content:
                        app_references.add(table)
            except Exception:
                pass
        matched = sql_tables & app_references
        ratio = len(matched) / len(sql_tables) if sql_tables else 0.0
        if ratio >= 0.8:
            return {"score": 1.0, "status": "PASS", "evidence": {"sql_tables": list(sql_tables), "referenced": list(matched)}}
        elif ratio >= 0.4:
            return {"score": 0.5, "status": "PARTIAL", "evidence": {"sql_tables": list(sql_tables), "referenced": list(matched)}}
        return {"score": 0.0, "status": "FAIL", "evidence": {"sql_tables": list(sql_tables), "referenced": list(matched)}}

    def _compute_api_client_alignment(self, context: SubmissionContext) -> Dict[str, object]:
        """Compute JS fetch ↔ PHP route alignment score."""
        js_files = sorted(context.files_for("js", relevant_only=True))
        php_files = sorted(context.files_for("php", relevant_only=True))
        # Extract fetch() endpoints from JS
        fetch_pattern = re.compile(r"""fetch\s*\(\s*['"]([^'"]+)['"]""", re.IGNORECASE)
        js_endpoints: Set[str] = set()
        for js_file in js_files:
            try:
                content = js_file.read_text(encoding="utf-8", errors="replace")
                for match in fetch_pattern.finditer(content):
                    js_endpoints.add(match.group(1).lower())
            except Exception:
                pass
        if not js_endpoints:
            return {"score": "SKIPPED", "status": "SKIPPED", "evidence": {"reason": "no_fetch_calls"}}
        # Check PHP files handle those endpoints (by filename or request_uri pattern)
        php_routes: Set[str] = set()
        for php_file in php_files:
            php_routes.add(php_file.name.lower().replace(".php", ""))
        matched = {ep for ep in js_endpoints if any(r in ep for r in php_routes)}
        ratio = len(matched) / len(js_endpoints) if js_endpoints else 0.0
        if ratio >= 0.7:
            return {"score": 1.0, "status": "PASS", "evidence": {"js_endpoints": list(js_endpoints), "php_routes": list(php_routes), "matched": list(matched)}}
        elif ratio >= 0.3:
            return {"score": 0.5, "status": "PARTIAL", "evidence": {"js_endpoints": list(js_endpoints), "php_routes": list(php_routes), "matched": list(matched)}}
        return {"score": 0.0, "status": "FAIL", "evidence": {"js_endpoints": list(js_endpoints), "php_routes": list(php_routes), "matched": list(matched)}}

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

            # Check whether file exists within submission
            try:
                if not target_path.exists() or not str(target_path).startswith(str(submission_root.resolve())):
                    findings.append(
                        Finding(
                            id=COID.MISSING_LINK_TARGET,
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

            # Check whether file exists within submission
            try:
                if not target_path.exists() or not str(target_path).startswith(str(submission_root.resolve())):
                    findings.append(
                        Finding(
                            id=COID.MISSING_FORM_ACTION_TARGET,
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
