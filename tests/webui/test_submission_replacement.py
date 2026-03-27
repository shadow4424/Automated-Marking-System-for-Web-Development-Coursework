from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from ams.core.db import init_db
from ams.io.web_storage import list_runs, save_run_info
from ams.web.routes_student import _gather_student_runs
from ams.web.routes_batch import _write_run_index_batch
from ams.web.routes_marking import _replace_existing_submissions


def _use_temp_db(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "ams_users.db"
    monkeypatch.setattr("ams.core.db._DEFAULT_DB_PATH", db_path)
    init_db()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _components(score: float) -> dict[str, float | None]:
    return {
        "html": score,
        "css": score,
        "js": score,
        "php": None,
        "sql": None,
    }


def _make_report(student_id: str, assignment_id: str, original_filename: str, score: float) -> dict:
    return {
        "scores": {
            "overall": score,
            "by_component": {
                "html": {"score": score},
                "css": {"score": score},
                "js": {"score": score},
            },
        },
        "findings": [],
        "metadata": {
            "submission_metadata": {
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": original_filename,
                "timestamp": "2026-03-19T12:00:00Z",
            },
            "student_identity": {
                "student_id": student_id,
                "name_normalized": student_id,
            },
        },
    }


def _make_mark_run(
    runs_root: Path,
    *,
    run_id: str,
    created_at: str,
    student_id: str,
    assignment_id: str,
) -> Path:
    run_dir = runs_root / assignment_id / student_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        run_dir / "report.json",
        _make_report(student_id, assignment_id, f"{student_id}_{assignment_id}.zip", 0.75),
    )
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "mark",
            "profile": "frontend",
            "created_at": created_at,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "original_filename": f"{student_id}_{assignment_id}.zip",
            "status": "completed",
            "report": "report.json",
        },
    )
    return run_dir


def _make_batch_run(
    runs_root: Path,
    *,
    run_id: str,
    created_at: str,
    assignment_id: str,
    students: list[tuple[str, float]],
) -> Path:
    run_dir = runs_root / assignment_id / "batch" / run_id
    records: list[dict] = []

    for student_id, score in students:
        submission_id = f"{student_id}_{assignment_id}"
        report_path = run_dir / "runs" / submission_id / "report.json"
        _write_json(
            report_path,
            _make_report(student_id, assignment_id, f"{submission_id}.zip", score),
        )
        records.append(
            {
                "id": submission_id,
                "student_id": student_id,
                "assignment_id": assignment_id,
                "original_filename": f"{submission_id}.zip",
                "upload_timestamp": created_at,
                "overall": score,
                "components": _components(score),
                "status": "ok",
                "report_path": str(report_path),
            }
        )

    _write_json(run_dir / "batch_summary.json", {"records": records})

    run_info = {
        "id": run_id,
        "mode": "batch",
        "profile": "frontend",
        "created_at": created_at,
        "assignment_id": assignment_id,
        "status": "completed",
        "summary": "batch_summary.json",
        "batch_summary": {"records": records},
    }
    save_run_info(run_dir, run_info)
    _write_run_index_batch(run_dir, run_info)
    return run_dir


def _make_invalid_batch_run(
    runs_root: Path,
    *,
    run_id: str,
    created_at: str,
    run_assignment_id: str,
    submission_assignment_id: str,
    students: list[str],
) -> Path:
    run_dir = runs_root / run_assignment_id / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for student_id in students:
        submission_id = f"{student_id}_{submission_assignment_id}"
        records.append(
            {
                "id": submission_id,
                "student_id": student_id,
                "assignment_id": submission_assignment_id,
                "original_filename": f"{submission_id}.zip",
                "upload_timestamp": created_at,
                "overall": 0.0,
                "components": _components(0.0),
                "status": "invalid_assignment_id",
                "invalid": True,
                "validation_error": (
                    f"Assignment ID '{submission_assignment_id}' does not match "
                    f"the expected assignment '{run_assignment_id}'"
                ),
                "report_path": None,
            }
        )

    _write_json(run_dir / "batch_summary.json", {"records": records})
    _write_json(
        run_dir / "run_index.json",
        {
            "run_id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": created_at,
            "overall": None,
            "status": "ok",
            "submissions": [
                {
                    "submission_id": record["id"],
                    "student_name": None,
                    "student_id": record["student_id"],
                    "assignment_id": record["assignment_id"],
                    "original_filename": record["original_filename"],
                    "upload_timestamp": record["upload_timestamp"],
                }
                for record in records
            ],
        },
    )

    run_info = {
        "id": run_id,
        "mode": "batch",
        "profile": "frontend",
        "created_at": created_at,
        "assignment_id": run_assignment_id,
        "status": "completed",
        "summary": "batch_summary.json",
        "batch_summary": {"records": records},
    }
    save_run_info(run_dir, run_info)
    _write_run_index_batch(run_dir, run_info)
    return run_dir


