from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ams.tools.batch import discover_batch_items, run_batch


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
    result = run_batch(submissions, out_root, profile="frontend")

    summary_json = out_root / "batch_summary.json"
    summary_csv = out_root / "batch_summary.csv"
    freq_csv = out_root / "findings_frequency.csv"
    failure_csv = out_root / "failure_reasons_frequency.csv"
    bucket_csv = out_root / "score_buckets.csv"
    comp_csv = out_root / "component_means.csv"
    assert summary_json.exists()
    assert summary_csv.exists()
    assert freq_csv.exists()
    assert failure_csv.exists()
    assert bucket_csv.exists()
    assert comp_csv.exists()

    data = json.loads(summary_json.read_text(encoding="utf-8"))
    records = data["records"]
    summary = data["summary"]

    # All discovered items counted in totals (including failures)
    assert summary["total_submissions"] == 3
    assert summary["failed"] == 1
    assert summary["succeeded"] == 2
    assert len(records) == 3

    runs_root = out_root / "runs"
    assert (runs_root / "good1_assignment1" / "report.json").exists()
    assert (runs_root / "partial1_assignment1" / "report.json").exists()

    # Aggregate stats should have mean/median for succeeded
    overall_stats = summary["overall_stats"]
    assert overall_stats is not None
    assert overall_stats["mean"] is not None

    # Failure reasons aggregated
    failure_freq = summary["failure_reason_frequency"]
    assert any("Zip entry would escape extraction directory" in k for k in failure_freq.keys())
    # Failure CSV content
    failure_lines = failure_csv.read_text(encoding="utf-8").splitlines()
    assert failure_lines[0] == "reason,count"
    assert any("Zip entry would escape extraction directory" in line for line in failure_lines)
    # Bucket CSV content
    bucket_lines = bucket_csv.read_text(encoding="utf-8").splitlines()
    assert bucket_lines[0] == "bucket,count"
    for name in ["zero", "gt_0_to_0_5", "gt_0_5_to_1", "one"]:
        assert any(line.startswith(f"{name},") for line in bucket_lines[1:])
    # Component means CSV content
    comp_lines = comp_csv.read_text(encoding="utf-8").splitlines()
    assert comp_lines[0] == "component,mean"
    assert any(line.startswith("html,") for line in comp_lines[1:])
    assert any(line.startswith("js,") for line in comp_lines[1:])
    php_line = next(line for line in comp_lines if line.startswith("php,"))
    sql_line = next(line for line in comp_lines if line.startswith("sql,"))
    assert php_line == "php,"
    assert sql_line == "sql,"


def test_run_batch_without_keep_individual_runs(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()

    good_dir = submissions / "good1_assignment1"
    good_dir.mkdir()
    create_frontend_good(good_dir)

    partial_zip = submissions / "partial1_assignment1.zip"
    create_frontend_partial_zip(partial_zip, tmp_path)

    out_root = tmp_path / "out_no_keep"
    result = run_batch(submissions, out_root, profile="frontend", keep_individual_runs=False)

    assert (out_root / "batch_summary.json").exists()
    assert (out_root / "batch_summary.csv").exists()
    assert (out_root / "findings_frequency.csv").exists()

    # Runs directory should not persist
    runs_dir = out_root / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())

    summary = result["summary"]
    assert summary["total_submissions"] == 2
    assert summary["failed"] == 0
    assert summary["succeeded"] == 2
