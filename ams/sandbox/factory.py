"""Factory functions for creating sandboxed or unsandboxed runners."""
from __future__ import annotations

import logging

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    SubprocessRunner,
)
from ams.assessors.playwright_assessor import BrowserRunner, PlaywrightRunner
from ams.sandbox.config import SandboxConfig, SandboxMode, get_sandbox_config

logger = logging.getLogger(__name__)


class SandboxUnavailableError(RuntimeError):
    """Raised when Docker sandboxing is required but not available."""


def get_command_runner(
    config: SandboxConfig | None = None,
    *,
    container_retain: bool = False,
    run_id: str | None = None,
) -> CommandRunner:
    """Return a CommandRunner appropriate for the configured sandbox mode."""
    cfg = config or get_sandbox_config()

    if cfg.mode == SandboxMode.DOCKER:
        try:
            from ams.sandbox.docker_runner import DockerCommandRunner

            runner = DockerCommandRunner(
                cfg,
                container_retain=container_retain,
                run_id=run_id,
            )
            logger.info("Using DockerCommandRunner (sandboxed execution).")
            return runner
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"DockerCommandRunner import failed ({exc}). "
                "Cannot run student code without Docker sandbox. "
                "Set AMS_SANDBOX_MODE=subprocess to bypass (not recommended)."
            ) from exc
        except RuntimeError as exc:
            raise SandboxUnavailableError(
                f"Docker sandbox unavailable: {exc}. "
                "Ensure Docker is running and the sandbox image is built "
                "(run docker/build.sh). "
                "Set AMS_SANDBOX_MODE=subprocess to bypass (not recommended)."
            ) from exc

    # Explicit SUBPROCESS mode — developer/testing only
    logger.warning(
        "Using SubprocessRunner (sandbox mode = subprocess). "
        "Student code will execute WITHOUT isolation."
    )
    return SubprocessRunner()


def get_browser_runner(
    config: SandboxConfig | None = None,
    *,
    container_retain: bool = False,
    run_id: str | None = None,
) -> BrowserRunner:
    """Return a BrowserRunner appropriate for the configured sandbox mode."""
    cfg = config or get_sandbox_config()

    if cfg.mode == SandboxMode.DOCKER:
        try:
            from ams.sandbox.playwright_docker import DockerPlaywrightRunner

            runner = DockerPlaywrightRunner(
                cfg,
                container_retain=container_retain,
                run_id=run_id,
            )
            logger.info("Using DockerPlaywrightRunner (sandboxed browser).")
            return runner
        except ImportError as exc:
            raise SandboxUnavailableError(
                f"DockerPlaywrightRunner import failed ({exc}). "
                "Cannot run browser tests without Docker sandbox. "
                "Set AMS_SANDBOX_MODE=subprocess to bypass (not recommended)."
            ) from exc
        except RuntimeError as exc:
            raise SandboxUnavailableError(
                f"Docker sandbox unavailable for Playwright: {exc}. "
                "Ensure Docker is running and the sandbox image is built. "
                "Set AMS_SANDBOX_MODE=subprocess to bypass (not recommended)."
            ) from exc

    # Explicit SUBPROCESS mode — developer/testing only
    logger.warning(
        "Using PlaywrightRunner (sandbox mode = subprocess). "
        "Browser tests will execute WITHOUT Docker isolation."
    )
    return PlaywrightRunner()


__all__ = ["get_command_runner", "get_browser_runner", "SandboxUnavailableError"]
