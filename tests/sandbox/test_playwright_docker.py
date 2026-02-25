"""Tests for DockerPlaywrightRunner."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ams.assessors.playwright_assessor import BrowserRunResult
from ams.sandbox.config import SandboxConfig, SandboxMode
from ams.sandbox.playwright_docker import DockerPlaywrightRunner


@pytest.fixture
def pw_config() -> SandboxConfig:
    return SandboxConfig(
        mode=SandboxMode.DOCKER,
        playwright_image="mcr.microsoft.com/playwright/python:v1.40.0-jammy",
        browser_timeout_ms=5000,
        network_mode="none",
    )


class TestDockerPlaywrightRunner:
    """Tests for DockerPlaywrightRunner (Docker CLI mocked)."""

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_successful_run(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html><body>Hello</body></html>")

        container_output = json.dumps({
            "status": "pass",
            "url": "file:///workspace/index.html",
            "duration_ms": 200,
            "dom_before": "<html><body>Hello</body></html>",
            "dom_after": "<html><body>Hello</body></html>",
            "console_errors": [],
            "network_errors": [],
            "actions": [{"type": "goto", "target": "index.html"}],
            "notes": "",
        })
        mock_run.return_value = MagicMock(
            returncode=0, stdout=container_output, stderr=""
        )

        runner = DockerPlaywrightRunner(pw_config)
        result = runner.run(entry, workdir, interaction=True)

        assert isinstance(result, BrowserRunResult)
        assert result.status == "pass"
        assert "Hello" in result.dom_before

        # Script file should be cleaned up
        assert not (workdir / "_ams_pw_script.py").exists()

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_timeout_handling(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html></html>")

        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="docker run ...", timeout=10.0
        )

        runner = DockerPlaywrightRunner(pw_config)
        result = runner.run(entry, workdir)

        assert result.status == "timeout"
        assert "timed out" in result.notes.lower()

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_container_error(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html></html>")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="container failed"
        )

        runner = DockerPlaywrightRunner(pw_config)
        result = runner.run(entry, workdir)

        assert result.status == "error"

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_invalid_json_output(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html></html>")

        mock_run.return_value = MagicMock(
            returncode=0, stdout="not valid json", stderr=""
        )

        runner = DockerPlaywrightRunner(pw_config)
        result = runner.run(entry, workdir)

        assert result.status == "error"

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_docker_cmd_includes_isolation_flags(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html></html>")

        container_output = json.dumps({"status": "pass"})
        mock_run.return_value = MagicMock(
            returncode=0, stdout=container_output, stderr=""
        )

        runner = DockerPlaywrightRunner(pw_config)
        runner.run(entry, workdir)

        call_args = mock_run.call_args[0][0]
        assert "--network" in call_args
        assert "none" in call_args
        assert "--memory" in call_args
        assert "--cpus" in call_args

    @patch("ams.sandbox.playwright_docker.subprocess.run")
    def test_generic_exception(self, mock_run, pw_config, tmp_path):
        workdir = tmp_path / "workspace"
        workdir.mkdir()
        entry = workdir / "index.html"
        entry.write_text("<html></html>")

        mock_run.side_effect = OSError("Docker daemon not running")

        runner = DockerPlaywrightRunner(pw_config)
        result = runner.run(entry, workdir)

        assert result.status == "error"
        assert "Docker daemon" in result.notes
