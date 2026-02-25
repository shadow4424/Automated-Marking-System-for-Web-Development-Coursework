"""Tests for sandbox configuration."""
from __future__ import annotations

import os
import pytest

from ams.sandbox.config import (
    SandboxConfig,
    SandboxMode,
    get_sandbox_config,
    reset_sandbox_config,
)


class TestSandboxConfig:
    """Unit tests for SandboxConfig dataclass."""

    def test_default_values(self):
        cfg = SandboxConfig()
        assert cfg.mode == SandboxMode.DOCKER
        assert cfg.image == "ams-sandbox:latest"
        assert cfg.cpu_limit == 1.0
        assert cfg.memory_limit == "512m"
        assert cfg.pids_limit == 64
        assert cfg.network_mode == "none"
        assert cfg.read_only_root is True
        assert cfg.drop_all_caps is True
        assert cfg.no_new_privileges is True
        assert cfg.user == "1000:1000"

    def test_docker_mode(self):
        cfg = SandboxConfig(mode=SandboxMode.DOCKER)
        assert cfg.mode == SandboxMode.DOCKER

    def test_custom_limits(self):
        cfg = SandboxConfig(cpu_limit=0.5, memory_limit="256m", pids_limit=32)
        assert cfg.cpu_limit == 0.5
        assert cfg.memory_limit == "256m"
        assert cfg.pids_limit == 32


class TestSandboxConfigFromEnv:
    """Tests for environment-based configuration."""

    def setup_method(self):
        reset_sandbox_config()

    def teardown_method(self):
        reset_sandbox_config()
        # Clean up env vars
        for key in list(os.environ):
            if key.startswith("AMS_SANDBOX_"):
                del os.environ[key]

    def test_from_env_defaults(self):
        # Remove the conftest autouse fixture's env var to test actual defaults
        os.environ.pop("AMS_SANDBOX_MODE", None)
        cfg = SandboxConfig.from_env()
        assert cfg.mode == SandboxMode.DOCKER
        assert cfg.image == "ams-sandbox:latest"

    def test_from_env_docker_mode(self):
        os.environ["AMS_SANDBOX_MODE"] = "docker"
        cfg = SandboxConfig.from_env()
        assert cfg.mode == SandboxMode.DOCKER

    def test_from_env_custom_limits(self):
        os.environ["AMS_SANDBOX_CPU_LIMIT"] = "0.5"
        os.environ["AMS_SANDBOX_MEMORY_LIMIT"] = "256m"
        os.environ["AMS_SANDBOX_PIDS_LIMIT"] = "32"
        cfg = SandboxConfig.from_env()
        assert cfg.cpu_limit == 0.5
        assert cfg.memory_limit == "256m"
        assert cfg.pids_limit == 32

    def test_from_env_unknown_mode_falls_back(self):
        os.environ["AMS_SANDBOX_MODE"] = "totally_invalid"
        cfg = SandboxConfig.from_env()
        assert cfg.mode == SandboxMode.DOCKER

    def test_from_env_custom_image(self):
        os.environ["AMS_SANDBOX_IMAGE"] = "my-custom:v2"
        cfg = SandboxConfig.from_env()
        assert cfg.image == "my-custom:v2"


class TestGetSandboxConfig:
    """Tests for the singleton config accessor."""

    def setup_method(self):
        reset_sandbox_config()

    def teardown_method(self):
        reset_sandbox_config()
        for key in list(os.environ):
            if key.startswith("AMS_SANDBOX_"):
                del os.environ[key]

    def test_returns_same_instance(self):
        cfg1 = get_sandbox_config()
        cfg2 = get_sandbox_config()
        assert cfg1 is cfg2

    def test_reset_clears_cache(self):
        cfg1 = get_sandbox_config()
        reset_sandbox_config()
        cfg2 = get_sandbox_config()
        assert cfg1 is not cfg2
