from __future__ import annotations

import json
from pathlib import Path

from ams.analytics.assignment_analytics import generate_assignment_analytics
from ams.core.assignment_store import create_assignment
from ams.core.database import init_db
from ams.io.web_storage import list_runs, save_run_info
from ams.webui import create_app
from tests.webui.conftest import authenticate_client


def _use_temp_db(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "ams_users.db"
    monkeypatch.setattr("ams.core.database._DEFAULT_DB_PATH", db_path)
    init_db()
    return db_path


def _write_report(
    run_dir: Path,
    *,
    student_id: str,
    assignment_id: str,
    original_filename: str,
    attempt_id: str,
    attempt_number: int,
    source_type: str,
    submitted_at: str,
    score: float,
    llm_error: bool = False,
    syntax_error: bool = False,
) -> Path:
    report_path = run_dir / "report.json"
    findings = []
    if llm_error:
        findings.append(
            {
                "id": "LLM.ERROR.REQUIRES_REVIEW",
                "severity": "WARN",
                "evidence": {
                    "llm_error_message": "Provider timeout during grading.",
                    "llm_error_messages": ["Provider timeout during grading."],
                },
            }
        )
    if syntax_error:
        findings.append(
            {
                "id": "JS.SYNTAX_SUSPECT",
                "severity": "FAIL",
                "message": "JavaScript contains syntax mistakes that affect execution.",
                "evidence": {"file": "script.js", "reason": "unexpected token"},
            }
        )
    report_path.write_text(
        json.dumps(
            {
                "summary": {"confidence": "high"},
                "scores": {
                    "overall": score,
                    "by_component": {
                        "html": {"score": score},
                        "css": {"score": score},
                        "js": {"score": score},
                    },
                },
                "score_evidence": {
                    "confidence": {"level": "high"},
                    "review": {"recommended": llm_error},
                },
                "findings": findings,
                "metadata": {
                    "submission_metadata": {
                        "student_id": student_id,
                        "assignment_id": assignment_id,
                        "original_filename": original_filename,
                        "timestamp": submitted_at,
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "source_type": source_type,
                    },
                    "student_identity": {
                        "student_id": student_id,
                        "name_normalized": student_id,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def _make_attempt_run(
    runs_root: Path,
    *,
    run_id: str,
    attempt_number: int,
    created_at: str,
    student_id: str,
    assignment_id: str,
    source_type: str = "student_zip_upload",
    status: str = "completed",
    score: float | None = None,
    llm_error: bool = False,
    syntax_error: bool = False,
) -> Path:
    run_dir = runs_root / assignment_id / student_id / "attempts" / f"{attempt_number:03d}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_info = {
        "id": run_id,
        "mode": "mark",
        "profile": "frontend",
        "created_at": created_at,
        "student_id": student_id,
        "assignment_id": assignment_id,
        "original_filename": f"{student_id}_{assignment_id}.zip",
        "status": status,
        "attempt_id": run_id,
        "attempt_number": attempt_number,
        "source_type": source_type,
        "source_actor_user_id": "admin123" if source_type.startswith("teacher") else student_id,
    }
    if status == "completed" and score is not None:
        _write_report(
            run_dir,
            student_id=student_id,
            assignment_id=assignment_id,
            original_filename=f"{student_id}_{assignment_id}.zip",
            attempt_id=run_id,
            attempt_number=attempt_number,
            source_type=source_type,
            submitted_at=created_at,
            score=score,
            llm_error=llm_error,
            syntax_error=syntax_error,
        )
        run_info["report"] = "report.json"
        run_info["summary"] = "summary.txt"
        run_info["status"] = "llm_error" if llm_error else status
        run_info["llm_error_flagged"] = llm_error
        run_info["validity_status"] = "valid"
    else:
        run_info["error"] = "pipeline failed"
        run_info["validity_status"] = "invalid"
    save_run_info(run_dir, run_info)
    return run_dir


def test_multiple_attempts_are_preserved_and_latest_valid_wins(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="attempt_old",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.41,
    )
    newer_dir = _make_attempt_run(
        tmp_path,
        run_id="attempt_new",
        attempt_number=2,
        created_at="2026-03-21T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.83,
    )

    history_runs = list_runs(tmp_path, only_active=False)
    active_runs = list_runs(tmp_path)

    assert [run["id"] for run in active_runs if run.get("student_id") == "student1"] == ["attempt_new"]
    history_ids = [run["id"] for run in history_runs if run.get("student_id") == "student1"]
    assert history_ids == ["attempt_new", "attempt_old"]
    assert newer_dir.exists()
    assert (tmp_path / "assignment1" / "student1" / "attempts" / "001_attempt_old" / "report.json").exists()
    assert (tmp_path / "assignment1" / "student1" / "attempts" / "002_attempt_new" / "report.json").exists()


def test_invalid_newest_attempt_falls_back_to_previous_valid_attempt(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="attempt_valid",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.74,
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_invalid",
        attempt_number=2,
        created_at="2026-03-22T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        status="failed",
    )

    active_runs = list_runs(tmp_path)
    history_runs = list_runs(tmp_path, only_active=False)

    assert [run["id"] for run in active_runs if run.get("student_id") == "student1"] == ["attempt_valid"]
    invalid_run = next(run for run in history_runs if run["id"] == "attempt_invalid")
    assert invalid_run["validity_status"] == "invalid"


def test_llm_error_newest_attempt_falls_back_to_previous_valid_attempt(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="attempt_valid",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.74,
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_llm_error",
        attempt_number=2,
        created_at="2026-03-22T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.92,
        llm_error=True,
    )

    active_runs = list_runs(tmp_path)
    history_runs = list_runs(tmp_path, only_active=False)

    assert [run["id"] for run in active_runs if run.get("student_id") == "student1"] == ["attempt_valid"]
    llm_error_run = next(run for run in history_runs if run["id"] == "attempt_llm_error")
    assert llm_error_run["status"] == "llm_error"
    assert llm_error_run["validity_status"] == "invalid"
    assert llm_error_run["is_active"] is False


def test_teacher_uploaded_newest_valid_attempt_becomes_active(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="student_attempt",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        source_type="student_zip_upload",
        score=0.58,
    )
    _make_attempt_run(
        tmp_path,
        run_id="teacher_attempt",
        attempt_number=2,
        created_at="2026-03-23T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        source_type="teacher_upload",
        score=0.77,
    )

    active_runs = list_runs(tmp_path)
    assert [run["id"] for run in active_runs if run.get("student_id") == "student1"] == ["teacher_attempt"]
    active_run = next(run for run in active_runs if run["id"] == "teacher_attempt")
    assert active_run["source_type"] == "teacher_upload"


def test_student_syntax_errors_do_not_trigger_fallback_when_report_is_usable(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_attempt_run(
        tmp_path,
        run_id="attempt_clean",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.74,
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_syntax",
        attempt_number=2,
        created_at="2026-03-22T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.31,
        syntax_error=True,
    )

    active_runs = list_runs(tmp_path)
    history_runs = list_runs(tmp_path, only_active=False)

    assert [run["id"] for run in active_runs if run.get("student_id") == "student1"] == ["attempt_syntax"]
    syntax_run = next(run for run in history_runs if run["id"] == "attempt_syntax")
    assert syntax_run["validity_status"] == "valid"
    assert syntax_run["is_active"] is True


def test_attempt_sync_repairs_malformed_submission_metadata_in_report(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    run_dir = _make_attempt_run(
        tmp_path,
        run_id="attempt_bad_meta",
        attempt_number=1,
        created_at="2026-03-22T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.67,
    )
    report_path = run_dir / "report.json"
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    report_data["metadata"] = "broken"
    report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    runs = list_runs(tmp_path, only_active=False)

    assert any(run["id"] == "attempt_bad_meta" for run in runs)

    repaired = json.loads(report_path.read_text(encoding="utf-8"))
    submission_meta = repaired["metadata"]["submission_metadata"]
    assert submission_meta["attempt_id"] == "attempt_bad_meta"
    assert submission_meta["attempt_number"] == 1
    assert submission_meta["is_active"] is True
    assert submission_meta["active_attempt_id"] == "attempt_bad_meta"


def test_assignment_analytics_count_each_student_once_using_active_attempt(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    create_assignment(
        "assignment1",
        teacher_id="admin123",
        title="Assignment 1",
        profile="frontend",
        assigned_students=["student1", "student2"],
    )
    _make_attempt_run(
        tmp_path,
        run_id="student1_old",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.40,
    )
    _make_attempt_run(
        tmp_path,
        run_id="student1_new",
        attempt_number=2,
        created_at="2026-03-21T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.81,
    )
    _make_attempt_run(
        tmp_path,
        run_id="student2_only",
        attempt_number=1,
        created_at="2026-03-21T10:00:00Z",
        student_id="student2",
        assignment_id="assignment1",
        score=0.66,
    )

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    analytics = generate_assignment_analytics("assignment1", app=app)

    assert analytics["submission_count"] == 2


def test_run_detail_and_teacher_assignment_page_label_active_and_history_attempts(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    create_assignment(
        "assignment1",
        teacher_id="admin123",
        title="Assignment 1",
        profile="frontend",
        assigned_students=["student1"],
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_old",
        attempt_number=1,
        created_at="2026-03-20T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.42,
    )
    _make_attempt_run(
        tmp_path,
        run_id="attempt_new",
        attempt_number=2,
        created_at="2026-03-21T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
        score=0.88,
    )

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    authenticate_client(client)

    assignment_page = client.get("/teacher/assignment/assignment1")
    assert assignment_page.status_code == 200
    body = assignment_page.get_data(as_text=True)
    assert "#1" in body
    assert "#2" in body
    assert "Active" in body
    # Template uses "N older attempt(s)" toggle button instead of a static label
    assert "older attempt" in body

    old_attempt_page = client.get("/runs/attempt_old")
    assert old_attempt_page.status_code == 200
    assert "History attempt" in old_attempt_page.get_data(as_text=True)

    new_attempt_page = client.get("/runs/attempt_new")
    assert new_attempt_page.status_code == 200
    assert "Active attempt" in new_attempt_page.get_data(as_text=True)
