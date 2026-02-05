"""Shared pytest fixtures for the AMS test suite."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Mapping

import pytest

from ams.core.pipeline import AssessmentPipeline


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
