from __future__ import annotations

import re
from pathlib import Path
from typing import List

from ams.assessors import Assessor
from ams.assessors.static.base_static import (
    missing_component_finding,
    resolve_component_requirement,
    skipped_component_finding,
)
from ams.core.finding_ids import API as AID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext


class APIStaticAssessor(Assessor):
    """Deterministic API static checks — detects RESTful patterns in PHP and JS files."""
    name = "api_static"

    # Run the API static checks for PHP and JavaScript files.
    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        profile_name, is_required = resolve_component_requirement(context, "api")

        candidate_files = self._candidate_files(context)

        if not candidate_files:
            findings.append(self._missing_or_skipped_finding(profile_name, is_required))
            return findings

        for path in candidate_files:
            content = self._read_content(path)
            if content is None:
                continue

            evidence = self._detect_api_patterns(path, content)
            if evidence.get("is_api_endpoint") or evidence.get("has_api_patterns"):
                findings.append(
                    Finding(
                        id=AID.EVIDENCE,
                        category="api",
                        message="API usage patterns detected.",
                        severity=Severity.INFO,
                        evidence=evidence,
                        source=self.name,
                        finding_category=FindingCategory.EVIDENCE,
                    )
                )

        return findings

    @staticmethod
    def _candidate_files(context: SubmissionContext) -> list[Path]:
        php_files = sorted(context.files_for("php", relevant_only=True))
        js_files = sorted(context.files_for("js", relevant_only=True))
        return php_files + js_files

    def _missing_or_skipped_finding(self, profile_name: str, is_required: bool) -> Finding:
        if is_required:
            return missing_component_finding(
                finding_id=AID.MISSING_FILES,
                category="api",
                message="No PHP or JS files found; API component is required for this profile.",
                source=self.name,
                profile_name=profile_name,
                expected_extensions=[".php", ".js"],
            )
        return skipped_component_finding(
            finding_id=AID.SKIPPED,
            category="api",
            message="No PHP or JS files found; API component is not required for this profile.",
            source=self.name,
            profile_name=profile_name,
            expected_extensions=[".php", ".js"],
        )

    @staticmethod
    def _read_content(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    # Route the file to the correct API pattern detector.
    def _detect_api_patterns(self, path, content: str) -> dict:
        lowered = content.lower()
        suffix = path.suffix.lower()

        if suffix == ".php":
            return self._detect_php_api(content, lowered, path)
        elif suffix == ".js":
            return self._detect_js_api(content, lowered, path)
        return {}

    # Detect API-style behaviour in PHP files.
    def _detect_php_api(self, content: str, lowered: str, path) -> dict:
        json_content_type = bool(re.search(
            r"""header\s*\(\s*['"]Content-Type\s*:\s*application/json""",
            content,
            re.IGNORECASE,
        ))
        json_encode_count = lowered.count("json_encode(")
        method_routing = bool(re.search(
            r"""\$_SERVER\s*\[\s*['"]REQUEST_METHOD['"]\s*\]""",
            content,
        ))
        php_input = bool(re.search(
            r"""file_get_contents\s*\(\s*['"]php://input['"]""",
            content,
            re.IGNORECASE,
        ))
        json_decode_count = lowered.count("json_decode(")
        http_response_code = bool(re.search(r"""http_response_code\s*\(""", content, re.IGNORECASE))

        is_api_endpoint = json_content_type and json_encode_count > 0
        has_api_patterns = is_api_endpoint or method_routing or php_input or json_decode_count > 0

        return {
            "path": str(path),
            "file_type": "php",
            "json_content_type_header": json_content_type,
            "json_encode_count": json_encode_count,
            "method_routing": method_routing,
            "php_input_read": php_input,
            "json_decode_count": json_decode_count,
            "http_response_code": http_response_code,
            "is_api_endpoint": is_api_endpoint,
            "has_api_patterns": has_api_patterns,
        }

    # Detect API-style behaviour in JavaScript files.
    def _detect_js_api(self, content: str, lowered: str, path) -> dict:
        fetch_count = lowered.count("fetch(") + lowered.count("fetch (")
        xhr_count = lowered.count("xmlhttprequest") + lowered.count("xhr.")
        axios_count = lowered.count("axios.")
        json_parse_count = lowered.count("json.parse(")
        json_stringify_count = lowered.count("json.stringify(")
        async_await = bool(re.search(r'\basync\b.*\bawait\b', content, re.DOTALL))

        has_api_patterns = fetch_count > 0 or xhr_count > 0 or axios_count > 0
        is_api_endpoint = False  # JS files are clients, not endpoints

        return {
            "path": str(path),
            "file_type": "js",
            "fetch_count": fetch_count,
            "xhr_count": xhr_count,
            "axios_count": axios_count,
            "json_parse_count": json_parse_count,
            "json_stringify_count": json_stringify_count,
            "async_await": async_await,
            "is_api_endpoint": is_api_endpoint,
            "has_api_patterns": has_api_patterns,
        }


__all__ = ["APIStaticAssessor"]
