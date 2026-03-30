"""Docker-based sandboxed command execution."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    RunResult,
)
from ams.sandbox.config import SandboxConfig, get_sandbox_config

logger = logging.getLogger(__name__)


class DockerCommandRunner(CommandRunner):
    """Execute commands inside an ephemeral Docker container."""

    def __init__(
        self,
        config: SandboxConfig | None = None,
        container_retain: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.config = config or get_sandbox_config()
        self.container_retain = container_retain
        self.run_id = run_id
        self._validate_prerequisites()


    # Validation


    def _validate_prerequisites(self) -> None:
        """Ensure Docker CLI is available and the sandbox image exists."""
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker CLI is not on PATH.  Install Docker Desktop or "
                "Docker Engine to enable sandboxed execution."
            )
        result = subprocess.run(
            ["docker", "images", "-q", self.config.image],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not result.stdout.strip():
            raise RuntimeError(
                f"Docker image '{self.config.image}' not found.  "
                "Build it with:  docker build -f docker/Dockerfile.sandbox "
                "-t ams-sandbox:latest docker/"
            )


    # CommandRunner interface


    def run(
        self,
        args: Sequence[str],
        timeout: float,
        cwd: Path | None = None,
    ) -> RunResult:
        """Run *args* inside a Docker container."""
        start = time.time()

        if cwd is None:
            raise ValueError("cwd must be specified for DockerCommandRunner")

        submission_root, inner_cwd = self._resolve_mount(cwd)

        # Rewrite any arguments that reference the host submission path so
        # They resolve under /submission inside the container.
        rewritten_args = self._rewrite_args(args, submission_root)

        docker_cmd = self._build_docker_cmd(
            rewritten_args,
            submission_root=submission_root,
            inner_cwd=inner_cwd,
        )

        logger.debug("Docker command: %s", " ".join(docker_cmd))

        try:
            completed = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 2,   # +2 s grace for container start
            )
            if completed.returncode != 0:
                logger.debug(
                    "Docker command exited with code %s. stderr=%s",
                    completed.returncode,
                    _decode(completed.stderr)[:500],
                )

            duration_ms = int((time.time() - start) * 1000)
            return RunResult(
                exit_code=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                duration_ms=duration_ms,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.time() - start) * 1000)
            # Attempt to kill any running container (best-effort)
            return RunResult(
                exit_code=None,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                duration_ms=duration_ms,
                timed_out=True,
            )
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            logger.error("Docker execution failed: %s", exc)
            return RunResult(
                exit_code=None,
                stdout="",
                stderr=str(exc),
                duration_ms=duration_ms,
                timed_out=False,
            )


    # Helpers


    def _build_docker_cmd(
        self,
        args: Sequence[str],
        *,
        submission_root: Path,
        inner_cwd: str,
    ) -> list[str]:
        """Assemble the docker run … CLI invocation."""
        cfg = self.config

        cmd: list[str] = [
            "docker", "run",
        ]

        # Container retention: skip --rm and assign a name when threats detected
        if self.container_retain and self.run_id:
            cmd.extend(["--name", f"ams-threat-{self.run_id}"])
        else:
            cmd.append("--rm")

        security_flags = self._build_docker_security_flags()
        volume_mounts = self._build_docker_volume_mounts(
            submission_root=submission_root,
            inner_cwd=inner_cwd,
        )

        cmd.extend([
            # Resource limits
            "--cpus", str(cfg.cpu_limit),
            "--memory", cfg.memory_limit,
            "--pids-limit", str(cfg.pids_limit),
            # Network
            "--network", cfg.network_mode,
        ])
        cmd.extend(security_flags)
        cmd.extend(volume_mounts)

        # Image + command
        cmd.append(cfg.image)
        cmd.extend(args)

        return cmd

    def _build_docker_security_flags(self) -> list[str]:
        """Build Docker security-hardening flags."""
        cfg = self.config
        flags: list[str] = []
        if cfg.read_only_root:
            flags.append("--read-only")
        if cfg.drop_all_caps:
            flags.extend(["--cap-drop", "ALL"])
        if cfg.no_new_privileges:
            flags.extend(["--security-opt", "no-new-privileges"])
        if cfg.seccomp_profile:
            flags.extend(["--security-opt", f"seccomp={cfg.seccomp_profile}"])
        return flags

    def _build_docker_volume_mounts(
        self,
        *,
        submission_root: Path,
        inner_cwd: str,
    ) -> list[str]:
        """Build Docker mount and working-directory flags."""
        cfg = self.config
        return [
            "-v", f"{submission_root.resolve()}:/submission:ro",
            "--tmpfs", f"/tmp:rw,noexec,size={cfg.tmpfs_size}",
            "-w", inner_cwd,
            "--user", cfg.user,
        ]

    @staticmethod
    def _resolve_mount(cwd: Path) -> tuple[Path, str]:
        """Find the submission root and the container-internal cwd."""
        resolved = cwd.resolve()
        current = resolved
        while current != current.parent:
            if current.name == "submission":
                # The relative path from the submission root to the real cwd
                try:
                    rel = resolved.relative_to(current)
                except ValueError:
                    rel = Path(".")
                inner_cwd = f"/submission/{rel.as_posix()}" if str(rel) != "." else "/submission"
                return current, inner_cwd
            current = current.parent

        # Fallback: mount *cwd* itself as /submission
        return resolved, "/submission"

    @staticmethod
    def _rewrite_args(
        args: Sequence[str],
        submission_root: Path,
    ) -> list[str]:
        """Replace host paths with container paths in arguments."""
        root_str = str(submission_root.resolve())
        root_posix = submission_root.resolve().as_posix()
        rewritten: list[str] = []
        for arg in args:
            if root_str in arg:
                # Replace host path and fix Windows backslashes → forward slashes
                arg = arg.replace(root_str, "/submission").replace("\\", "/")
            elif root_posix in arg:
                arg = arg.replace(root_posix, "/submission")
            rewritten.append(arg)
        return rewritten


def _decode(data: bytes | str | None) -> str:
    """Safely decode bytes from subprocess output."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def is_docker_available() -> bool:
    """Quick check whether the Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


__all__ = ["DockerCommandRunner", "is_docker_available"]
