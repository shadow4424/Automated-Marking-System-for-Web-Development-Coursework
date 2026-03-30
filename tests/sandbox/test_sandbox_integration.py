"""Integration tests verifying that the sandbox wiring is correct."""
from __future__ import annotations

import os
import pytest

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    DeterministicTestEngine,
    SubprocessRunner,
)
from ams.assessors.playwright_assessor import (
    BrowserRunner,
    PlaywrightAssessor,
    PlaywrightRunner,
)
from ams.sandbox.config import SandboxMode, reset_sandbox_config


class TestDeterministicTestEngineWiring:
    """Verify DeterministicTestEngine uses the factory."""

    def setup_method(self):
        reset_sandbox_config()
        # Ensure subprocess mode (no Docker required for CI)
        os.environ["AMS_SANDBOX_MODE"] = "subprocess"

    def teardown_method(self):
        reset_sandbox_config()
        os.environ.pop("AMS_SANDBOX_MODE", None)

    def test_default_runner_is_subprocess(self):
        """Without Docker, the engine should get a SubprocessRunner."""
        engine = DeterministicTestEngine()
        assert isinstance(engine.runner, SubprocessRunner)

    def test_explicit_runner_is_respected(self):
        """Passing a runner explicitly should bypass the factory."""

        class FakeRunner(CommandRunner):
            def run(self, args, timeout, cwd=None):
                pass  # pragma: no cover

        runner = FakeRunner()
        engine = DeterministicTestEngine(runner=runner)
        assert engine.runner is runner


class TestPlaywrightAssessorWiring:
    """Verify PlaywrightAssessor uses the factory."""

    def setup_method(self):
        reset_sandbox_config()
        os.environ["AMS_SANDBOX_MODE"] = "subprocess"

    def teardown_method(self):
        reset_sandbox_config()
        os.environ.pop("AMS_SANDBOX_MODE", None)

    def test_default_runner_is_playwright(self):
        """Without Docker, the assessor should get a PlaywrightRunner."""
        assessor = PlaywrightAssessor()
        assert isinstance(assessor.runner, PlaywrightRunner)

    def test_explicit_runner_is_respected(self):
        """Passing a runner explicitly should bypass the factory."""

        class FakeBrowser(BrowserRunner):
            def run(self, entry_path, workdir, interaction=True):
                pass  # pragma: no cover

        browser = FakeBrowser()
        assessor = PlaywrightAssessor(runner=browser)
        assert assessor.runner is browser
