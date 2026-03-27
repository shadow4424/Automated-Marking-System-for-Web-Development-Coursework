from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping

from ams.assessors import Assessor
from ams.core.finding_ids import BEHAVIOUR as BEHID
from ams.core.models import BehaviouralEvidence, BrowserEvidence, Finding, FindingCategory, Severity, SubmissionContext
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
    calculator_test_results: dict | None = None
    hover_test_result: dict | None = None
    viewport_test_result: dict | None = None
    dom_structure: dict | None = None


class BrowserRunner:
    """Abstraction for browser automation to enable fake runners in tests."""

    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:  # pragma: no cover - interface
        raise NotImplementedError

    def run_calculator_tests(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Run calculator click sequences and return result dict. Override in subclasses."""
        return {"status": "skipped", "reason": "not_implemented"}

    def run_hover_test(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Run hover style test and return result dict. Override in subclasses."""
        return {"status": "skipped", "reason": "not_implemented"}

    def run_viewport_test(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Run viewport resize test and return result dict. Override in subclasses."""
        return {"status": "skipped", "reason": "not_implemented"}


class PlaywrightRunner(BrowserRunner):
    """Direct host-based Playwright runner (dev/test only).

    .. deprecated:: 2.0
        Production execution uses :class:`~ams.sandbox.playwright_docker.DockerPlaywrightRunner`.
        This class is retained only for local development and unit tests that
        inject it explicitly via the *runner* parameter of
        :class:`PlaywrightAssessor`.
    """

    def __init__(self, timeout_ms: int = 5000, output_cap: int = 10_000) -> None:
        self.timeout_ms = timeout_ms
        self.output_cap = output_cap

    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:  # pragma: no cover
        try:
            from playwright.sync_api import TimeoutError as PWTimeoutError, sync_playwright
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

    def run_calculator_tests(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Run calculator button click sequences and assert display output."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return {"status": "skipped", "reason": f"playwright_unavailable: {exc}"}

        sequences = [
            (["2", "+", "3", "="], "5"),
            (["9", "-", "4", "="], "5"),
            (["6", "*", "7", "="], "42"),
            (["8", "/", "2", "="], "4"),
        ]
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for seq, expected in sequences:
                try:
                    page = browser.new_page()
                    page.goto(entry_path.as_uri(), wait_until="load", timeout=5000)
                    for label in seq:
                        btn = page.query_selector(f"button:has-text('{label}')")
                        if btn:
                            btn.click()
                            page.wait_for_timeout(50)
                    display = page.query_selector("input[readonly], #theDisplay, #display, .display")
                    actual = (display.input_value() if display else "").strip() if display else ""
                    results.append({"seq": "".join(seq), "expected": expected, "actual": actual, "pass": actual == expected})
                    page.close()
                except Exception as exc:
                    results.append({"seq": "".join(seq), "expected": expected, "actual": None, "pass": False, "error": str(exc)})
            browser.close()

        passed = sum(1 for r in results if r["pass"])
        total = len(results)
        if passed == total:
            status = "pass"
        elif passed >= total // 2:
            status = "partial"
        else:
            status = "fail"
        return {"status": status, "passed": passed, "total": total, "results": results}

    def run_hover_test(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Test that hovering over links changes computed styles."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return {"status": "skipped", "reason": f"playwright_unavailable: {exc}"}

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(entry_path.as_uri(), wait_until="load", timeout=5000)
                links = page.query_selector_all("a")
                if not links:
                    browser.close()
                    return {"status": "skipped", "reason": "no_links_found"}
                link = links[0]
                color_before = page.evaluate("(el) => getComputedStyle(el).color", link)
                link.hover()
                page.wait_for_timeout(200)
                color_after = page.evaluate("(el) => getComputedStyle(el).color", link)
                changed = color_before != color_after
                browser.close()
                return {"status": "pass" if changed else "fail", "changed": changed, "before": color_before, "after": color_after}
            except Exception as exc:
                return {"status": "skipped", "reason": str(exc)}

    def run_viewport_test(self, entry_path: Path, workdir: Path) -> dict:  # pragma: no cover
        """Test layout at mobile (375x667) and desktop (1280x800) viewports."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return {"status": "skipped", "reason": f"playwright_unavailable: {exc}"}

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                mobile_ok, desktop_ok = True, True
                for width, height, label in [(375, 667, "mobile"), (1280, 800, "desktop")]:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.goto(entry_path.as_uri(), wait_until="load", timeout=5000)
                    # Check for horizontal overflow (a proxy for broken layout)
                    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
                    if label == "mobile":
                        mobile_ok = not overflow
                    else:
                        desktop_ok = not overflow
                    page.close()
                browser.close()
                status = "pass" if (mobile_ok and desktop_ok) else ("partial" if (mobile_ok or desktop_ok) else "fail")
                return {"status": status, "mobile_ok": mobile_ok, "desktop_ok": desktop_ok}
            except Exception as exc:
                return {"status": "skipped", "reason": str(exc)}


class PlaywrightAssessor(Assessor):
    """Minimal browser automation stage using Playwright (or fake runner)."""

    name = "browser_automation"

    def __init__(self, runner: BrowserRunner | None = None, output_cap: int = 10_000) -> None:
        if runner is not None:
            self.runner = runner
        else:
            from ams.sandbox.factory import get_browser_runner
            self.runner = get_browser_runner()
        self.output_cap = output_cap

    # ------------------------------------------------------------------
    # Multi-page screenshot capture for UX Review
    # ------------------------------------------------------------------

    def capture_all_pages(self, context: SubmissionContext) -> List[dict]:
        """Render every .html file in the submission and capture a
        full-page screenshot for each one.

        Returns a list of dicts::

            [{"page": "index.html", "screenshot": Path("…/index_html.png")}, …]

        The screenshots are saved under ``artifacts/browser/<filename>.png``.
        This method is intentionally separated from the scoring pipeline so
        it can be called independently for the UX Review feature.

        Execution goes through ``self.runner`` (a :class:`BrowserRunner`),
        which respects the active sandbox mode.  When Docker sandboxing is
        enabled, screenshots are captured inside the container.
        """
        import logging as _logging
        _logger = _logging.getLogger(__name__)

        html_files = sorted(context.files_for("html", relevant_only=True))
        if not html_files:
            return []

        results: List[dict] = []
        shot_dir = context.workspace_path / "artifacts" / "browser"
        shot_dir.mkdir(parents=True, exist_ok=True)

        for idx, html_path in enumerate(html_files):
            page_name = html_path.name  # e.g. "index.html"
            safe_stem = page_name.replace(".", "_")  # e.g. "index_html"
            shot_path = shot_dir / f"{safe_stem}.png"

            _logger.debug(
                "UX: evaluating page %d/%d %s",
                idx + 1, len(html_files), page_name,
            )

            try:
                # Run through the sandbox-aware BrowserRunner (interaction
                # disabled — we only need the screenshot, not DOM diffs).
                result = self.runner.run(
                    html_path, context.workspace_path, interaction=False,
                )
                # The runner may produce its own screenshots; copy the
                # first one to our canonical path if present.
                if result.screenshot_paths:
                    import shutil
                    src = Path(result.screenshot_paths[0])
                    if src.exists():
                        shutil.copy2(src, shot_path)
                        _logger.info(
                            "UX: success %s screenshot=%s size=%d",
                            page_name, shot_path,
                            shot_path.stat().st_size,
                        )
                        results.append({"page": page_name, "screenshot": shot_path})
                        continue

                # Runner didn't produce a usable screenshot — record the
                # page with screenshot=None so the pipeline can still emit
                # a NOT_EVALUATED finding instead of silently dropping it.
                _logger.warning(
                    "UX: failed %s error=no screenshot returned "
                    "(runner status=%s, notes=%s)",
                    page_name,
                    getattr(result, "status", "?"),
                    getattr(result, "notes", "")[:200],
                )
                results.append({"page": page_name, "screenshot": None})

            except Exception as exc:
                _logger.warning(
                    "UX: failed %s error=%s", page_name, exc,
                )
                results.append({"page": page_name, "screenshot": None})

        _logger.info(
            "capture_all_pages completed: %d/%d with screenshots",
            sum(1 for r in results if r["screenshot"] is not None),
            len(results),
        )
        return results

    def _capture_pages(self, context: SubmissionContext) -> tuple[Path | None, BrowserRunResult | None]:
        entry = self._select_entrypoint(context)
        if not entry:
            return None, None
        return entry, self.runner.run(entry, context.workspace_path, interaction=True)

    def _orchestrate_browser_tests(
        self,
        context: SubmissionContext,
        entry: Path,
        profile: str,
        profile_spec,
    ) -> List[Finding]:
        findings: List[Finding] = []
        if profile_spec:
            behavioral_test_types = {rule.test_type for rule in profile_spec.behavioral_rules}
            if "calculator_sequence" in behavioral_test_types or "calculator_display" in behavioral_test_types or "calculator_operator" in behavioral_test_types:
                findings.extend(self._run_calculator_behavioral(context, entry, profile))
            if "hover_check" in behavioral_test_types:
                findings.extend(self._run_hover_behavioral(context, entry, profile))
            if "viewport_resize" in behavioral_test_types:
                findings.extend(self._run_viewport_behavioral(context, entry, profile))
        return findings

    def _collect_browser_evidence(
        self,
        context: SubmissionContext,
        entry: Path,
        result: BrowserRunResult,
        profile: str,
    ) -> List[Finding]:
        findings: List[Finding] = []
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

        if (
            profile_spec
            and profile_spec.is_component_required("php")
            and context.files_for("php", relevant_only=True)
        ):
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

        captured_entry, result = self._capture_pages(context)
        if not captured_entry or not result:
            return findings

        findings.extend(self._collect_browser_evidence(context, captured_entry, result, profile))
        if result.status in {"timeout", "error"}:
            return findings
        findings.extend(self._orchestrate_browser_tests(context, captured_entry, profile, profile_spec))

        return findings

    def _run_calculator_behavioral(
        self, context: SubmissionContext, entry: Path, profile: str
    ) -> List[Finding]:
        """Run calculator interaction tests and emit behavioural evidence."""
        result = self.runner.run_calculator_tests(entry, context.workspace_path)
        status = result.get("status", "skipped")
        if status == "pass":
            finding_id = BEHID.CALCULATOR_PASS
            severity = Severity.INFO
        elif status == "partial":
            finding_id = BEHID.CALCULATOR_PARTIAL
            severity = Severity.WARN
        elif status == "skipped":
            finding_id = BEHID.CALCULATOR_SKIPPED
            severity = Severity.SKIPPED
        else:
            finding_id = BEHID.CALCULATOR_FAIL
            severity = Severity.FAIL
        context.behavioural_evidence.append(
            BehaviouralEvidence(
                test_id="BEHAVIOUR.CALCULATOR_SEQUENCE",
                component="js",
                status=status,
                outputs=result,
            )
        )
        return [self._finding(finding_id, f"Calculator tests: {status}", severity, profile=profile, evidence=result)]

    def _run_hover_behavioral(
        self, context: SubmissionContext, entry: Path, profile: str
    ) -> List[Finding]:
        """Run hover style test and emit behavioural evidence."""
        result = self.runner.run_hover_test(entry, context.workspace_path)
        status = result.get("status", "skipped")
        finding_id = BEHID.HOVER_PASS if status == "pass" else (BEHID.HOVER_SKIPPED if status == "skipped" else BEHID.HOVER_FAIL)
        severity = Severity.INFO if status == "pass" else (Severity.SKIPPED if status == "skipped" else Severity.WARN)
        context.behavioural_evidence.append(
            BehaviouralEvidence(
                test_id="BEHAVIOUR.HOVER_CHECK",
                component="css",
                status=status,
                outputs=result,
            )
        )
        return [self._finding(finding_id, f"Hover style test: {status}", severity, profile=profile, evidence=result)]

    def _run_viewport_behavioral(
        self, context: SubmissionContext, entry: Path, profile: str
    ) -> List[Finding]:
        """Run viewport resize test and emit behavioural evidence."""
        result = self.runner.run_viewport_test(entry, context.workspace_path)
        status = result.get("status", "skipped")
        finding_id = BEHID.VIEWPORT_PASS if status == "pass" else (BEHID.VIEWPORT_SKIPPED if status == "skipped" else BEHID.VIEWPORT_FAIL)
        severity = Severity.INFO if status == "pass" else (Severity.SKIPPED if status == "skipped" else Severity.WARN)
        context.behavioural_evidence.append(
            BehaviouralEvidence(
                test_id="BEHAVIOUR.VIEWPORT_RESIZE",
                component="css",
                status=status,
                outputs=result,
            )
        )
        return [self._finding(finding_id, f"Viewport resize test: {status}", severity, profile=profile, evidence=result)]

    def _select_entrypoint(self, context: SubmissionContext) -> Path | None:
        html_files = sorted(context.files_for("html", relevant_only=True))
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
