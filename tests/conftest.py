"""Shared pytest fixtures for the AMS test suite."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

import pytest

from ams.core.config import ScoringMode
from ams.core.pipeline import AssessmentPipeline


# Database isolation — use a per-test SQLite file so tests never share state
# In the submission_attempts (or any other) table.


@pytest.fixture(autouse=True)
def _isolate_database(tmp_path, monkeypatch):
    """Redirect ams.core.database to a fresh per-test SQLite database."""
    import ams.core.database as _db_module

    test_db = tmp_path / "test_ams.db"
    monkeypatch.setattr(_db_module, "_DEFAULT_DB_PATH", test_db)

    # Initialise schema so the fresh DB has all required tables.
    _db_module.init_db()
    yield


# Global sandbox fixture — force subprocess mode for all tests so that the
# Test suite does not require a running Docker daemon. Individual sandbox
# Tests that need to test Docker behaviour mock the prerequisites instead.


@pytest.fixture(autouse=True)
def _force_subprocess_sandbox():
    """Ensure all tests run in subprocess sandbox mode by default."""
    prev = os.environ.get("AMS_SANDBOX_MODE")
    os.environ["AMS_SANDBOX_MODE"] = "subprocess"

    from ams.sandbox.config import reset_sandbox_config
    reset_sandbox_config()

    yield

    # Restore previous env var
    if prev is None:
        os.environ.pop("AMS_SANDBOX_MODE", None)
    else:
        os.environ["AMS_SANDBOX_MODE"] = prev
    reset_sandbox_config()


@pytest.fixture(autouse=True)
def _disable_batch_llm_by_default(request, monkeypatch):
    """Force batch tests onto STATIC_ONLY unless a test explicitly opts into LLM."""
    if request.node.get_closest_marker("uses_llm"):
        yield
        return

    class _StaticOnlyBatchPipeline(AssessmentPipeline):
        def __init__(self, *args, **kwargs):
            kwargs["scoring_mode"] = ScoringMode.STATIC_ONLY
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("ams.tools.batch.AssessmentPipeline", _StaticOnlyBatchPipeline)
    yield


@pytest.fixture
def build_submission(tmp_path: Path):
    """Create a submission directory from a mapping of relative paths -> file contents."""

    def _build(files: Mapping[str, str]) -> Path:
        submission_dir = tmp_path / "submission"
        submission_dir.mkdir()
        for rel_path, content in files.items():
            dest = submission_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        return submission_dir

    return _build


@pytest.fixture
def run_pipeline():
    """Run the assessment pipeline for a prepared submission and return the JSON report."""

    def _run(submission_dir: Path, profile: str = "frontend") -> dict:
        pipeline = AssessmentPipeline()
        with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
            report_path = pipeline.run(submission_dir, Path(workspace_dir), profile=profile)
            return json.loads(report_path.read_text(encoding="utf-8"))

    return _run
