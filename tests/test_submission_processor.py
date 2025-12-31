from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from ams.io.submission import SubmissionProcessor


def _make_zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_submission_zip_slip_rejected(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "bad")
    processor = SubmissionProcessor()
    with pytest.raises(ValueError):
        processor.prepare(zip_path, tmp_path / "workspace")


def test_wrapper_flattening(tmp_path: Path) -> None:
    submission = tmp_path / "submission"
    inner = submission / "student"
    inner.mkdir(parents=True)
    (inner / "index.html").write_text("<html></html>", encoding="utf-8")
    processor = SubmissionProcessor()
    context = processor.prepare(submission, tmp_path / "workspace", profile="frontend")
    normalization = context.metadata["normalization"]
    assert normalization["wrapper_flattened"] is True
    assert context.metadata["resolved_root"].endswith("student")
    assert context.discovered_files["html"]


def test_nested_zip_unwrapped(tmp_path: Path) -> None:
    submission = tmp_path / "submission"
    submission.mkdir()
    inner_zip = submission / "inner.zip"
    _make_zip(inner_zip, {"index.html": "<html></html>"})
    processor = SubmissionProcessor()
    context = processor.prepare(submission, tmp_path / "workspace", profile="frontend")
    normalization = context.metadata["normalization"]
    assert normalization["nested_zip_unwrapped"] is True
    assert context.discovered_files["html"]


def test_filtering_junk_files(tmp_path: Path) -> None:
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "__MACOSX").mkdir()
    (submission / ".DS_Store").write_text("junk", encoding="utf-8")
    (submission / "virus.exe").write_bytes(b"\x00\x01")
    processor = SubmissionProcessor()
    context = processor.prepare(submission, tmp_path / "workspace", profile="frontend")
    filtered = context.metadata["normalization"]["filtered_items"]
    assert "__MACOSX" in filtered
    assert ".DS_Store" in filtered
    assert "virus.exe" in filtered
