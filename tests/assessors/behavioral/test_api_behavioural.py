"""Tests for API behavioural execution engine (_api_exec)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import pytest

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    DeterministicTestEngine,
    RunResult,
)
from ams.core.models import Finding, SubmissionContext
from ams.io.submission import SubmissionProcessor


class FakeRunner(CommandRunner):
    """Controllable fake runner for deterministic test outcomes."""

    def __init__(
        self,
        outcomes: list[RunResult] | None = None,
        default: RunResult | None = None,
    ) -> None:
        self.outcomes = list(outcomes or [])
        self.default = default or RunResult(
            exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False
        )
        self.calls: list[dict] = []

    def run(self, args, timeout: float, cwd: Path | None = None) -> RunResult:
        self.calls.append({"args": list(args), "timeout": timeout, "cwd": cwd})
        if self.outcomes:
            return self.outcomes.pop(0)
        return self.default


def _make_context(tmp_path: Path, files: dict[str, str]) -> SubmissionContext:
    """Create a fullstack context with the given file contents."""
    submission = tmp_path / "submission"
    submission.mkdir(exist_ok=True)
    for name, content in files.items():
        (submission / name).write_text(content, encoding="utf-8")

    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-api-test-") as ws:
        context = processor.prepare(submission, Path(ws), profile="fullstack")
        # Re-point discovered files to submission dir since workspace may copy
        context.metadata["profile"] = "fullstack"
    return context


class TestApiExecDiscovery:
    """Tests for _discover_api_endpoint."""

    def test_discovers_json_content_type_endpoint(self, tmp_path: Path) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "api.php"
        api_file.write_text(
            "<?php header('Content-Type: application/json'); echo json_encode(['ok' => true]); ?>",
            encoding="utf-8",
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file]},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=FakeRunner())
        endpoint = engine._discover_api_endpoint(context)
        assert endpoint is not None
        assert endpoint.name == "api.php"

    def test_discovers_method_routing_endpoint(self, tmp_path: Path) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "rest.php"
        api_file.write_text(
            "<?php $m = $_SERVER['REQUEST_METHOD']; echo json_encode([]); ?>",
            encoding="utf-8",
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file]},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=FakeRunner())
        endpoint = engine._discover_api_endpoint(context)
        assert endpoint is not None

    def test_no_api_endpoint_returns_none(self, tmp_path: Path) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        index_file = submission / "index.php"
        index_file.write_text("<?php echo 'Hello'; ?>", encoding="utf-8")
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [index_file]},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=FakeRunner())
        endpoint = engine._discover_api_endpoint(context)
        assert endpoint is None

    def test_no_php_files_returns_none(self, tmp_path: Path) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=FakeRunner())
        assert engine._discover_api_endpoint(context) is None


class TestApiExecExecution:
    """Tests for _api_exec pass/fail/skip/timeout paths."""

    def test_api_exec_pass_with_valid_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "api.php"
        api_file.write_text(
            "<?php header('Content-Type: application/json'); echo json_encode(['ok' => true]); ?>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
            lambda _: "php",
        )
        runner = FakeRunner(
            [
                # php_smoke
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # php_form_injection
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # api_exec
                RunResult(
                    exit_code=0,
                    stdout='{"ok":true}',
                    stderr="",
                    duration_ms=10,
                    timed_out=False,
                ),
            ]
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file], "html": [], "sql": []},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=runner)
        findings = engine.run(context)

        api_pass = [f for f in findings if f.id == "BEHAVIOUR.API_EXEC_PASS"]
        assert len(api_pass) == 1
        json_valid = [f for f in findings if f.id == "BEHAVIOUR.API_JSON_VALID"]
        assert len(json_valid) == 1

    def test_api_exec_fail_with_invalid_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "api.php"
        api_file.write_text(
            "<?php header('Content-Type: application/json'); echo json_encode(['ok' => true]); ?>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
            lambda _: "php",
        )
        runner = FakeRunner(
            [
                # php_smoke
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # php_form_injection
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # api_exec returning invalid JSON
                RunResult(
                    exit_code=0,
                    stdout="<html>Not JSON</html>",
                    stderr="",
                    duration_ms=10,
                    timed_out=False,
                ),
            ]
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file], "html": [], "sql": []},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=runner)
        findings = engine.run(context)

        api_fail = [f for f in findings if f.id == "BEHAVIOUR.API_EXEC_FAIL"]
        assert len(api_fail) == 1
        json_invalid = [f for f in findings if f.id == "BEHAVIOUR.API_JSON_INVALID"]
        assert len(json_invalid) == 1

    def test_api_exec_skipped_no_endpoint(self, tmp_path: Path) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        index_file = submission / "index.php"
        index_file.write_text("<?php echo 'Hello'; ?>", encoding="utf-8")
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [index_file], "html": [], "sql": []},
            metadata={"profile": "fullstack"},
        )
        runner = FakeRunner(
            [
                # php_smoke
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # php_form_injection
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
            ]
        )
        engine = DeterministicTestEngine(runner=runner)
        findings = engine.run(context)

        api_skipped = [f for f in findings if f.id == "BEHAVIOUR.API_EXEC_SKIPPED"]
        assert len(api_skipped) == 1

    def test_api_exec_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "api.php"
        api_file.write_text(
            "<?php header('Content-Type: application/json'); echo json_encode([]); ?>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
            lambda _: "php",
        )
        runner = FakeRunner(
            [
                # php_smoke
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # php_form_injection
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                # api_exec times out
                RunResult(
                    exit_code=None,
                    stdout="",
                    stderr="",
                    duration_ms=5000,
                    timed_out=True,
                ),
            ]
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file], "html": [], "sql": []},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=runner)
        findings = engine.run(context)

        api_timeout = [f for f in findings if f.id == "BEHAVIOUR.API_EXEC_TIMEOUT"]
        assert len(api_timeout) == 1

    def test_api_exec_evidence_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        submission = tmp_path / "submission"
        submission.mkdir()
        api_file = submission / "api.php"
        api_file.write_text(
            "<?php header('Content-Type: application/json'); echo json_encode(['data' => 1]); ?>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
            lambda _: "php",
        )
        runner = FakeRunner(
            [
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
                RunResult(
                    exit_code=0,
                    stdout='{"data":1}',
                    stderr="",
                    duration_ms=8,
                    timed_out=False,
                ),
            ]
        )
        context = SubmissionContext(
            submission_path=submission,
            workspace_path=tmp_path,
            discovered_files={"php": [api_file], "html": [], "sql": []},
            metadata={"profile": "fullstack"},
        )
        engine = DeterministicTestEngine(runner=runner)
        engine.run(context)

        api_evidence = [
            e for e in context.behavioural_evidence if e.test_id == "API.EXEC"
        ]
        assert len(api_evidence) >= 1
        assert api_evidence[-1].status == "pass"
        assert api_evidence[-1].outputs["json_valid"] is True
