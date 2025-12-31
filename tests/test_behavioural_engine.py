from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ams.assessors.behavioral.deterministic_test_engine import (
    CommandRunner,
    DeterministicTestEngine,
    RunResult,
)
from ams.io.submission import SubmissionProcessor
from ams.core.pipeline import AssessmentPipeline


class FakeRunner(CommandRunner):
    def __init__(self, outcomes: list[RunResult] | None = None, default: RunResult | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.default = default or RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False)
        self.calls: list[dict] = []

    def run(self, args, timeout: float, cwd: Path | None = None) -> RunResult:
        self.calls.append({"args": list(args), "timeout": timeout, "cwd": cwd})
        if self.outcomes:
            return self.outcomes.pop(0)
        return self.default


def test_behavioural_evidence_in_report_for_fullstack(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir), profile="fullstack")
        data = json.loads(report_path.read_text(encoding="utf-8"))

    evidence = data.get("behavioural_evidence") or []
    assert any(ev.get("test_id") == "PHP.SMOKE" for ev in evidence)
    assert any(ev.get("test_id") == "SQL.SQLITE_EXEC" for ev in evidence)


def test_behavioural_skips_for_frontend(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir), profile="frontend")
        data = json.loads(report_path.read_text(encoding="utf-8"))

    findings = data.get("findings") or []
    behavioural_fail = [
        f
        for f in findings
        if f.get("id", "").startswith("BEHAVIOUR.") and f.get("severity") == "FAIL"
    ]
    assert behavioural_fail == []


def test_sqlite_exec_records_success_flags(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    sql_file = submission_dir / "schema.sql"
    sql_file.write_text(
        "CREATE TABLE users(id INTEGER PRIMARY KEY);"
        "INSERT INTO users(id) VALUES (1);"
        "SELECT * FROM users;",
        encoding="utf-8",
    )

    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        context = processor.prepare(submission_dir, Path(workspace_dir), profile="fullstack")
        engine = DeterministicTestEngine(runner=FakeRunner([]))
        context.metadata["profile"] = "fullstack"
        findings = engine.run(context)

    sql_evidence = next(e for e in context.behavioural_evidence if e.test_id == "SQL.SQLITE_EXEC")
    assert sql_evidence.status == "pass"
    assert sql_evidence.outputs["schema_ok"]
    assert sql_evidence.outputs["insert_ok"]
    assert sql_evidence.outputs["select_ok"]
    assert any(f.id == "BEHAVIOUR.SQL_EXEC_PASS" for f in findings)


def test_sqlite_exec_handles_invalid_sql(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    sql_file = submission_dir / "broken.sql"
    sql_file.write_text("CREAT TABLE bad(id INTEGER); SELECT * FROM missing_table;", encoding="utf-8")

    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        context = processor.prepare(submission_dir, Path(workspace_dir), profile="fullstack")
        engine = DeterministicTestEngine(runner=FakeRunner([]))
        context.metadata["profile"] = "fullstack"
        findings = engine.run(context)

    sql_evidence = next(e for e in context.behavioural_evidence if e.test_id == "SQL.SQLITE_EXEC")
    assert sql_evidence.status == "fail"
    assert any(f.id == "BEHAVIOUR.SQL_EXEC_FAIL" for f in findings)
    assert sql_evidence.stderr


def test_php_entrypoint_prefers_form_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php echo 'index'; ?>", encoding="utf-8")
    (submission_dir / "handler.php").write_text("<?php echo 'handled'; ?>", encoding="utf-8")
    (submission_dir / "index.html").write_text(
        "<!doctype html><html><body><form action='handler.php'><input name='name'></form></body></html>",
        encoding="utf-8",
    )

    runner = FakeRunner(
        [
            RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
            RunResult(exit_code=0, stdout="ok", stderr="", duration_ms=5, timed_out=False),
        ]
    )
    monkeypatch.setattr(
        "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
        lambda _: "php",
    )

    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        context = processor.prepare(submission_dir, Path(workspace_dir), profile="fullstack")
        engine = DeterministicTestEngine(runner=runner)
        context.metadata["profile"] = "fullstack"
        engine.run(context)

    assert runner.calls, "Runner should have been invoked for PHP tests"
    first_call = runner.calls[0]["args"]
    assert first_call[-1].endswith("handler.php")


def test_timeout_produces_timeout_finding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()
    (submission_dir / "index.php").write_text("<?php echo 'slow'; ?>", encoding="utf-8")
    (submission_dir / "index.html").write_text("<html><body><form></form></body></html>", encoding="utf-8")

    runner = FakeRunner(
        [
            RunResult(exit_code=None, stdout="", stderr="", duration_ms=2000, timed_out=True),
            RunResult(exit_code=None, stdout="", stderr="", duration_ms=2000, timed_out=True),
        ]
    )
    monkeypatch.setattr(
        "ams.assessors.behavioral.deterministic_test_engine.shutil.which",
        lambda _: "php",
    )

    processor = SubmissionProcessor()
    with tempfile.TemporaryDirectory(prefix="ams-behavioural-") as workspace_dir:
        context = processor.prepare(submission_dir, Path(workspace_dir), profile="fullstack")
        engine = DeterministicTestEngine(runner=runner, per_test_timeout=0.1)
        context.metadata["profile"] = "fullstack"
        findings = engine.run(context)

    assert any(f.id == "BEHAVIOUR.PHP_SMOKE_TIMEOUT" for f in findings)
    php_evidence = next(e for e in context.behavioural_evidence if e.test_id == "PHP.SMOKE")
    assert php_evidence.status == "timeout"
