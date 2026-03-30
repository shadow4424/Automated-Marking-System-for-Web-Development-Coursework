"""Workspace Management Utilities. Provides utilities for creating and cleaning up run directories."""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ams.core.config import WORKSPACE_ROOT, WORKSPACE_MAX_AGE_HOURS

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages run workspaces: creation, cleanup, and organization. Attributes: root: Base directory for all runs."""

    def __init__(self, root: Optional[Path] = None):
        """Initialise WorkspaceManager. Args: root: Root directory for runs. Defaults to WORKSPACE_ROOT from config."""
        self.root = Path(root) if root else WORKSPACE_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def create_run_directory(
        self,
        prefix: str = "run",
        assignment_id: Optional[str] = None,
    ) -> Path:
        """Create a unique directory for a new run."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        unique_id = uuid.uuid4().hex[:8]

        # Structure: runs_root/[assignment_id/]prefix/TIMESTAMP_prefix_UNIQUE
        if assignment_id:
            run_parent = self.root / assignment_id / prefix
        else:
            run_parent = self.root / prefix

        run_name = f"{timestamp}_{prefix}_{unique_id}"
        run_dir = run_parent / run_name
        run_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Created run directory: {run_dir}")
        return run_dir

    def cleanup_old_runs(
        self,
        max_age_hours: Optional[int] = None,
        dry_run: bool = False,
    ) -> list[Path]:
        """Delete old run directories to prevent disk bloat."""
        if max_age_hours is None:
            max_age_hours = WORKSPACE_MAX_AGE_HOURS

        now = datetime.now(timezone.utc)
        cutoff_seconds = max_age_hours * 3600
        deleted: list[Path] = []

        if not self.root.exists():
            return deleted

        # Find all run directories (contain run_info.json)
        for run_info_path in self.root.rglob("run_info.json"):
            run_dir = run_info_path.parent

            try:
                # Check modification time of run_info.json
                mtime = run_info_path.stat().st_mtime
                age_seconds = (now.timestamp() - mtime)

                if age_seconds > cutoff_seconds:
                    if dry_run:
                        logger.info(f"Would delete (age={age_seconds/3600:.1f}h): {run_dir}")
                    else:
                        logger.info(f"Deleting old run (age={age_seconds/3600:.1f}h): {run_dir}")
                        shutil.rmtree(run_dir)
                    deleted.append(run_dir)
            except Exception as e:
                logger.warning(f"Error checking/deleting {run_dir}: {e}")

        if deleted:
            logger.info(f"Cleanup: {'would delete' if dry_run else 'deleted'} {len(deleted)} old runs")
        else:
            logger.debug("Cleanup: no old runs to delete")

        return deleted

    def get_disk_usage(self) -> dict:
        """Get disk usage statistics for the workspace. Returns: Dict with total_size_mb, run_count, and oldest_run info."""
        total_size = 0
        run_count = 0
        oldest_mtime = None

        if not self.root.exists():
            return {"total_size_mb": 0, "run_count": 0, "oldest_run": None}

        for run_info_path in self.root.rglob("run_info.json"):
            run_dir = run_info_path.parent
            run_count += 1

            # Calculate directory size
            for file_path in run_dir.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size

            # Track oldest run
            mtime = run_info_path.stat().st_mtime
            if oldest_mtime is None or mtime < oldest_mtime:
                oldest_mtime = mtime

        return {
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "run_count": run_count,
            "oldest_run": datetime.fromtimestamp(oldest_mtime, tz=timezone.utc).isoformat() if oldest_mtime else None,
        }


# Singleton instance for convenience
_default_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    """Get or create the default WorkspaceManager instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = WorkspaceManager()
    return _default_manager


def cleanup_old_runs(max_age_hours: Optional[int] = None) -> list[Path]:
    """Convenience function to clean up old runs using default manager."""
    return get_workspace_manager().cleanup_old_runs(max_age_hours)


__all__ = [
    "WorkspaceManager",
    "get_workspace_manager",
    "cleanup_old_runs",
]
