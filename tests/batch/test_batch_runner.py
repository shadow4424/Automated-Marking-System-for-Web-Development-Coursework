from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ams.core.config import ScoringMode
from ams.tools.batch import BatchItem, _process_one_submission, discover_batch_items, run_batch, write_outputs


def create_frontend_good(dir_path: Path) -> None:
    (dir_path / "index.html").write_text("<!doctype html><html><head></head><body><form><input><a href='#'>x</a></form></body></html>", encoding="utf-8")
    (dir_path / "style.css").write_text("body { color: red; }", encoding="utf-8")
    (dir_path / "app.js").write_text("function a(){const el=document.querySelector('body');el.addEventListener('click',()=>{});}", encoding="utf-8")


def create_frontend_partial_zip(zip_path: Path, tmp_dir: Path) -> None:
    staging = tmp_dir / "partial"
    staging.mkdir()
    (staging / "index.html").write_text("<div>hi</div>", encoding="utf-8")
    (staging / "style.css").write_text("body { color: blue", encoding="utf-8")
    (staging / "app.js").write_text("console.log('x')", encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file in staging.iterdir():
            zf.write(file, file.name)


def create_bad_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "bad")


def test_discover_batch_items_filters_and_sorts(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()
    (submissions / ".hidden").mkdir()
    (submissions / "a_dir").mkdir()
    (submissions / "b.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (submissions / "c.txt").write_text("ignore", encoding="utf-8")

    items = discover_batch_items(submissions)
    ids = [i.id for i in items]
    assert ids == ["a_dir", "b"]
    kinds = {i.id: i.kind for i in items}
    assert kinds["a_dir"] == "dir"
    assert kinds["b"] == "zip"


def test_run_batch_success_and_failure(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()

    good_dir = submissions / "good1_assignment1"
    good_dir.mkdir()
    create_frontend_good(good_dir)

    partial_zip = submissions / "partial1_assignment1.zip"
    create_frontend_partial_zip(partial_zip, tmp_path)

    bad_zip = submissions / "bad1_assignment1.zip"
    create_bad_zip(bad_zip)

    out_root = tmp_path / "out"
    result = run_batch(
        submissions,
        out_root,
        profile="frontend",
        scoring_mode=ScoringMode.STATIC_ONLY,
    )

    summary_json = out_root / "batch_summary.json"
    summary_csv = out_root / "batch_summary.csv"
    assert summary_json.exists()
    assert summary_csv.exists()
    assert not (out_root / "findings_frequency.csv").exists()
    assert not (out_root / "failure_reasons_frequency.csv").exists()
    assert not (out_root / "score_buckets.csv").exists()
    assert not (out_root / "component_means.csv").exists()

    data = json.loads(summary_json.read_text(encoding="utf-8"))
    records = data["records"]

    assert len(records) == 3
    assert "summary" not in data
    successful = [r for r in records if r.get("status") == "ok"]
    assert len(successful) == 2
    assert len([r for r in records if r.get("status") == "error"]) == 1

    runs_root = out_root / "runs"
    assert (runs_root / "good1_assignment1" / "report.json").exists()
    assert (runs_root / "partial1_assignment1" / "report.json").exists()


def test_write_outputs_removes_legacy_batch_exports(tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    out_root.mkdir()
    for filename in (
        "component_means.csv",
        "failure_reasons_frequency.csv",
        "findings_frequency.csv",
        "score_buckets.csv",
    ):
        (out_root / filename).write_text("legacy", encoding="utf-8")
    (out_root / "batch_reports_frontend_old.zip").write_bytes(b"legacy")

    write_outputs(
        out_root,
        [
            {
                "id": "good1_assignment1",
                "student_id": "good1",
                "assignment_id": "assignment1",
                "kind": "dir",
                "overall": 1.0,
                "components": {"html": 1.0, "css": 1.0, "js": 1.0, "php": None, "sql": None, "api": None},
                "status": "ok",
            }
        ],
        profile="frontend",
    )

    for filename in (
        "component_means.csv",
        "failure_reasons_frequency.csv",
        "findings_frequency.csv",
        "score_buckets.csv",
    ):
        assert not (out_root / filename).exists()
    assert not (out_root / "batch_reports_frontend_old.zip").exists()


def test_process_submission_records_canonical_submission_path(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()
    good_dir = submissions / "good1_assignment1"
    good_dir.mkdir()
    create_frontend_good(good_dir)
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    class _Pipeline:
        def run(self, submission_path, workspace_path, profile, metadata):
            submission_dir = Path(workspace_path) / "submission"
            submission_dir.mkdir(parents=True, exist_ok=True)
            (submission_dir / "index.html").write_text((Path(submission_path) / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
            report_path = Path(workspace_path) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "scores": {
                            "overall": 0.9,
                            "by_component": {
                                "html": {"score": 1.0},
                                "css": {"score": 0.8},
                                "js": {"score": 0.9},
                            },
                        },
                        "metadata": {
                            "submission_metadata": metadata,
                        },
                    }
                ),
                encoding="utf-8",
            )
            return report_path

    record = _process_one_submission(
        item=BatchItem(id="good1_assignment1", path=good_dir, kind="dir"),
        runs_root=runs_root,
        pipeline=_Pipeline(),
        profile="frontend",
        keep_individual_runs=True,
        assignment_id="assignment1",
    )

    source_path = Path(record["path"])
    assert source_path.exists()
    assert source_path.name == "submission"
    assert record["status"] == "ok"


def test_run_batch_without_keep_individual_runs(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()

    good_dir = submissions / "good1_assignment1"
    good_dir.mkdir()
    create_frontend_good(good_dir)

    partial_zip = submissions / "partial1_assignment1.zip"
    create_frontend_partial_zip(partial_zip, tmp_path)

    out_root = tmp_path / "out_no_keep"
    result = run_batch(
        submissions,
        out_root,
        profile="frontend",
        keep_individual_runs=False,
        scoring_mode=ScoringMode.STATIC_ONLY,
    )

    assert (out_root / "batch_summary.json").exists()
    assert (out_root / "batch_summary.csv").exists()
    assert not (out_root / "findings_frequency.csv").exists()

    # Runs directory should not persist
    runs_dir = out_root / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())

    assert len(result["records"]) == 2
    assert all(record.get("status") == "ok" for record in result["records"])
