"""Tests for the container forensics utilities."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ams.sandbox.forensics import (
    RetainedContainer,
    cleanup_all_retained,
    cleanup_container,
    inspect_container,
    list_retained_containers,
)


# List_retained_containers

class TestListRetainedContainers:
    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_containers(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=(
                "ams-threat-run1\tabc123\tExited (0)\t2025-02-25 10:00:00\tams-sandbox:latest\n"
                "ams-threat-pw-run1\tdef456\tExited (0)\t2025-02-25 10:01:00\tams-pw:latest\n"
            ),
        )
        containers = list_retained_containers()
        assert len(containers) == 2
        assert containers[0].name == "ams-threat-run1"
        assert containers[0].container_id == "abc123"
        assert containers[1].name == "ams-threat-pw-run1"

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_empty_on_no_output(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="",
        )
        assert list_retained_containers() == []

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_empty_on_nonzero_exit(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error",
        )
        assert list_retained_containers() == []

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_empty_on_exception(self, mock_run: MagicMock):
        mock_run.side_effect = FileNotFoundError("docker not found")
        assert list_retained_containers() == []

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_skips_malformed_lines(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="has\tonly\tthree\tfields\n",
        )
        assert list_retained_containers() == []


# Inspect_container

class TestInspectContainer:
    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_diff_and_logs(self, mock_run: MagicMock):
        def side_effect(args, **kwargs):
            if "diff" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout="C /var/www/backdoor.php\nA /tmp/evil\n",
                )
            elif "logs" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout="some log output\n",
                    stderr="some stderr\n",
                )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="")

        mock_run.side_effect = side_effect
        info = inspect_container("ams-threat-test1")
        assert info is not None
        assert info["name"] == "ams-threat-test1"
        assert len(info["diff"]) == 2
        assert "some log output" in info["logs"]

    def test_rejects_non_threat_container(self):
        """Should refuse to inspect containers without the threat prefix."""
        result = inspect_container("my-random-container")
        assert result is None

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_none_on_exception(self, mock_run: MagicMock):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=10)
        result = inspect_container("ams-threat-broken")
        assert result is None


# Cleanup_container

class TestCleanupContainer:
    @patch("ams.sandbox.forensics.subprocess.run")
    def test_successful_removal(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ams-threat-x\n",
        )
        assert cleanup_container("ams-threat-x") is True

    def test_rejects_non_threat_container(self):
        """Should refuse to remove containers without the threat prefix."""
        assert cleanup_container("production-database") is False

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_false_on_failure(self, mock_run: MagicMock):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no such container",
        )
        assert cleanup_container("ams-threat-gone") is False

    @patch("ams.sandbox.forensics.subprocess.run")
    def test_returns_false_on_exception(self, mock_run: MagicMock):
        mock_run.side_effect = OSError("docker not found")
        assert cleanup_container("ams-threat-err") is False


# Cleanup_all_retained

class TestCleanupAllRetained:
    @patch("ams.sandbox.forensics.cleanup_container")
    @patch("ams.sandbox.forensics.list_retained_containers")
    def test_removes_all_and_returns_count(self, mock_list, mock_cleanup):
        mock_list.return_value = [
            RetainedContainer("ams-threat-a", "aa", "Exited", "2025-01-01", "img"),
            RetainedContainer("ams-threat-b", "bb", "Exited", "2025-01-01", "img"),
        ]
        mock_cleanup.return_value = True
        assert cleanup_all_retained() == 2
        assert mock_cleanup.call_count == 2

    @patch("ams.sandbox.forensics.cleanup_container")
    @patch("ams.sandbox.forensics.list_retained_containers")
    def test_partial_failure(self, mock_list, mock_cleanup):
        mock_list.return_value = [
            RetainedContainer("ams-threat-a", "aa", "Exited", "2025-01-01", "img"),
            RetainedContainer("ams-threat-b", "bb", "Exited", "2025-01-01", "img"),
        ]
        mock_cleanup.side_effect = [True, False]
        assert cleanup_all_retained() == 1

    @patch("ams.sandbox.forensics.list_retained_containers")
    def test_empty_when_none_found(self, mock_list):
        mock_list.return_value = []
        assert cleanup_all_retained() == 0
