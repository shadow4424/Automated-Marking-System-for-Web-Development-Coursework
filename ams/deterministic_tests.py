"""Deterministic behavioural checks without browser automation.

These rules approximate expected coursework structure without locking to a single
solution. Each rule is transparent and logs the precise rationale for its
pass/fail outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import List, Sequence

from .models import DeterministicRule, StepResult, SubmissionContext
from .normalisation import iter_files_with_suffix


@dataclass
class FilePresenceRule(DeterministicRule):
    targets: Sequence[str]

    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:
        found = any((context.extracted_path / target).exists() for target in self.targets)
        return found, (
            f"Found expected entry file among {', '.join(self.targets)}"
            if found
            else f"Missing entry file; checked {', '.join(self.targets)}"
        )


@dataclass
class FormHandlingRule(DeterministicRule):
    file_suffixes: Sequence[str]

    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:
        files = iter_files_with_suffix(context.normalized_files, tuple(self.file_suffixes))
        pattern = re.compile(r"<form[^>]+method=", flags=re.IGNORECASE)
        matches = 0
        for file in files:
            text = file.read_text(errors="ignore")
            if pattern.search(text):
                matches += 1
        passed = matches > 0
        return passed, (
            f"Detected form handling markup in {matches} file(s)"
            if passed
            else "No HTML forms with explicit method attribute found"
        )


@dataclass
class ClientApiRule(DeterministicRule):
    file_suffixes: Sequence[str]

    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:
        files = iter_files_with_suffix(context.normalized_files, tuple(self.file_suffixes))
        fetch_count = 0
        xhr_count = 0
        for file in files:
            text = file.read_text(errors="ignore")
            fetch_count += text.count("fetch(")
            xhr_count += text.lower().count("xmlhttprequest")
        passed = (fetch_count + xhr_count) > 0
        return passed, (
            f"Detected client-side API usage (fetch:{fetch_count}, xhr:{xhr_count})"
            if passed
            else "No client-side API calls detected"
        )


@dataclass
class SqlUsageRule(DeterministicRule):
    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:
        sql_files = iter_files_with_suffix(context.normalized_files, (".sql",))
        statements = 0
        for file in sql_files:
            statements += len([stmt for stmt in file.read_text(errors="ignore").split(";") if stmt.strip()])
        passed = statements > 0
        return passed, (
            f"SQL statements present ({statements})" if passed else "No SQL statements detected"
        )


@dataclass
class ServerInputRule(DeterministicRule):
    file_suffixes: Sequence[str]

    def evaluate(self, context: SubmissionContext) -> tuple[bool, str]:
        files = iter_files_with_suffix(context.normalized_files, tuple(self.file_suffixes))
        patterns = ["$_POST", "$_GET", "filter_input", "request->get"]
        hits = 0
        for file in files:
            text = file.read_text(errors="ignore")
            hits += sum(1 for pattern in patterns if pattern.lower() in text.lower())
        passed = hits > 0
        return passed, f"Server-side input handling references: {hits}" if passed else "No server-side input handling found"


class DeterministicTestRunner:
    def __init__(self) -> None:
        # Weights indicate relative importance; sum of weights used for scoring ratio.
        self.rules: List[DeterministicRule] = [
            FilePresenceRule(name="Entry file present", description="Checks for index entry point", weight=2.0, targets=["index.html", "index.htm", "index.php"]),
            FormHandlingRule(name="Form handling", description="Forms include explicit method", weight=1.0, file_suffixes=[".html", ".htm", ".php"]),
            ClientApiRule(name="Client API", description="Client-side calls to APIs", weight=1.0, file_suffixes=[".js", ".html", ".htm"]),
            SqlUsageRule(name="SQL present", description="SQL scripts include statements", weight=1.0),
            ServerInputRule(name="Server input handling", description="Server code processes user input", weight=1.0, file_suffixes=[".php"]),
        ]

    def run(self, context: SubmissionContext) -> StepResult:
        reasons: List[str] = []
        total_weight = sum(rule.weight for rule in self.rules)
        achieved = 0.0

        for rule in self.rules:
            passed, reason = rule.evaluate(context)
            reasons.append(f"{rule.name}: {reason}")
            if passed:
                achieved += rule.weight

        if total_weight == 0:
            score = 0.0
            reasons.append("No deterministic rules configured")
        else:
            ratio = achieved / total_weight
            if ratio >= 0.75:
                score = 1.0
                reasons.append(f"Deterministic behavioural checks strong ({achieved}/{total_weight})")
            elif ratio >= 0.35:
                score = 0.5
                reasons.append(f"Deterministic behavioural checks partial ({achieved}/{total_weight})")
            else:
                score = 0.0
                reasons.append(f"Deterministic behavioural checks weak ({achieved}/{total_weight})")

        return StepResult(name="deterministic_tests", score=score, reasons=reasons)
