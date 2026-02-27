"""Sandbox configuration for AMS.

Controls whether Docker-based sandboxing is used for student code execution,
and specifies resource limits for containers.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class SandboxMode(str, Enum):
    """Execution mode for student code."""
    SUBPROCESS = "subprocess"   # Direct subprocess (no isolation) – legacy/testing only
    DOCKER = "docker"           # Docker container with full isolation (default)


@dataclass
class SandboxConfig:
    """Configuration for Docker sandbox resource limits and policies.

    All values can be overridden via environment variables prefixed with
    ``AMS_SANDBOX_``.  For example ``AMS_SANDBOX_MEMORY_LIMIT=256m``.

    The default mode is **DOCKER** so student code is always executed inside
    an isolated container.  Set ``AMS_SANDBOX_MODE=subprocess`` only for
    local development or testing where Docker is not available.
    """

    # ── Execution mode ──────────────────────────────────────────────
    mode: SandboxMode = SandboxMode.DOCKER

    # ── Docker image ────────────────────────────────────────────────
    image: str = "ams-sandbox:latest"

    # ── Resource limits ─────────────────────────────────────────────
    cpu_limit: float = 1.0          # CPU cores
    memory_limit: str = "512m"      # Docker memory limit string
    disk_quota: str = "100m"        # Storage limit (overlay2 driver)
    pids_limit: int = 64            # Max number of processes

    # ── Network ─────────────────────────────────────────────────────
    network_mode: str = "none"      # "none" = fully isolated

    # ── Filesystem ──────────────────────────────────────────────────
    read_only_root: bool = True     # Mount root filesystem read-only
    tmpfs_size: str = "50m"         # Writable tmpfs for /tmp

    # ── Security ────────────────────────────────────────────────────
    drop_all_caps: bool = True      # --cap-drop ALL
    no_new_privileges: bool = True  # --security-opt no-new-privileges
    seccomp_profile: str | None = None   # Path to seccomp JSON (None = Docker default)
    user: str = "1000:1000"         # UID:GID inside container

    # ── Playwright-specific ─────────────────────────────────────────
    playwright_image: str = "ams-sandbox:latest"
    browser_timeout_ms: int = 8000  # Timeout for browser operations inside container

    @classmethod
    def from_env(cls) -> SandboxConfig:
        """Build configuration from environment variables.

        Environment variables (all optional, shown with defaults):
            AMS_SANDBOX_MODE=subprocess
            AMS_SANDBOX_IMAGE=ams-sandbox:latest
            AMS_SANDBOX_CPU_LIMIT=1.0
            AMS_SANDBOX_MEMORY_LIMIT=512m
            AMS_SANDBOX_DISK_QUOTA=100m
            AMS_SANDBOX_PIDS_LIMIT=64
            AMS_SANDBOX_NETWORK_MODE=none
            AMS_SANDBOX_TMPFS_SIZE=50m
            AMS_SANDBOX_USER=1000:1000
        """
        mode_str = os.environ.get("AMS_SANDBOX_MODE", "docker").lower()
        try:
            mode = SandboxMode(mode_str)
        except ValueError:
            logger.warning("Unknown AMS_SANDBOX_MODE=%r, falling back to docker", mode_str)
            mode = SandboxMode.DOCKER

        return cls(
            mode=mode,
            image=os.environ.get("AMS_SANDBOX_IMAGE", cls.image),
            cpu_limit=float(os.environ.get("AMS_SANDBOX_CPU_LIMIT", str(cls.cpu_limit))),
            memory_limit=os.environ.get("AMS_SANDBOX_MEMORY_LIMIT", cls.memory_limit),
            disk_quota=os.environ.get("AMS_SANDBOX_DISK_QUOTA", cls.disk_quota),
            pids_limit=int(os.environ.get("AMS_SANDBOX_PIDS_LIMIT", str(cls.pids_limit))),
            network_mode=os.environ.get("AMS_SANDBOX_NETWORK_MODE", cls.network_mode),
            tmpfs_size=os.environ.get("AMS_SANDBOX_TMPFS_SIZE", cls.tmpfs_size),
            user=os.environ.get("AMS_SANDBOX_USER", cls.user),
        )


# Singleton — lazily populated on first access via ``get_sandbox_config()``.
_config: SandboxConfig | None = None


def get_sandbox_config() -> SandboxConfig:
    """Return the global sandbox configuration (created once from env)."""
    global _config
    if _config is None:
        _config = SandboxConfig.from_env()
    return _config


def reset_sandbox_config() -> None:
    """Reset the cached config — mainly for testing."""
    global _config
    _config = None


def get_sandbox_status() -> dict:
    """Return a status dict describing the current sandbox state.

    Useful for CLI banners and WebUI status indicators.

    Returns a dict with keys:
        mode:            'docker' or 'subprocess'
        docker_available: bool – True if Docker daemon is reachable
        image_available:  bool – True if the sandbox image exists
        enforced:         bool – True when mode is DOCKER and Docker is ready
        message:          str  – human-readable status summary
    """
    import shutil
    import subprocess as _sp

    cfg = get_sandbox_config()
    status: dict = {
        "mode": cfg.mode.value,
        "docker_available": False,
        "image_available": False,
        "enforced": False,
        "message": "",
    }

    # Check Docker CLI
    if not shutil.which("docker"):
        status["message"] = (
            "Docker CLI not found on PATH. Sandboxing is NOT active — "
            "student code will run unsandboxed."
        )
        return status

    # Check Docker daemon
    try:
        res = _sp.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if res.returncode != 0:
            status["message"] = (
                "Docker daemon is not running. Sandboxing is NOT active."
            )
            return status
    except Exception:
        status["message"] = "Unable to reach Docker daemon."
        return status

    status["docker_available"] = True

    # Check sandbox image
    try:
        res = _sp.run(
            ["docker", "images", "-q", cfg.image],
            capture_output=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout.strip():
            status["image_available"] = True
    except Exception:
        pass

    if cfg.mode == SandboxMode.DOCKER and status["docker_available"] and status["image_available"]:
        status["enforced"] = True
        status["message"] = (
            f"Sandbox ACTIVE — all student code runs inside Docker "
            f"({cfg.image}, {cfg.memory_limit} RAM, {cfg.cpu_limit} CPU, "
            f"network={cfg.network_mode})."
        )
    elif cfg.mode == SandboxMode.DOCKER and status["docker_available"] and not status["image_available"]:
        status["message"] = (
            f"Docker is available but sandbox image '{cfg.image}' is missing. "
            f"Run 'docker/build.sh' to build it. Sandboxing will NOT be active."
        )
    elif cfg.mode == SandboxMode.SUBPROCESS:
        status["message"] = (
            "Sandbox mode is set to 'subprocess' (no isolation). "
            "Set AMS_SANDBOX_MODE=docker for full sandboxing."
        )
    else:
        status["message"] = "Sandbox is not active."

    return status


__all__ = [
    "SandboxConfig",
    "SandboxMode",
    "get_sandbox_config",
    "reset_sandbox_config",
    "get_sandbox_status",
]
