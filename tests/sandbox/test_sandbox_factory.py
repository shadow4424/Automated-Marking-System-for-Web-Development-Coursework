"""Tests for the sandbox runner factory."""
from __future__ import annotations

import os
import pytest

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    SubprocessRunner,
)
from ams.assessors.playwright_assessor import BrowserRunner, PlaywrightRunner
from ams.sandbox.config import SandboxConfig, SandboxMode, reset_sandbox_config
from ams.sandbox.factory import get_command_runner, get_browser_runner, SandboxUnavailableError


class TestGetCommandRunner:
    """Tests for get_command_runner factory function."""

    def setup_method(self):
        reset_sandbox_config()

    def teardown_method(self):
        reset_sandbox_config()
        for key in list(os.environ):
            if key.startswith("AMS_SANDBOX_"):
                del os.environ[key]

    def test_subprocess_mode_returns_subprocess_runner(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_command_runner(cfg)
        assert isinstance(runner, SubprocessRunner)

    def test_docker_mode_raises_when_docker_unavailable(self):
        """When Docker image doesn't exist, factory should raise SandboxUnavailableError."""
        cfg = SandboxConfig(
            mode=SandboxMode.DOCKER,
            image="nonexistent-image-ams-test:latest",
        )
        with pytest.raises(SandboxUnavailableError):
            get_command_runner(cfg)

    def test_explicit_subprocess_config_returns_subprocess(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_command_runner(cfg)
        assert isinstance(runner, SubprocessRunner)

    def test_runner_is_command_runner(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_command_runner(cfg)
        assert isinstance(runner, CommandRunner)


class TestGetBrowserRunner:
    """Tests for get_browser_runner factory function."""

    def setup_method(self):
        reset_sandbox_config()

    def teardown_method(self):
        reset_sandbox_config()
        for key in list(os.environ):
            if key.startswith("AMS_SANDBOX_"):
                del os.environ[key]

    def test_subprocess_mode_returns_playwright_runner(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_browser_runner(cfg)
        assert isinstance(runner, PlaywrightRunner)

    def test_explicit_subprocess_config_returns_playwright(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_browser_runner(cfg)
        assert isinstance(runner, PlaywrightRunner)

    def test_runner_is_browser_runner(self):
        cfg = SandboxConfig(mode=SandboxMode.SUBPROCESS)
        runner = get_browser_runner(cfg)
        assert isinstance(runner, BrowserRunner)
