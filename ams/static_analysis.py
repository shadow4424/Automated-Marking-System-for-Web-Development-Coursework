"""Static analysis for multi-language coursework submissions.

The checks are intentionally lightweight to tolerate stylistic variation while
surfacing structural issues. Each rule is deterministic and emits a reason that
can be presented to students and instructors.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

from .models import StepResult, SubmissionContext
from .normalisation import iter_files_with_suffix


RuleFn = Callable[[Path], Tuple[bool, str]]


class StaticAnalyser:
    def __init__(self) -> None:
        self.html_rules: List[RuleFn] = [
            self._html_has_root_elements,
            self._html_declares_charset,
        ]
        self.css_rules: List[RuleFn] = [self._css_has_selectors]
        self.js_rules: List[RuleFn] = [self._js_has_functions]
        self.php_rules: List[RuleFn] = [self._php_tags_present]
        self.sql_rules: List[RuleFn] = [self._sql_has_statements]

    def run(self, context: SubmissionContext) -> StepResult:
        reasons: List[str] = []
        total_checks = 0
        passed_checks = 0

        file_groups = {
            "html": iter_files_with_suffix(context.normalized_files, (".html", ".htm")),
            "css": iter_files_with_suffix(context.normalized_files, (".css",)),
            "js": iter_files_with_suffix(context.normalized_files, (".js",)),
            "php": iter_files_with_suffix(context.normalized_files, (".php",)),
            "sql": iter_files_with_suffix(context.normalized_files, (".sql",)),
        }

        for label, files in file_groups.items():
            reasons.append(f"Detected {len(files)} {label.upper()} file(s)")

        for file in file_groups["html"]:
            passed, rule_reasons, checks = self._run_rules(file, self.html_rules)
            passed_checks += passed
            total_checks += checks
            reasons.extend(rule_reasons)

        for file in file_groups["css"]:
            passed, rule_reasons, checks = self._run_rules(file, self.css_rules)
            passed_checks += passed
            total_checks += checks
            reasons.extend(rule_reasons)

        for file in file_groups["js"]:
            passed, rule_reasons, checks = self._run_rules(file, self.js_rules)
            passed_checks += passed
            total_checks += checks
            reasons.extend(rule_reasons)

        for file in file_groups["php"]:
            passed, rule_reasons, checks = self._run_rules(file, self.php_rules)
            passed_checks += passed
            total_checks += checks
            reasons.extend(rule_reasons)

        for file in file_groups["sql"]:
            passed, rule_reasons, checks = self._run_rules(file, self.sql_rules)
            passed_checks += passed
            total_checks += checks
            reasons.extend(rule_reasons)

        if total_checks == 0:
            reasons.append("No relevant source files found for static analysis")
            score = 0.0
        else:
            ratio = passed_checks / total_checks
            if ratio >= 0.75:
                score = 1.0
                reasons.append(f"Static checks mostly passed ({passed_checks}/{total_checks})")
            elif ratio >= 0.35:
                score = 0.5
                reasons.append(f"Static checks partially passed ({passed_checks}/{total_checks})")
            else:
                score = 0.0
                reasons.append(f"Static checks weak ({passed_checks}/{total_checks})")

        return StepResult(name="static_analysis", score=score, reasons=reasons)

    def _run_rules(self, file_path: Path, rules: Iterable[RuleFn]) -> tuple[int, List[str], int]:
        rule_reasons: List[str] = []
        passed_rules = 0
        total = 0
        for rule in rules:
            passed, reason = rule(file_path)
            total += 1
            passed_rules += int(passed)
            rule_reasons.append(reason)
        return passed_rules, rule_reasons, total

    def _html_has_root_elements(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore")
        has_html = "<html" in text.lower() and "</html>" in text.lower()
        has_body = "<body" in text.lower() and "</body>" in text.lower()
        passed = has_html and has_body
        reason = f"{path.name}: {'has' if passed else 'missing'} <html>/<body> structure"
        return passed, reason

    def _html_declares_charset(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore")
        charset_meta = re.search(r"<meta[^>]+charset=", text, flags=re.IGNORECASE)
        passed = charset_meta is not None
        reason = f"{path.name}: {'declares' if passed else 'missing'} charset meta tag"
        return passed, reason

    def _css_has_selectors(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore")
        selectors = re.findall(r"[.#]?[a-zA-Z0-9_-]+\s*\{", text)
        passed = len(selectors) > 0
        reason = f"{path.name}: {'found' if passed else 'no'} CSS selector blocks"
        return passed, reason

    def _js_has_functions(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore")
        has_named_function = "function" in text
        has_arrow = "=>" in text
        passed = has_named_function or has_arrow
        reason = f"{path.name}: {'contains' if passed else 'missing'} JavaScript function definitions"
        return passed, reason

    def _php_tags_present(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore").lower()
        passed = "<?php" in text or text.strip().startswith("<?")
        reason = f"{path.name}: {'PHP open tag found' if passed else 'missing PHP open tag'}"
        return passed, reason

    def _sql_has_statements(self, path: Path) -> tuple[bool, str]:
        text = path.read_text(errors="ignore")
        statements = [stmt.strip() for stmt in text.split(";") if stmt.strip()]
        passed = len(statements) > 0
        reason = f"{path.name}: {'contains' if passed else 'no'} SQL statements"
        return passed, reason
