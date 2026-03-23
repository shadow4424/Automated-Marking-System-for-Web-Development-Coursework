from __future__ import annotations

import io
import zipfile
import json
from pathlib import Path

from ams.core.config import ScoringMode
from ams.tools.batch import run_batch


def _make_inner_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_bundle(tmp_path: Path, include_bad: bool = False) -> Path:
    bundle = tmp_path / "bundle.zip"
    student_a = _make_inner_zip(
        {
            "index.html": "<!doctype html><html><body><form><input><a href='#'>x</a></form></body></html>",
            "style.css": "body { color: red; }",
            "app.js": "document.body.addEventListener('click', ()=>{});",
        }
    )
    student_b_dir = tmp_path / "studentB_assignment1"
    student_b_dir.mkdir()
    (student_b_dir / "index.html").write_text("<!doctype html><html><body><form><input><a>y</a></form></body></html>", encoding="utf-8")
    (student_b_dir / "style.css").write_text("body { color: blue; }", encoding="utf-8")
    (student_b_dir / "app.js").write_text("document.body.addEventListener('click', ()=>{});", encoding="utf-8")

    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("studentA_assignment1.zip", student_a)
        for path in student_b_dir.rglob("*"):
            arc = path.relative_to(tmp_path)
            zf.write(path, arc.as_posix())
        if include_bad:
            zf.writestr("studentC_assignment1.zip", b"not-a-zip")
    return bundle


def test_batch_bundle_processes_all(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path)
    out_root = tmp_path / "out"
    result = run_batch(
        bundle,
        out_root,
        profile="frontend",
        keep_individual_runs=True,
        scoring_mode=ScoringMode.STATIC_ONLY,
    )
    summary_json = out_root / "batch_summary.json"
    assert summary_json.exists()
    data = json.loads(summary_json.read_text(encoding="utf-8"))
    records = data["records"]
    assert len(records) == 2
    assert all(r.get("status") == "ok" for r in records)
    assert all(r.get("overall") is not None for r in records)


def test_batch_bundle_continues_on_error(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path, include_bad=True)
    out_root = tmp_path / "out_err"
    result = run_batch(
        bundle,
        out_root,
        profile="frontend",
        keep_individual_runs=True,
        scoring_mode=ScoringMode.STATIC_ONLY,
    )
    data = json.loads((out_root / "batch_summary.json").read_text(encoding="utf-8"))
    records = data["records"]
    assert len(records) == 3
    statuses = [r.get("status") for r in records]
    assert statuses.count("error") == 1
    assert statuses.count("ok") == 2
