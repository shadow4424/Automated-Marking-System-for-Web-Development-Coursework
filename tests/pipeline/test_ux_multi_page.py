"""Tests for multi-page UX review pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from ams.assessors.playwright_assessor import (
    BrowserRunResult,
    BrowserRunner,
    PlaywrightAssessor,
)
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.pipeline import AssessmentPipeline
from ams.llm.providers import LocalLMStudioProvider, LLMResponse
from ams.llm.schemas import UXReviewResult


# Fixtures


PAGES = ["index.html", "about.html", "contact.html"]


@pytest.fixture
def workspace(tmp_path) -> Path:
    """Create a 3-page workspace with fake runner screenshots."""
    sub = tmp_path / "submission"
    sub.mkdir()
    for p in PAGES:
        (sub / p).write_text(f"<html><body>{p}</body></html>")

    # Runner screenshots live in a SEPARATE directory
    runner_dir = tmp_path / "_runner_shots"
    runner_dir.mkdir()
    for p in PAGES:
        safe = p.replace(".", "_")
        shot = runner_dir / f"{safe}.png"
        shot.write_bytes(b"\x89PNG" + b"\x00" * 600)  # > 500 bytes

    return tmp_path


# Build a submission context for the UX review tests.
@pytest.fixture
def context(workspace) -> SubmissionContext:
    sub = workspace / "submission"
    html_paths = sorted(sub.glob("*.html"))
    return SubmissionContext(
        workspace_path=workspace,
        submission_path=sub,
        discovered_files={"html": html_paths},
        metadata={"profile": "frontend"},
    )


@pytest.fixture
def fake_image(tmp_path) -> Path:
    """Create a small valid JPEG-like image for provider tests."""
    try:
        from PIL import Image
        img = Image.new("RGB", (800, 600), color="blue")
        p = tmp_path / "test_image.png"
        img.save(str(p))
        return p
    except ImportError:
        pytest.skip("Pillow required for provider retry tests")


class FakeRunner(BrowserRunner):
    """A stub runner that returns pre-built screenshots per call."""

    # Store the fake UX reviews returned by the stub analyst.
    def __init__(self, runner_shot_dir: Path, pages: list[str]):
        self._shot_dir = runner_shot_dir
        self._pages = pages
        self.call_log: list[str] = []

    # Return the prepared UX reviews for the test run.
    def run(self, entry_path: Path, workdir: Path, interaction: bool = True) -> BrowserRunResult:
        page = entry_path.name
        self.call_log.append(page)
        safe = page.replace(".", "_")
        shot = self._shot_dir / f"{safe}.png"
        return BrowserRunResult(
            status="pass",
            screenshot_paths=[str(shot)] if shot.exists() else [],
        )


# Tests


class TestMultiPageUXReview:
    """Ensure every page produces its own UX review finding."""

    def test_capture_all_pages_returns_entry_per_page(self, context, workspace):
        """Capture_all_pages must return one entry per HTML file."""
        runner_dir = workspace / "_runner_shots"
        runner = FakeRunner(runner_dir, PAGES)
        pa = PlaywrightAssessor(runner=runner)

        results = pa.capture_all_pages(context)

        assert len(results) == 3
        page_names = [r["page"] for r in results]
        for p in PAGES:
            assert p in page_names, f"Missing page: {p}"
        for r in results:
            assert r["screenshot"] is not None, f"Screenshot missing for {r['page']}"

    def test_capture_all_pages_records_failures(self, context, workspace):
        """Pages with no screenshot should still appear with screenshot=None."""
        runner_dir = workspace / "_runner_shots"

        # Delete one screenshot so that page fails
        (runner_dir / "about_html.png").unlink()

        runner = FakeRunner(runner_dir, PAGES)
        pa = PlaywrightAssessor(runner=runner)

        results = pa.capture_all_pages(context)

        assert len(results) == 3
        about = next(r for r in results if r["page"] == "about.html")
        assert about["screenshot"] is None

    def test_run_ux_reviews_produces_findings_for_all_pages(self, context, workspace):
        """_run_ux_reviews must produce one finding per page."""
        runner_dir = workspace / "_runner_shots"
        runner = FakeRunner(runner_dir, PAGES)
        pa = PlaywrightAssessor(runner=runner)

        # Mock VisionAnalyst to return distinct per-page results
        mock_analyst = MagicMock()
        call_counter = {"n": 0}

        # Return the fake UX review for this screenshot.
        def fake_review_ux(screenshot_path, page_name, context_note=None):
            call_counter["n"] += 1
            return UXReviewResult(
                page=page_name,
                status="NEEDS_IMPROVEMENT",
                feedback=f"Feedback for {page_name} #{call_counter['n']}",
                improvement_recommendation=f"Improve {page_name}",
                screenshot=screenshot_path,
                model="mock",
            )

        mock_analyst.review_ux = fake_review_ux

        pipeline = AssessmentPipeline()
        pipeline._vision_analyst = mock_analyst
        pipeline._vision_enabled = True

        # Inject our PlaywrightAssessor with the fake runner
        pipeline.assessors = [pa]

        ux_findings, ux_evidence = pipeline._run_ux_reviews(
            context, "frontend", static_findings=[],
        )

        # Must have exactly 3 findings
        assert len(ux_findings) == 3, (
            f"Expected 3 UX findings, got {len(ux_findings)}: "
            f"{[f.id for f in ux_findings]}"
        )
        assert len(ux_evidence) == 3

        # Each page must appear exactly once
        finding_pages = [f.evidence["page"] for f in ux_findings]
        for p in PAGES:
            assert p in finding_pages, f"Missing UX finding for {p}"

        # Findings should have distinct IDs
        finding_ids = [f.id for f in ux_findings]
        assert len(set(finding_ids)) == 3, f"Duplicate finding IDs: {finding_ids}"

        # Each finding must have its own ux_review content (not shared/overwritten)
        feedbacks = [f.evidence["ux_review"]["feedback"] for f in ux_findings]
        assert len(set(feedbacks)) == 3, f"Duplicate feedbacks (shared state bug): {feedbacks}"

    def test_failed_screenshot_produces_not_evaluated_finding(self, context, workspace):
        """Pages where the screenshot fails must get a NOT_EVALUATED finding."""
        runner_dir = workspace / "_runner_shots"

        # Delete screenshots for about.html and contact.html
        (runner_dir / "about_html.png").unlink()
        (runner_dir / "contact_html.png").unlink()

        runner = FakeRunner(runner_dir, PAGES)
        pa = PlaywrightAssessor(runner=runner)

        mock_analyst = MagicMock()
        mock_analyst.review_ux.return_value = UXReviewResult(
            page="index.html",
            status="PASS",
            feedback="Looks good",
            screenshot="x",
            model="mock",
        )

        pipeline = AssessmentPipeline()
        pipeline._vision_analyst = mock_analyst
        pipeline._vision_enabled = True
        pipeline.assessors = [pa]

        ux_findings, _ = pipeline._run_ux_reviews(
            context, "frontend", static_findings=[],
        )

        assert len(ux_findings) == 3

        # Index.html should have real feedback
        idx_f = next(f for f in ux_findings if f.evidence["page"] == "index.html")
        assert idx_f.evidence["ux_review"]["status"] != "NOT_EVALUATED"

        # About and contact should be NOT_EVALUATED
        for name in ["about.html", "contact.html"]:
            nf = next(f for f in ux_findings if f.evidence["page"] == name)
            assert nf.evidence["ux_review"]["status"] == "NOT_EVALUATED", (
                f"{name} should be NOT_EVALUATED but got {nf.evidence['ux_review']['status']}"
            )


# Provider Adaptive Retry / Backoff Tests


class TestProviderAdaptiveRetry:
    """Verify that LocalLMStudioProvider retries on slot/OOM errors."""

    @patch("ams.llm.providers.time.sleep")  # Don't actually sleep in tests
    def test_retry_recovers_on_slot_error(self, mock_sleep, fake_image):
        """First attempt fails with 'failed to process image', second succeeds."""
        provider = LocalLMStudioProvider(
            base_url="http://localhost:1234/v1",
            model="test-model",
        )

        # Build a mock OpenAI client
        mock_client = MagicMock()

        # First call raises retryable error, second call succeeds
        mock_choice = MagicMock()
        mock_choice.message.content = '{"status": "PASS", "feedback": "ok"}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(
            prompt_tokens=100, completion_tokens=50, total_tokens=150,
        )
        mock_response.model_dump.return_value = {}

        mock_client.chat.completions.create.side_effect = [
            Exception("Error code: 400 - failed to process image"),
            mock_response,
        ]
        provider._client = mock_client

        # Track _encode_image calls to verify shrinking max_size
        original_encode = provider._encode_image
        encode_calls: list[int] = []

        # Track which screenshots were encoded during the run.
        def tracking_encode(image_path, max_size=768):
            encode_calls.append(max_size)
            return original_encode(image_path, max_size=max_size)

        provider._encode_image = tracking_encode

        result = provider.complete(
            prompt="Analyze this page",
            image_path=str(fake_image),
            json_mode=True,
        )

        # Should succeed (second attempt)
        assert result.success, f"Expected success but got error: {result.error}"
        assert result.content  # Non-empty response

        # Provider should have called the API twice
        assert mock_client.chat.completions.create.call_count == 2

        # Encode_image should have been called twice with decreasing max_size.
        assert len(encode_calls) == 2
        assert encode_calls[0] > encode_calls[1], (
            f"Expected decreasing max_size, got {encode_calls}"
        )
        # First call at default (768), second at 80% (614)
        assert encode_calls[0] == 768
        assert encode_calls[1] == int(768 * 0.8)  # 614

        # Should have slept once (0.5s before attempt 2)
        mock_sleep.assert_called_once_with(0.5)

    @patch("ams.llm.providers.time.sleep")
    def test_all_retries_exhausted_returns_error(self, mock_sleep, fake_image):
        """If all 3 attempts fail, return an error response (don't crash)."""
        provider = LocalLMStudioProvider(
            base_url="http://localhost:1234/v1",
            model="test-model",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(
            "failed to process image"
        )
        provider._client = mock_client

        result = provider.complete(
            prompt="Analyze",
            image_path=str(fake_image),
        )

        # Should return a clean error, not raise
        assert not result.success
        assert "failed to process image" in result.error

        # Should have tried 3 times total
        assert mock_client.chat.completions.create.call_count == 3

        # Should have slept twice (before attempt 2 and 3)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)
        mock_sleep.assert_any_call(1.5)

    @patch("ams.llm.providers.time.sleep")
    def test_non_retryable_error_not_retried(self, mock_sleep, fake_image):
        """Non-OOM errors (e.g. connection refused) should NOT be retried."""
        provider = LocalLMStudioProvider(
            base_url="http://localhost:1234/v1",
            model="test-model",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception(
            "Connection refused"
        )
        provider._client = mock_client

        result = provider.complete(
            prompt="Analyze",
            image_path=str(fake_image),
        )

        # Should fail immediately without retrying
        assert not result.success
        assert mock_client.chat.completions.create.call_count == 1
        mock_sleep.assert_not_called()

    def test_encode_image_produces_jpeg_mime(self, fake_image):
        """The image encoder should always produce image/jpeg MIME type."""
        provider = LocalLMStudioProvider(
            base_url="http://localhost:1234/v1",
            model="test-model",
        )

        base64_str, mime = provider._encode_image(str(fake_image))
        assert mime == "image/jpeg"
        assert len(base64_str) > 0

