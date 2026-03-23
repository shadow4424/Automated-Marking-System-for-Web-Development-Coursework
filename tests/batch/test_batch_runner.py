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
    successful = [r for r in records if r.get("status") in {"ok", "llm_error"}]
    assert len(successful) == 2
    assert len([r for r in records if r.get("status") == "error"]) == 1
    for record in [r for r in records if r.get("status") == "llm_error"]:
        assert record.get("llm_error_flagged") is True
        assert record.get("llm_error_messages")

    runs_root = out_root / "runs"
    assert (runs_root / "good1_assignment1" / "report.json").exists()
    assert (runs_root / "partial1_assignment1" / "report.json").exists()


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
    assert not (out_root / "findings_frequency.csv").exists()

    # Runs directory should not persist
    runs_dir = out_root / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())

    assert len(result["records"]) == 2
    assert all(record.get("status") in {"ok", "llm_error"} for record in result["records"])
    for record in [r for r in result["records"] if r.get("status") == "llm_error"]:
        assert record.get("llm_error_flagged") is True
        assert record.get("llm_error_messages")
