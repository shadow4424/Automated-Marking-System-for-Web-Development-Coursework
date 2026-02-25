"""Sandbox module for secure execution of student submissions.

Provides Docker-based isolation for running untrusted student code
with resource limits, filesystem isolation, and network restrictions.
"""
from __future__ import annotations

from ams.sandbox.config import SandboxConfig, SandboxMode, get_sandbox_status
from ams.sandbox.factory import get_command_runner, get_browser_runner, SandboxUnavailableError

__all__ = [
    "SandboxConfig",
    "SandboxMode",
    "SandboxUnavailableError",
    "get_command_runner",
    "get_browser_runner",
    "get_sandbox_status",
]
