from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.web.routes_batch import _write_batch_reports_zip, _write_run_index_batch
from ams.web.routes_runs import _write_run_index_mark
from ams.webui import create_app
from tests.webui.conftest import (
    _capture_job_submission,
    _client,
    _make_zip,
    _seed_batch_llm_error_run,
    _seed_batch_threat_run,
    _seed_mark_llm_error_run,
    _seed_mark_run,
    _stub_assignment,
    _stub_assignment_options,
    _stub_student_assignment_options,
    authenticate_client,
)


def test_webui_batch_run_redirects_to_assignment_and_keeps_batch_downloads(tmp_path: Path):
    client, runs_root = _client(tmp_path)
    run_id = "20260319-100000_batch_frontend_demo"
    run_dir = runs_root / "assignment1" / "batch" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "runs" / "student1_assignment1" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "submission_metadata": {
                        "student_id": "student1",
                        "assignment_id": "assignment1",
                        "original_filename": "student1_assignment1.zip",
                        "timestamp": "2026-03-19T10:00:00Z",
                    },
                    "student_identity": {"student_id": "student1", "name_normalized": "student1"},
                }
            }
        ),
        encoding="utf-8",
    )

    batch_summary = {
        "records": [
            {
                "id": "student1_assignment1",
                "student_id": "student1",
                "assignment_id": "assignment1",
                "original_filename": "student1_assignment1.zip",
                "upload_timestamp": "2026-03-19T10:00:00Z",
                "overall": 0.8,
                "components": {"html": 0.8, "css": 0.8, "js": 0.8, "php": None, "sql": None},
                "status": "ok",
                "report_path": str(report_path),
            }
        ]
    }
    (run_dir / "batch_summary.json").write_text(json.dumps(batch_summary), encoding="utf-8")
    (run_dir / "batch_summary.csv").write_text("id,overall\nstudent1_assignment1,0.8\n", encoding="utf-8")
    save_run_info(
        run_dir,
        {
            "id": run_id,
            "mode": "batch",
            "profile": "frontend",
            "created_at": "now",
            "assignment_id": "assignment1",
            "summary": "batch_summary.json",
            "batch_summary": batch_summary,
        },
    )
    _write_run_index_batch(run_dir, {"id": run_id, "mode": "batch", "profile": "frontend"})
    _write_batch_reports_zip(run_dir, "frontend", run_id)

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 302
    assert detail.headers["Location"].endswith("/teacher/assignment/assignment1")

    dl = client.get(f"/download/{run_id}/batch_summary.json")
    assert dl.status_code == 200
    cd = dl.headers["Content-Disposition"].encode("utf-8")
    assert run_id.encode() in cd
    assert b"frontend" in cd

def test_batch_form_shows_assignment_dropdown_and_hides_profile_selector(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment_options(
        monkeypatch,
        [
            {"assignmentID": "assignment1", "title": "Assignment 1", "profile": "frontend_interactive"},
            {"assignmentID": "assignment2", "title": "Assignment 2", "profile": "fullstack_php_sql"},
        ],
    )

    response = client.get("/batch")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'select id="assignment_id"' in body
    assert "assignment1 — Assignment 1" in body
    assert "assignment2 — Assignment 2" in body
    assert "Assessment Profile" not in body
    assert 'name="profile"' not in body

def test_batch_route_resolves_profile_from_selected_assignment(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    queued = _capture_job_submission(monkeypatch)
    captured: dict[str, object] = {}

    _stub_assignment_options(
        monkeypatch,
        [{"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql"}],
    )

    def _run_batch_stub(**kwargs):
        captured.update(kwargs)
        return {"records": []}

    monkeypatch.setattr("ams.web.routes_batch.run_batch", _run_batch_stub)

    inner_submission = _make_zip({"index.html": "<!doctype html><html><body>ok</body></html>"})
    batch_bundle = _make_zip({"student1_assignment1.zip": inner_submission})

    response = client.post(
        "/batch",
        data={
            "assignment_id": "assignment1",
            "profile": "frontend_basic",
            "submission_method": "upload",
            "submission": (io.BytesIO(batch_bundle), "batch_submissions.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    queued["func"]()
    assert captured["profile"] == "fullstack_php_sql"
    assert captured["assignment_id"] == "assignment1"

def test_batch_route_rejects_new_submission_when_grades_released(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    _stub_assignment_options(
        monkeypatch,
        [{"assignmentID": "assignment1", "title": "Assignment 1", "profile": "fullstack_php_sql", "marks_released": True}],
    )

    inner_submission = _make_zip({"index.html": "<!doctype html><html><body>ok</body></html>"})
    batch_bundle = _make_zip({"student1_assignment1.zip": inner_submission})
    response = client.post(
        "/batch",
        data={
            "assignment_id": "assignment1",
            "submission_method": "upload",
            "submission": (io.BytesIO(batch_bundle), "batch_submissions.zip"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 403
    body = response.get_data(as_text=True)
    assert "Grades have already been released for this assignment, so new submissions are locked." in body
    assert not list(tmp_path.rglob("run_info.json"))

def test_reprocessing_flagged_batch_submission_unblocks_grade_release(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])
    queued = _capture_job_submission(monkeypatch)
    seen_skip_flags: list[bool] = []

    class _SafePipeline:
        def __init__(self, scoring_mode=None):
            self.scoring_mode = scoring_mode

        def run(self, submission_path, workspace_path, profile, metadata, skip_threat_scan=False):
            seen_skip_flags.append(skip_threat_scan)
            report_path = Path(workspace_path) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scores": {
                            "overall": 0.82,
                            "by_component": {
                                "html": {"score": 0.8},
                                "css": {"score": 0.85},
                                "js": {"score": 0.8},
                                "php": {"score": 0.8},
                                "sql": {"score": 0.85},
                            },
                        },
                        "findings": [{"id": "HTML.REQ.PASS", "severity": "INFO"}],
                        "metadata": {
                            "submission_metadata": metadata,
                            "student_identity": {
                                "student_id": metadata.get("student_id"),
                                "name_normalized": metadata.get("student_id"),
                            },
                            "threat_override": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return report_path

    monkeypatch.setattr("ams.web.routes_marking.AssessmentPipeline", _SafePipeline)

    response = client.post(
        "/teacher/assignment/assignment1/threats/reprocess",
        data={"run_id": run_id, "submission_id": submission_id},
        headers={"X-AMS-Async": "1"},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == "job-queued-1"
    assert payload["view_url"].endswith(f"/batch/{run_id}/submissions/{submission_id}/view")

    queued_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    queued_record = queued_summary["records"][0]
    assert queued_record["status"] == "pending"
    assert queued_record["overall"] is None
    assert queued_record["rerun_pending"] is True

    queued["func"]()

    response = client.get("/teacher/assignment/assignment1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Grade release blocked" not in body
    assert "82%" in body
    assert "THREAT" not in body
    assert seen_skip_flags == [True]

    batch_summary = json.loads((run_dir / "batch_summary.json").read_text(encoding="utf-8"))
    record = batch_summary["records"][0]
    assert record["overall"] == 0.82
    assert record["threat_flagged"] is False
    assert "threat_count" not in record

def test_threat_detail_page_only_shows_one_rerun_button(tmp_path: Path, monkeypatch) -> None:
    client, _ = _client(tmp_path)
    run_id, submission_id, _run_dir = _seed_batch_threat_run(tmp_path)
    _stub_assignment(monkeypatch, "assignment1", ["student5"])

    response = client.get(f"/batch/{run_id}/submissions/{submission_id}/view")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert body.count("Rerun Submission") == 1
