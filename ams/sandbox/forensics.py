"""Forensic utilities for retained threat containers.

When the threat scanner flags a submission, containers are retained
(``--rm`` removed) so instructors can inspect the filesystem state.
This module provides helper functions for listing, inspecting, and
cleaning up those containers.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Naming convention: ams-threat-{run_id} and ams-threat-pw-{run_id}
_THREAT_CONTAINER_PREFIX = "ams-threat-"


@dataclass
class RetainedContainer:
    """Metadata for a retained threat container."""
    name: str
    container_id: str
    status: str
    created: str
    image: str


def list_retained_containers() -> List[RetainedContainer]:
    """List all retained ``ams-threat-*`` containers.

    Returns an empty list if Docker is unavailable or no containers match.
    """
    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a",
                "--filter", f"name={_THREAT_CONTAINER_PREFIX}",
                "--format", "{{.Names}}\t{{.ID}}\t{{.Status}}\t{{.CreatedAt}}\t{{.Image}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        containers: List[RetainedContainer] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                containers.append(RetainedContainer(
                    name=parts[0],
                    container_id=parts[1],
                    status=parts[2],
                    created=parts[3],
                    image=parts[4],
                ))
        return containers

    except Exception as exc:
        logger.warning("Failed to list retained containers: %s", exc)
        return []


def inspect_container(container_name: str) -> Optional[dict]:
    """Return filesystem diff and logs for a retained container.

    Returns ``None`` if the container does not exist or Docker fails.
    """
    if not container_name.startswith(_THREAT_CONTAINER_PREFIX):
        logger.warning("Refusing to inspect non-threat container: %s", container_name)
        return None

    info: dict = {"name": container_name, "diff": [], "logs": ""}

    try:
        # Filesystem diff
        diff_result = subprocess.run(
            ["docker", "diff", container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if diff_result.returncode == 0:
            info["diff"] = diff_result.stdout.strip().splitlines()

        # Container logs
        log_result = subprocess.run(
            ["docker", "logs", "--tail", "200", container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if log_result.returncode == 0:
            info["logs"] = (log_result.stdout + log_result.stderr)[:10_000]

        return info

    except Exception as exc:
        logger.warning("Failed to inspect container %s: %s", container_name, exc)
        return None


def cleanup_container(container_name: str) -> bool:
    """Remove a retained threat container after review.

    Returns ``True`` if the container was successfully removed.
    """
    if not container_name.startswith(_THREAT_CONTAINER_PREFIX):
        logger.warning("Refusing to remove non-threat container: %s", container_name)
        return False

    try:
        result = subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info("Removed retained container: %s", container_name)
            return True
        else:
            logger.warning(
                "Failed to remove container %s: %s",
                container_name,
                result.stderr.strip(),
            )
            return False

    except Exception as exc:
        logger.warning("Failed to remove container %s: %s", container_name, exc)
        return False


def cleanup_all_retained() -> int:
    """Remove all retained threat containers. Returns the count removed."""
    containers = list_retained_containers()
    removed = 0
    for c in containers:
        if cleanup_container(c.name):
            removed += 1
    return removed


__all__ = [
    "RetainedContainer",
    "cleanup_all_retained",
    "cleanup_container",
    "inspect_container",
    "list_retained_containers",
]