def test_list_runs_keeps_latest_submission_per_student_and_assignment(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_mark_run(
        tmp_path,
        run_id="20260319-090000_mark_frontend_old",
        created_at="2026-03-19T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
    )
    _make_mark_run(
        tmp_path,
        run_id="20260319-100000_mark_frontend_new",
        created_at="2026-03-19T10:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
    )

    runs = list_runs(tmp_path)

    assert [run["id"] for run in runs] == ["20260319-100000_mark_frontend_new"]


def test_list_runs_does_not_let_invalid_batch_hide_valid_submissions(tmp_path: Path, monkeypatch) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    _make_mark_run(
        tmp_path,
        run_id="20260319-090000_mark_frontend_student1",
        created_at="2026-03-19T09:00:00Z",
        student_id="student1",
        assignment_id="assignment1",
    )
    _make_mark_run(
        tmp_path,
        run_id="20260319-091500_mark_frontend_student2",
        created_at="2026-03-19T09:15:00Z",
        student_id="student2",
        assignment_id="assignment1",
    )
    _make_invalid_batch_run(
        tmp_path,
        run_id="20260320-100000_batch_frontend_invalid",
        created_at="2026-03-20T10:00:00Z",
        run_assignment_id="Assignment1",
        submission_assignment_id="assignment1",
        students=["student1", "student2"],
    )

    runs = list_runs(tmp_path)

    visible = {(run.get("assignment_id"), run.get("student_id")) for run in runs if run.get("mode") == "mark"}
    assert ("assignment1", "student1") in visible
    assert ("assignment1", "student2") in visible


def test_replace_existing_submissions_is_a_noop_shim(tmp_path: Path) -> None:
    """_replace_existing_submissions is now an immutability shim that does nothing.

    Submission attempts are immutable records; pruning is no longer supported.
    This test confirms the function is safe to call and leaves storage untouched.
    """
    run_dir = _make_batch_run(
        tmp_path,
        run_id="20260319-090000_batch_frontend_old",
        created_at="2026-03-19T09:00:00Z",
        assignment_id="assignment1",
        students=[("student1", 0.45), ("student2", 0.85)],
    )

    _replace_existing_submissions(
        tmp_path,
        [("assignment1", "student1")],
        current_run_id="20260319-100000_mark_frontend_new",
    )

    # The shim is intentionally a no-op: both students should still be present.
    batch_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    student_ids = [record["student_id"] for record in batch_summary["records"]]
    assert "student1" in student_ids
    assert "student2" in student_ids
    assert (run_dir / "runs" / "student1_assignment1").exists()
    assert (run_dir / "runs" / "student2_assignment1").exists()

    run_index = json.loads((run_dir / "run_index.json").read_text(encoding="utf-8"))
    index_student_ids = [submission["student_id"] for submission in run_index["submissions"]]
    assert "student1" in index_student_ids
    assert "student2" in index_student_ids


def test_gather_student_runs_includes_batch_submission_details(tmp_path: Path, monkeypatch) -> None:
    _make_batch_run(
        tmp_path,
        run_id="20260319-090000_batch_frontend_visible",
        created_at="2026-03-19T09:00:00Z",
        assignment_id="assignment1",
        students=[("student1", 0.72)],
    )

    app = Flask(__name__)
    app.config["AMS_RUNS_ROOT"] = tmp_path

    monkeypatch.setattr(
        "ams.web.routes_student.get_assignment",
        lambda assignment_id: {"assignmentID": assignment_id, "marks_released": True},
    )

    with app.app_context():
        runs, submitted_assignments = _gather_student_runs("student1")

    assert submitted_assignments == {"assignment1"}
    assert len(runs) == 1
    assert runs[0]["_batch_submission_id"] == "student1_assignment1"
    assert runs[0]["_submission_record"]["student_id"] == "student1"
