"""Browser-automation-inspired checks.

True browser execution is optional to keep execution deterministic in restricted
sandboxes. When Playwright is unavailable, the runner performs DOM-oriented
heuristics on the HTML files and clearly reports the limitation.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import List

from .models import StepResult, SubmissionContext
from .normalisation import iter_files_with_suffix


class BrowserAutomationRunner:
    def __init__(self, enable_playwright: bool = False) -> None:
        self.enable_playwright = enable_playwright

    def run(self, context: SubmissionContext) -> StepResult:
        reasons: List[str] = []
        html_files = iter_files_with_suffix(context.normalized_files, (".html", ".htm"))

        if not html_files:
            reasons.append("No HTML files available for browser checks")
            return StepResult(name="browser_checks", score=0.0, reasons=reasons)

        if self.enable_playwright and importlib.util.find_spec("playwright"):
            # Placeholder for deterministic browser runs. Not implemented to keep
            # the environment light; the log states the chosen path.
            reasons.append(
                "Playwright detected but execution disabled by default for determinism; fallback to static DOM heuristics"
            )
        else:
            reasons.append("Playwright not enabled; running heuristic DOM checks")

        interactive_elements = 0
        form_controls = 0
        for file in html_files:
            text = file.read_text(errors="ignore")
            interactive_elements += len(re.findall(r"<button[^>]*>", text, flags=re.IGNORECASE))
            form_controls += len(re.findall(r"<input[^>]*>", text, flags=re.IGNORECASE))
        if interactive_elements > 0 and form_controls > 0:
            score = 1.0
            reasons.append(
                f"Detected interactive controls (buttons:{interactive_elements}, inputs:{form_controls})"
            )
        elif interactive_elements + form_controls > 0:
            score = 0.5
            reasons.append(
                f"Limited interactivity detected (buttons:{interactive_elements}, inputs:{form_controls})"
            )
        else:
            score = 0.0
            reasons.append("No interactive elements detected in HTML for automation checks")

        return StepResult(name="browser_checks", score=score, reasons=reasons)
