from pathlib import Path
import zipfile

import pytest

from ams.submission import SubmissionProcessor


def create_sample_dir(tmp_path: Path) -> Path:
    submission_dir = tmp_path / "submission_src"
    submission_dir.mkdir()
    (submission_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    assets = submission_dir / "assets"
    assets.mkdir()
    (assets / "style.css").write_text("body{}", encoding="utf-8")
    (assets / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (submission_dir / "script.php").write_text("<?php ?>", encoding="utf-8")
    return submission_dir


def test_prepare_directory_input(tmp_path: Path) -> None:
    submission_dir = create_sample_dir(tmp_path)
    workspace = tmp_path / "workspace"

    context = SubmissionProcessor().prepare(submission_dir, workspace)

    staged_root = workspace / "submission"
    assert staged_root.exists()
    assert (staged_root / "index.html").exists()
    assert (staged_root / "assets" / "style.css").exists()

    assert len(context.discovered_files["html"]) == 1
    assert len(context.discovered_files["css"]) == 1
    assert len(context.discovered_files["js"]) == 1
    assert len(context.discovered_files["php"]) == 1
    assert context.metadata["input_type"] == "dir"
    assert context.metadata["discovered_counts"]["js"] == 1


def test_prepare_zip_input(tmp_path: Path) -> None:
    submission_dir = create_sample_dir(tmp_path)
    zip_path = tmp_path / "submission.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for file_path in submission_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(submission_dir))

    workspace = tmp_path / "workspace"
    context = SubmissionProcessor().prepare(zip_path, workspace)

    staged_root = workspace / "submission"
    assert (staged_root / "assets" / "app.js").exists()
    assert len(context.discovered_files["html"]) == 1
    assert context.metadata["input_type"] == "zip"


def test_zip_slip_raises(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", "bad")

    workspace = tmp_path / "workspace"
    processor = SubmissionProcessor()

    with pytest.raises(ValueError):
        processor.prepare(zip_path, workspace)
