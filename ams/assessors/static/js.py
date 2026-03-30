from __future__ import annotations

import re
from typing import List

from ams.assessors import Assessor
from ams.core.finding_ids import JS as JID
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


class JSStaticAssessor(Assessor):
    """Deterministic JavaScript static checks using simple heuristics."""

    name = "js_static"

    # Check js syntax.
    def _check_js_syntax(self, files: list[tuple[object, str]]) -> List[Finding]:
        findings: List[Finding] = []
        for path, content in files:
            open_braces = content.count("{")
            close_braces = content.count("}")
            open_parens = content.count("(")
            close_parens = content.count(")")
            braces_balanced = open_braces == close_braces
            parens_balanced = open_parens == close_parens
            has_semicolons = ";" in content

            syntax_evidence = {
                "path": str(path),
                "open_braces": open_braces,
                "close_braces": close_braces,
                "open_parens": open_parens,
                "close_parens": close_parens,
                "braces_balanced": braces_balanced,
                "parens_balanced": parens_balanced,
                "has_semicolons": has_semicolons,
            }

            if braces_balanced and parens_balanced and (open_braces + open_parens) > 0:
                findings.append(
                    Finding(
                        id=JID.SYNTAX_OK,
                        category="js",
                        message="JS syntax heuristics look OK.",
                        severity=Severity.INFO,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            elif (open_braces + close_braces + open_parens + close_parens) == 0:
                findings.append(
                    Finding(
                        id=JID.NO_CODE,
                        category="js",
                        message="JS file appears to contain no code-like tokens.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=JID.SYNTAX_SUSPECT,
                        category="js",
                        message="JS syntax heuristics look suspect.",
                        severity=Severity.WARN,
                        evidence=syntax_evidence,
                        source=self.name,
                    )
                )
        return findings

    # Check js patterns.
    def _check_js_patterns(self, files: list[tuple[object, str]]) -> List[Finding]:
        findings: List[Finding] = []
        for path, content in files:
            findings.extend(self._analyse_api_usage(path, content))

            lines = content.splitlines()
            potential_globals = []
            in_function = False
            brace_depth = 0

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                    continue

                brace_depth += line.count("{") - line.count("}")
                if "function" in stripped or "=>" in stripped:
                    in_function = True
                if brace_depth == 0:
                    in_function = False

                if not in_function and "=" in stripped:
                    if not re.search(r"\b(var|let|const|function)\s+", stripped):
                        match = re.match(r"^\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=", stripped)
                        if match:
                            var_name = match.group(1)
                            if var_name not in ["window", "document", "console", "Math", "Date"]:
                                potential_globals.append({"line": i, "variable": var_name})

            if potential_globals:
                findings.append(
                    Finding(
                        id=JID.QUALITY_GLOBAL_VARIABLES,
                        category="js",
                        message=f"Found {len(potential_globals)} potential global variable(s). Use var/let/const to avoid polluting global scope.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "global_count": len(potential_globals),
                            "examples": potential_globals[:5],
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            single_letter_vars = re.findall(r"\b([a-z])\s*[=:]", content.lower())
            common_loop_vars = {"i", "j", "k", "x", "y", "z"}
            suspicious_vars = [v for v in single_letter_vars if v not in common_loop_vars]

            short_names = re.findall(r"\b([a-z]{1,2})\b", content.lower())
            js_keywords = {"if", "in", "do", "of", "or", "is", "no", "on", "up", "at", "to", "as", "an"}
            common_short = {"id", "el", "fn", "cb", "x", "y", "z", "i", "j", "k"}
            suspicious_short = [n for n in set(short_names) if n not in common_short and n not in js_keywords and len(n) == 1]

            if len(suspicious_vars) > 5 or len(suspicious_short) > 3:
                findings.append(
                    Finding(
                        id=JID.QUALITY_POOR_NAMING,
                        category="js",
                        message="Found potentially unclear variable/function names. Use descriptive names for better code maintainability.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "suspicious_single_letter": len(suspicious_vars),
                            "suspicious_short": len(suspicious_short),
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            declared_vars: dict[str, int] = {}
            for match in re.finditer(r"\b(?:var|let|const)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)", content):
                declared_vars[match.group(1)] = match.end()

            unused_vars = set()
            for var_name, decl_end in declared_vars.items():
                usage_pattern = re.compile(r"\b" + re.escape(var_name) + r"\b")
                rest_of_content = content[decl_end:]
                if not usage_pattern.search(rest_of_content):
                    unused_vars.add(var_name)
            unused_vars = {v for v in unused_vars if not v.startswith("_")}

            if len(unused_vars) > 2:
                findings.append(
                    Finding(
                        id=JID.QUALITY_UNUSED_VARIABLES,
                        category="js",
                        message=f"Found {len(unused_vars)} potentially unused variable(s). Remove unused code to improve maintainability.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "unused_count": len(unused_vars),
                            "examples": list(unused_vars)[:5],
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            lines_after_return = []
            for i, line in enumerate(lines[:-1], 1):
                stripped = line.strip()
                if stripped and not stripped.startswith("//"):
                    if re.search(r"\b(return|throw|break|continue)\b", stripped):
                        for j in range(i, min(i + 3, len(lines))):
                            next_line = lines[j].strip()
                            if next_line and not next_line.startswith("//") and not next_line.startswith("/*"):
                                if "}" not in next_line and "else" not in next_line and "catch" not in next_line:
                                    lines_after_return.append(i + 1)
                                break

            if lines_after_return:
                findings.append(
                    Finding(
                        id=JID.QUALITY_UNREACHABLE_CODE,
                        category="js",
                        message="Found potentially unreachable code after return/throw/break/continue statements.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "unreachable_lines": lines_after_return[:10],
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

            function_declarations = content.count("function ") + content.count("=>")
            code_lines = sum(1 for line in lines if line.strip() and not line.strip().startswith("//"))
            if code_lines > 50 and function_declarations < 3:
                findings.append(
                    Finding(
                        id=JID.QUALITY_LACK_OF_MODULARITY,
                        category="js",
                        message="Code appears to lack modular structure. Consider breaking code into reusable functions.",
                        severity=Severity.WARN,
                        evidence={
                            "path": str(path),
                            "function_count": function_declarations,
                            "code_lines": code_lines,
                        },
                        source=self.name,
                        finding_category=FindingCategory.STRUCTURE,
                    )
                )

        return findings

    # Check js dependencies.
    def _check_js_dependencies(self, files: list[tuple[object, str]]) -> List[Finding]:
        findings: List[Finding] = []
        for path, content in files:
            lowered = content.lower()
            dom_calls = lowered.count("document.")
            query_calls = lowered.count("queryselector") + lowered.count("getelementby")
            event_listeners = lowered.count("addeventlistener")
            fetch_calls = lowered.count("fetch(")
            xhr_calls = lowered.count("xmlhttprequest")
            loops = lowered.count("for (") + lowered.count("while (")
            functions = lowered.count("function ") + lowered.count("=>")

            findings.append(
                Finding(
                    id=JID.EVIDENCE,
                    category="js",
                    message="JS evidence collected.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "dom_calls": dom_calls,
                        "query_calls": query_calls,
                        "event_listeners": event_listeners,
                        "fetch_calls": fetch_calls,
                        "xhr_calls": xhr_calls,
                        "loops": loops,
                        "functions": functions,
                    },
                    source=self.name,
                )
            )
        return findings

    # Run the JavaScript static checks.
    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        js_files = sorted(context.files_for("js", relevant_only=True))

        # Determine if JS is required for this profile
        profile_name = context.metadata.get("profile")
        is_required = False
        if profile_name:
            try:
                profile_spec = get_profile_spec(profile_name)
                is_required = profile_spec.is_component_required("js")
            except ValueError:
                pass  # Unknown profile, treat as not required

        if not js_files:
            if is_required:
                # Required for profile but missing
                findings.append(
                    Finding(
                        id=JID.MISSING_FILES,
                        category="js",
                        message="No JavaScript files found; JS is required for this profile.",
                        severity=Severity.FAIL,
                        evidence={
                            "expected_extensions": [".js"],
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
                        id=JID.SKIPPED,
                        category="js",
                        message="No JavaScript files found; JS is not required for this profile.",
                        severity=Severity.SKIPPED,
                        evidence={
                            "expected_extensions": [".js"],
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

        loaded_files: list[tuple[object, str]] = []
        for path in js_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                findings.append(
                    Finding(
                        id=JID.READ_ERROR,
                        category="js",
                        message="Failed to read JS file.",
                        severity=Severity.FAIL,
                        evidence={"path": str(path), "error": str(exc)},
                        source=self.name,
                    )
                )
                continue
            loaded_files.append((path, content))

        findings.extend(self._check_js_syntax(loaded_files))
        findings.extend(self._check_js_patterns(loaded_files))
        findings.extend(self._check_js_dependencies(loaded_files))
        return findings

    # API.
    def _analyse_api_usage(self, path, content: str) -> List[Finding]:
        """Detect and report API usage patterns in JavaScript files."""
        lowered = content.lower()
        findings: List[Finding] = []

        # HTTP Method Extraction.
        # Match method specifications inside fetch options objects
        method_pattern = re.compile(
            r"""(?:method\s*:\s*['"])(GET|POST|PUT|DELETE|PATCH)(?:['"])""",
            re.IGNORECASE,
        )
        http_methods_found = [m.upper() for m in method_pattern.findall(content)]

        # Endpoint Detection.
        # Look for URL-like strings inside fetch() calls
        endpoint_pattern = re.compile(
            r"""fetch\s*\(\s*['"`]([^'"`]+)['"`]""",
            re.IGNORECASE,
        )
        endpoints_found = endpoint_pattern.findall(content)
        api_style_endpoints = [
            ep for ep in endpoints_found
            if "/api" in ep.lower() or ep.startswith("http") or ep.startswith("/")
        ]

        # Payload & Headers.
        json_stringify_count = lowered.count("json.stringify")
        content_type_json = lowered.count("application/json")
        headers_count = lowered.count("headers")

        # Response Handling.
        response_json_count = lowered.count(".json(")
        catch_count = lowered.count(".catch(")
        then_count = lowered.count(".then(")
        response_ok_count = content.count("response.ok") + content.count("!response.ok")

        has_api_usage = bool(
            http_methods_found
            or api_style_endpoints
            or json_stringify_count
            or response_json_count
        )

        if has_api_usage:
            findings.append(
                Finding(
                    id=JID.API_EVIDENCE,
                    category="js",
                    message="API usage patterns detected in JavaScript.",
                    severity=Severity.INFO,
                    evidence={
                        "path": str(path),
                        "http_methods": http_methods_found,
                        "endpoints": api_style_endpoints[:10],
                        "json_stringify_count": json_stringify_count,
                        "content_type_json": content_type_json,
                        "headers_count": headers_count,
                        "response_json_count": response_json_count,
                        "catch_count": catch_count,
                        "then_count": then_count,
                        "response_ok_count": response_ok_count,
                    },
                    source=self.name,
                    finding_category=FindingCategory.EVIDENCE,
                )
            )

        return findings


__all__ = ["JSStaticAssessor"]
