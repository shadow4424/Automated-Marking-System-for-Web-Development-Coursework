"""Tests for DockerCommandRunner."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ams.assessors.behavioral.deterministic_test_engine import RunResult
from ams.sandbox.config import SandboxConfig, SandboxMode
from ams.sandbox.docker_runner import DockerCommandRunner, is_docker_available


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def docker_config() -> SandboxConfig:
    """Config with Docker mode enabled."""
    return SandboxConfig(
        mode=SandboxMode.DOCKER,
        image="ams-sandbox:latest",
        cpu_limit=1.0,
        memory_limit="512m",
        pids_limit=64,
        network_mode="none",
        read_only_root=True,
        tmpfs_size="50m",
        user="1000:1000",
    )


# ---------------------------------------------------------------------------
# Unit tests (Docker CLI mocked)
# ---------------------------------------------------------------------------


class TestDockerCommandRunnerInit:
    """Tests for DockerCommandRunner initialization."""

    @patch("ams.sandbox.docker_runner.shutil.which", return_value=None)
    def test_raises_if_docker_not_on_path(self, _mock_which, docker_config):
        with pytest.raises(RuntimeError, match="Docker CLI is not on PATH"):
            DockerCommandRunner(docker_config)

    @patch("ams.sandbox.docker_runner.subprocess.run")
    @patch("ams.sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker")
    def test_raises_if_image_missing(self, _which, mock_run, docker_config):
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        with pytest.raises(RuntimeError, match="not found"):
            DockerCommandRunner(docker_config)

    @patch("ams.sandbox.docker_runner.subprocess.run")
    @patch("ams.sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker")
    def test_init_succeeds_with_image(self, _which, mock_run, docker_config):
        mock_run.return_value = MagicMock(stdout="abc123\n", returncode=0)
        runner = DockerCommandRunner(docker_config)
        assert runner.config is docker_config


class TestDockerCommandRunnerRun:
    """Tests for DockerCommandRunner.run() with mocked Docker CLI."""

    @patch("ams.sandbox.docker_runner.subprocess.run")
    @patch("ams.sandbox.docker_runner.shutil.which", return_value="/usr/bin/docker")
    def _make_runner(self, docker_config, mock_run_init, _which):
        """Helper: create a runner with mocked init."""
        mock_run_init.return_value = MagicMock(stdout="img123\n", returncode=0)
        return DockerCommandRunner(docker_config)

    def test_run_requires_cwd(self, docker_config):
        runner = self._make_runner(docker_config)
        with pytest.raises(ValueError, match="cwd must be specified"):
            runner.run(["php", "-v"], timeout=4.0, cwd=None)

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_run_returns_run_result(self, mock_run, docker_config, tmp_path):
        runner = self._make_runner(docker_config)

        # Create a fake submission directory
        sub = tmp_path / "submission"
        sub.mkdir()
        (sub / "test.php").write_text("<?php echo 'hi'; ?>")

        mock_run.return_value = MagicMock(
            returncode=0, stdout="hi", stderr="", timeout=None
        )

        result = runner.run(
            ["php", "-f", str(sub / "test.php")],
            timeout=4.0,
            cwd=sub,
        )
        assert isinstance(result, RunResult)
        assert result.stdout == "hi"
        assert result.exit_code == 0
        assert not result.timed_out

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_run_handles_timeout(self, mock_run, docker_config, tmp_path):
        runner = self._make_runner(docker_config)

        sub = tmp_path / "submission"
        sub.mkdir()

        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="docker run ...", timeout=4.0, output=b"", stderr=b"timed out"
        )

        result = runner.run(["php", "-f", "test.php"], timeout=4.0, cwd=sub)
        assert result.timed_out is True
        assert result.exit_code is None

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_run_handles_generic_exception(self, mock_run, docker_config, tmp_path):
        runner = self._make_runner(docker_config)

        sub = tmp_path / "submission"
        sub.mkdir()

        mock_run.side_effect = OSError("Something went wrong")

        result = runner.run(["php", "-f", "test.php"], timeout=4.0, cwd=sub)
        assert result.exit_code is None
        assert "Something went wrong" in result.stderr

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_docker_cmd_includes_security_flags(self, mock_run, docker_config, tmp_path):
        runner = self._make_runner(docker_config)

        sub = tmp_path / "submission"
        sub.mkdir()

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        runner.run(["php", "-v"], timeout=4.0, cwd=sub)

        # Inspect how Docker was invoked
        call_args = mock_run.call_args[0][0]
        assert "--network" in call_args
        assert "none" in call_args
        assert "--cap-drop" in call_args
        assert "ALL" in call_args
        assert "--read-only" in call_args
        assert "--memory" in call_args
        assert "512m" in call_args
        assert "--cpus" in call_args
        assert "--pids-limit" in call_args

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_path_rewriting(self, mock_run, docker_config, tmp_path):
        runner = self._make_runner(docker_config)

        sub = tmp_path / "submission"
        sub.mkdir()
        target = sub / "index.php"
        target.write_text("<?php echo 1; ?>")

        mock_run.return_value = MagicMock(returncode=0, stdout="1", stderr="")

        runner.run(
            ["php", "-f", str(target)],
            timeout=4.0,
            cwd=sub,
        )

        # The path to the PHP file should be rewritten to /submission/…
        call_args = mock_run.call_args[0][0]
        assert any("/submission" in a for a in call_args)


class TestResolveMount:
    """Tests for the static _resolve_mount helper."""

    def test_finds_submission_dir(self, tmp_path):
        sub = tmp_path / "workspace" / "submission"
        sub.mkdir(parents=True)
        inner = sub / "subdir"
        inner.mkdir()

        root, inner_cwd = DockerCommandRunner._resolve_mount(inner)
        assert root == sub.resolve()
        assert inner_cwd == "/submission/subdir"

    def test_submission_root_itself(self, tmp_path):
        sub = tmp_path / "submission"
        sub.mkdir()

        root, inner_cwd = DockerCommandRunner._resolve_mount(sub)
        assert root == sub.resolve()
        assert inner_cwd == "/submission"

    def test_fallback_when_no_submission_dir(self, tmp_path):
        some_dir = tmp_path / "other"
        some_dir.mkdir()

        root, inner_cwd = DockerCommandRunner._resolve_mount(some_dir)
        assert root == some_dir.resolve()
        assert inner_cwd == "/submission"


class TestIsDockerAvailable:
    """Tests for the is_docker_available utility."""

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_returns_true_when_docker_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert is_docker_available() is True

    @patch("ams.sandbox.docker_runner.subprocess.run")
    def test_returns_false_when_docker_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert is_docker_available() is False

    @patch("ams.sandbox.docker_runner.subprocess.run", side_effect=FileNotFoundError)
    def test_returns_false_when_docker_not_installed(self, _mock_run):
        assert is_docker_available() is False
