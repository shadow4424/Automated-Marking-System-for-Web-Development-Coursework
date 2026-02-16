from __future__ import annotations

from typing import List

from ams.assessors.base import Assessor
from ams.assessors.shared.html_parser import TagCountingParser
from ams.core.finding_ids import HTML as HID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class HTMLStaticAssessor(Assessor):
    """Deterministic HTML static checks."""

    name = "html_static"

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        html_files = sorted(context.discovered_files.get("html", []))
        
        # Determine if HTML is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("html")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not html_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id=HID.MISSING_FILES,
                        category="html",
                        message="No HTML files found; HTML is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".html"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": True,
                        },
                        source=self.name,
                        finding_category=FindingCategory.MISSING,
                        profile=profile_name,
                        required=True,
                    )
                )
            else:
                # Not required for profile, skip
                findings.append(
                    Finding(
                        id=HID.SKIPPED,
                        category="html",
                        message="No HTML files found; HTML is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".html"],
                            "discovered_count": 0,
                            "profile": profile_name,
                            "required": False,
                        },
                        source=self.name,
                        finding_category=FindingCategory.OTHER,
                        profile=profile_name,
                        required=False,
                    )
                )
            return findings

        for path in html_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=HID.READ_ERROR,
                        category="html",
                        message="Failed to read HTML file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue

            lowered = content.lower()

            # Use the shared parser for structure detection
            parser = TagCountingParser()
            parser.feed(content)

            has_doctype = parser.has_doctype or "<!doctype html" in lowered
            has_html_tag = parser.has_html_tag
            has_head = parser.has_head
            has_body = parser.has_body

            structure_evidence = {
                "path": str(path),
                "has_doctype": has_doctype,
                "has_html_tag": has_html_tag,
                "has_head": has_head,
                "has_body": has_body,
            }

            if (has_doctype or has_html_tag) and has_head and has_body:
                findings.append(
                    Finding(
                        id=HID.PARSE_OK,
                        category="html",
                        message="HTML structure appears valid.",
                        severity=Severity.INFO,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=HID.PARSE_SUSPECT,
                        category="html",
                        message="HTML structure incomplete or missing expected elements.",
                        severity=Severity.WARN,
                        evidence=structure_evidence,
                        source=self.name,
                    )
                )

            forms = parser.form_count
            inputs = parser.input_count
            links = parser.link_count

            findings.append(
                Finding(
                    id=HID.ELEMENT_EVIDENCE,
                    category="html",
                    message="HTML element evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "forms": forms,
                        "inputs": inputs,
                        "links": links,
                    },
                    source=self.name,
                )
            )

            # Code Quality Checks
            # 1. Detect inline CSS (style attribute)
            inline_style_count = content.count('style="') + content.count("style='")
            if inline_style_count > 0:
                findings.append(
                    Finding(
                        id=HID.QUALITY_INLINE_CSS,
                        category="html",
                        message=f"Found {inline_style_count} inline style attribute(s). Consider using external CSS for better maintainability.",
                        severity=Severity.WARN if inline_style_count <= 3 else Severity.FAIL,
                        evidence={
                            "path": str(path),
                            "inline_style_count": inline_style_count,
                            "threshold": 3,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            # 2. Detect deprecated HTML tags
            deprecated_tags = {
                "<center>": "center",
                "<font>": "font",
                "<marquee>": "marquee",
                "<blink>": "blink",
                "<applet>": "applet",
                "<frame>": "frame",
                "<frameset>": "frameset",
            }
            found_deprecated = []
            for tag_pattern, tag_name in deprecated_tags.items():
                if tag_pattern.lower() in lowered:
                    found_deprecated.append(tag_name)
            
            if found_deprecated:
                findings.append(
                    Finding(
                        id=HID.QUALITY_DEPRECATED_TAGS,
                        category="html",
                        message=f"Found deprecated HTML tags: {', '.join(found_deprecated)}. Use modern HTML5 alternatives.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "deprecated_tags": found_deprecated,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

        return findings


__all__ = ["HTMLStaticAssessor"]
