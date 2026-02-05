from __future__ import annotations

import tempfile
from pathlib import Path

from ams.assessors.browser.playwright_assessor import (
    BrowserRunResult,
    BrowserRunner,
    PlaywrightAssessor,
)
from ams.io.submission import SubmissionProcessor


class FakeRunner(BrowserRunner):
    def __init__(self, result: BrowserRunResult) -> None:
        self.result = result
        self.calls: list[Path] = []

    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:
        self.calls.append(entry_path)
        return self.result


def _make_context(tmp_path: Path, html: str, profile: str = "frontend"):
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text(html, encoding="utf-8")
    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-browser-") as workspace_dir:
        context = processor.prepare(submission_dir, Path(workspace_dir), profile=profile)
        context.metadata["profile"] = profile
        yield context


def test_browser_stage_runs_and_emits_evidence(tmp_path: Path) -> None:
    result = BrowserRunResult(
        status="pass",
        url="file://index.html",
        duration_ms=120,
        dom_before="<html></html>",
        dom_after="<html><body>after</body></html>",
        actions=[{"type": "goto"}],
    )
    runner = FakeRunner(result)
    assessor = PlaywrightAssessor(runner=runner)

    for context in _make_context(tmp_path, "<html><body><button>Go</button></body></html>"):
        findings = assessor.run(context)

    assert any(f.id == "BROWSER.PAGE_LOAD_PASS" for f in findings)
    assert context.browser_evidence
    assert context.browser_evidence[0].status == "pass"


def test_interaction_skipped_when_no_controls(tmp_path: Path) -> None:
    result = BrowserRunResult(
        status="pass",
        url="file://index.html",
        duration_ms=50,
        dom_before="<html></html>",
        dom_after="<html></html>",
        actions=[{"type": "interaction_skipped", "reason": "no form/button found"}],
    )
    assessor = PlaywrightAssessor(runner=FakeRunner(result))
    for context in _make_context(tmp_path, "<html><body></body></html>"):
        findings = assessor.run(context)

    assert any(f.id == "BROWSER.INTERACTION_SKIPPED" for f in findings)


def test_console_errors_emitted(tmp_path: Path) -> None:
    result = BrowserRunResult(
        status="pass",
        url="file://index.html",
        duration_ms=80,
        dom_before="<html></html>",
        dom_after="<html></html>",
        actions=[{"type": "goto"}],
        console_errors=["ReferenceError: x is not defined"] * 2,
    )
    assessor = PlaywrightAssessor(runner=FakeRunner(result))
    for context in _make_context(tmp_path, "<html><body><script>console.error('x')</script></body></html>"):
        findings = assessor.run(context)

    assert any(f.id == "BROWSER.CONSOLE_ERRORS_PRESENT" for f in findings)
    evidence = context.browser_evidence[0]
    assert len(evidence.console_errors) == 2


def test_timeout_maps_to_timeout_finding(tmp_path: Path) -> None:
    result = BrowserRunResult(
        status="timeout",
        url="file://index.html",
        duration_ms=5000,
        dom_before="",
        dom_after="",
        actions=[{"type": "goto"}],
    )
    assessor = PlaywrightAssessor(runner=FakeRunner(result))
    for context in _make_context(tmp_path, "<html><body></body></html>"):
        findings = assessor.run(context)

    assert any(f.id == "BROWSER.PAGE_LOAD_TIMEOUT" for f in findings)
    assert context.browser_evidence[0].status == "timeout"
