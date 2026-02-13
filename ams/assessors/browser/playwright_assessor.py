from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping

from ams.assessors.base import Assessor
from ams.core.models import BrowserEvidence, Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


def _cap(text: str, limit: int = 10_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


@dataclass
class BrowserRunResult:
    status: str
    url: str = ""
    duration_ms: int = 0
    dom_before: str = ""
    dom_after: str = ""
    console_errors: List[str] | None = None
    network_errors: List[str] | None = None
    actions: List[Mapping[str, object]] | None = None
    screenshot_paths: List[str] | None = None
    notes: str = ""


class BrowserRunner:
    """Abstraction for browser automation to enable fake runners in tests."""

    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:  # pragma: no cover - interface
        raise NotImplementedError


class PlaywrightRunner(BrowserRunner):
    def __init__(self, timeout_ms: int = 5000, output_cap: int = 10_000) -> None:
        self.timeout_ms = timeout_ms
        self.output_cap = output_cap

    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:  # pragma: no cover
        try:
            from playwright.sync_api import Playwright, TimeoutError as PWTimeoutError, sync_playwright
        except Exception as exc:
            return BrowserRunResult(status="skipped", notes=f"Playwright unavailable: {exc}")

        actions: List[Mapping[str, object]] = []
        console_errors: List[str] = []
        network_errors: List[str] = []
        screenshot_paths: List[str] = []

        def _handle_console(msg) -> None:
            if msg.type == "error":
                console_errors.append(_cap(msg.text, self.output_cap))

        def _handle_failed_request(request) -> None:
            network_errors.append(_cap(f"{request.url}: {request.failure}", self.output_cap))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("console", _handle_console)
            page.on("requestfailed", _handle_failed_request)
            url = entry_path.as_uri()
            actions.append({"type": "goto", "target": entry_path.name})
            try:
                start = time.time()
                page.goto(url, wait_until="load", timeout=self.timeout_ms)
                dom_before = _cap(page.content(), self.output_cap)

                if interaction:
                    did_interact = False
                    form = page.query_selector("form")
                    if form:
                        text_input = form.query_selector("input[type=text], input:not([type])")
                        if text_input:
                            text_input.fill("test")
                        submit_btn = form.query_selector("button[type=submit], input[type=submit]")
                        if submit_btn:
                            submit_btn.click()
                        else:
                            with contextlib.suppress(Exception):
                                form.evaluate("form.submit()")
                        actions.append({"type": "form_submit", "selector": "form"})
                        did_interact = True
                    else:
                        button = page.query_selector("button")
                        if button:
                            button.click()
                            actions.append({"type": "click", "selector": "button"})
                            did_interact = True
                    if did_interact:
                        page.wait_for_timeout(500)
                    else:
                        actions.append({"type": "interaction_skipped", "reason": "no form/button found"})

                dom_after = _cap(page.content(), self.output_cap)
                shot_dir = workdir / "artifacts" / "browser"
                shot_dir.mkdir(parents=True, exist_ok=True)
                shot_path = shot_dir / f"{int(time.time() * 1000)}.png"
                page.screenshot(path=str(shot_path), full_page=True)
                screenshot_paths.append(str(shot_path))
                duration_ms = int((time.time() - start) * 1000)
                browser.close()
                return BrowserRunResult(
                    status="pass",
                    url=url,
                    duration_ms=duration_ms,
                    dom_before=dom_before,
                    dom_after=dom_after,
                    console_errors=console_errors[:20],
                    network_errors=network_errors[:20],
                    actions=actions,
                    screenshot_paths=screenshot_paths,
                    notes="",
                )
            except PWTimeoutError:
                browser.close()
                return BrowserRunResult(
                    status="timeout",
                    url=url,
                    notes="Page load timeout",
                    actions=actions,
                )
            except Exception as exc:
                browser.close()
                return BrowserRunResult(
                    status="error",
                    url=url,
                    notes=str(exc),
                    actions=actions,
                    console_errors=console_errors[:20],
                    network_errors=network_errors[:20],
                )


class PlaywrightAssessor(Assessor):
    """Minimal browser automation stage using Playwright (or fake runner)."""

    name = "browser_automation"

    def __init__(self, runner: BrowserRunner | None = None, output_cap: int = 10_000) -> None:
        self.runner = runner or PlaywrightRunner()
        self.output_cap = output_cap

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        profile = context.metadata.get("profile", "unknown")
        try:
            profile_spec = get_profile_spec(profile)
        except ValueError:
            profile_spec = None

        entry = self._select_entrypoint(context)
        if not entry:
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_SKIPPED",
                    "No HTML entrypoint found for browser automation.",
                    Severity.SKIPPED,
                    profile=profile,
                    evidence={"reason": "no_html_found"},
                )
            )
            return findings

        if profile_spec and profile_spec.name == "fullstack" and context.discovered_files.get("php"):
            # warn or skip if PHP present and we cannot serve dynamically
            findings.append(
                self._finding(
                    "BROWSER.PHP_BACKEND_LIMITATION",
                    "Browser automation limited to static load; PHP not served.",
                    Severity.WARN,
                    profile=profile,
                    evidence={"entry": str(entry)},
                )
            )

        result = self.runner.run(entry, context.workspace_path, interaction=True)
        evidence = BrowserEvidence(
            test_id="BROWSER.PAGE",
            status=result.status,
            duration_ms=result.duration_ms,
            url=result.url,
            actions=result.actions or [],
            dom_before=_cap(result.dom_before or "", self.output_cap),
            dom_after=_cap(result.dom_after or "", self.output_cap),
            console_errors=[_cap(e, self.output_cap) for e in (result.console_errors or [])][:20],
            network_errors=[_cap(e, self.output_cap) for e in (result.network_errors or [])][:20],
            screenshot_paths=result.screenshot_paths or [],
            notes=_cap(result.notes or "", self.output_cap),
        )
        context.browser_evidence.append(evidence)

        if result.status == "timeout":
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_TIMEOUT",
                    "Browser page load timed out.",
                    Severity.FAIL,
                    profile=profile,
                    evidence={"entry": str(entry)},
                )
            )
            return findings
        if result.status == "error":
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_FAIL",
                    "Browser automation failed.",
                    Severity.FAIL,
                    profile=profile,
                    evidence={"entry": str(entry), "error": _cap(result.notes or "", 1000)},
                )
            )
            return findings
        if result.status == "skipped":
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_SKIPPED",
                    "Browser automation skipped.",
                    Severity.SKIPPED,
                    profile=profile,
                    evidence={"entry": str(entry), "reason": result.notes or "skipped"},
                )
            )
        else:
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_PASS",
                    "Page loaded successfully in browser.",
                    Severity.INFO,
                    profile=profile,
                    evidence={"entry": str(entry)},
                )
            )

        # Interaction findings
        interacted = any(a.get("type") in {"form_submit", "click"} for a in evidence.actions)
        if any(a.get("type") == "interaction_skipped" for a in evidence.actions):
            findings.append(
                self._finding(
                    "BROWSER.INTERACTION_SKIPPED",
                    "No form or button found for deterministic interaction.",
                    Severity.SKIPPED,
                    profile=profile,
                    evidence={"entry": str(entry)},
                )
            )
        elif interacted and result.status == "pass":
            findings.append(
                self._finding(
                    "BROWSER.INTERACTION_PASS",
                    "Deterministic interaction executed.",
                    Severity.INFO,
                    profile=profile,
                    evidence={"actions": evidence.actions},
                )
            )
        elif interacted and result.status != "pass":
            findings.append(
                self._finding(
                    "BROWSER.INTERACTION_FAIL",
                    "Interaction attempted but failed.",
                    Severity.WARN,
                    profile=profile,
                    evidence={"status": result.status, "actions": evidence.actions},
                )
            )

        # Console findings
        if evidence.console_errors:
            findings.append(
                self._finding(
                    "BROWSER.CONSOLE_ERRORS_PRESENT",
                    "Console errors observed during browser automation.",
                    Severity.WARN,
                    profile=profile,
                    evidence={"count": len(evidence.console_errors), "first": evidence.console_errors[0]},
                )
            )
        else:
            findings.append(
                self._finding(
                    "BROWSER.CONSOLE_CLEAN",
                    "No console errors observed.",
                    Severity.INFO,
                    profile=profile,
                    evidence={"count": 0},
                )
            )

        return findings

    def _select_entrypoint(self, context: SubmissionContext) -> Path | None:
        html_files = sorted(context.discovered_files.get("html", []))
        if not html_files:
            return None
        for path in html_files:
            if path.name.lower() == "index.html":
                return path
        return html_files[0]

    def _finding(
        self,
        code: str,
        message: str,
        severity: Severity,
        profile: str,
        evidence: Mapping[str, object] | None = None,
        required: bool | None = False,
    ) -> Finding:
        evidence_data = dict(evidence or {})
        if profile is not None and "profile" not in evidence_data:
            evidence_data["profile"] = profile
        if required is not None and "required" not in evidence_data:
            evidence_data["required"] = required
        return Finding(
            id=code,
            category="browser",
            message=message,
            severity=severity,
            evidence=evidence_data,
            source=self.name,
            finding_category=FindingCategory.BEHAVIORAL,
            profile=profile,
            required=required,
        )


__all__ = ["PlaywrightAssessor", "BrowserRunner", "PlaywrightRunner", "BrowserRunResult"]
