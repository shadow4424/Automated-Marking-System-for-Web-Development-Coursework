"""Background job manager for the AMS web interface.

Uses :class:`concurrent.futures.ThreadPoolExecutor` to run heavy marking
pipelines off the Flask request thread, preventing UI freezes and HTTP
timeouts.

Usage::

    from ams.core.job_manager import job_manager

    job_id = job_manager.submit_job("single_mark", pipeline.run, path, ...)
    status = job_manager.get_job_status(job_id)
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_WORKERS = 4


class JobManager:
    """Thread-safe in-memory job scheduler backed by a thread pool.

    A global semaphore ensures only one submission runs at a time so
    its batch-parallel LLM calls get full throughput from the model
    server (no slot contention with other submissions).
    """

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ams-job",
        )
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Only one submission may use the LLM at a time.
        self._llm_gate = threading.Semaphore(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit_job(
        self,
        task_type: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit *func* for background execution.

        Returns the generated ``job_id`` immediately.
        """
        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "task_type": task_type,
                "status": "processing",
                "progress": 0.0,
                "submitted_at": now,
                "completed_at": None,
                "result": None,
                "error": None,
            }

        self._executor.submit(self._run_job, job_id, func, *args, **kwargs)
        logger.info("Job %s (%s) submitted.", job_id, task_type)
        return job_id

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return a *snapshot* of the job state, or ``None`` if unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def update_progress(self, job_id: str, progress: float) -> None:
        """Allow tasks to report incremental progress (0.0 – 1.0)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["progress"] = min(max(progress, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Execute *func* inside the thread pool and record the outcome.

        Acquires ``_llm_gate`` so only one submission uses the LLM at a
        time, giving it full access to all model-server slots.
        """
        self._llm_gate.acquire()
        try:
            result = func(*args, **kwargs)
            self._finish(job_id, status="completed", result=result)
        except Exception as exc:
            logger.exception("Job %s failed.", job_id)
            self._finish(job_id, status="failed", error=str(exc))
        finally:
            self._llm_gate.release()

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = status
                job["progress"] = 1.0
                job["completed_at"] = now
                job["result"] = result
                job["error"] = error
        logger.info("Job %s finished with status=%s.", job_id, status)

    def shutdown(self, wait: bool = True) -> None:
        """Gracefully shut down the thread pool."""
        self._executor.shutdown(wait=wait)


# Global singleton — import this from other modules.
job_manager = JobManager()
