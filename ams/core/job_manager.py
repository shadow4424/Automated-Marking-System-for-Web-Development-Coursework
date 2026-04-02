from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

# Set up logging
logger = logging.getLogger(__name__)

# Configuration:
# Adjust based on LLM concurrency capabilities.
# Current LLM can only handle 4 requests at a time.
_MAX_WORKERS = 4

# Role is to manage background jobs, ensuring that only one LLM call happens at a time.
class JobManager:
    """Thread-safe in-memory job scheduler backed by a thread pool."""

    # Initialisation
    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        """Return the."""
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="ams-job",
        )
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        # Only one submission may use the LLM at a time.
        self._llm_gate = threading.Semaphore(1)

    # Function to submit and track jobs
    def submit_job(
        self,
        task_type: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit *func* for background execution. Returns the generated job_id immediately."""
        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Record the job in the registry with initial state.
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

    # Status and progress tracking
    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return a *snapshot* of the job state, or None if unknown."""
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    # Progress updates
    def update_progress(self, job_id: str, progress: float) -> None:
        """Allow tasks to report incremental progress (0.0 – 1.0)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["progress"] = min(max(progress, 0.0), 1.0)

    # Internal method to run the job and handle completion.
    def _run_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Execute *func* inside the thread pool and record the outcome."""
        self._llm_gate.acquire()
        try:
            result = func(*args, **kwargs)
            self._finish(job_id, status="completed", result=result)
        except Exception as exc:
            logger.exception("Job %s failed.", job_id)
            self._finish(job_id, status="failed", error=str(exc))
        finally:
            self._llm_gate.release()

    # Internal method to update job state on completion.
    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """Return finish."""
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
