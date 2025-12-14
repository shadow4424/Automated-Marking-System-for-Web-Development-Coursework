from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from .models import SubmissionContext


class SubmissionProcessor:
    def prepare(self, submission_path: Path, workspace_path: Path) -> SubmissionContext:
        submission_path = submission_path.resolve()
        workspace_path = workspace_path.resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)

        staged_root = workspace_path / "submission"
        if staged_root.exists():
            shutil.rmtree(staged_root)
        staged_root.mkdir(parents=True, exist_ok=True)

        if submission_path.is_dir():
            input_type = "dir"
            self._stage_directory(submission_path, staged_root)
        elif submission_path.suffix.lower() == ".zip" and submission_path.is_file():
            input_type = "zip"
            self._stage_zip(submission_path, staged_root)
        else:
            raise ValueError(f"Unsupported submission input: {submission_path}")

        discovered_files = self._discover_files(staged_root)
        metadata = {
            "submission_name": submission_path.name,
            "input_type": input_type,
            "staged_root": str(staged_root),
            "discovered_counts": {ext: len(paths) for ext, paths in discovered_files.items()},
        }

        return SubmissionContext(
            submission_path=submission_path,
            workspace_path=workspace_path,
            discovered_files=discovered_files,
            metadata=metadata,
        )

    def _stage_directory(self, submission_path: Path, staged_root: Path) -> None:
        for item in submission_path.iterdir():
            target = staged_root / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)

    def _stage_zip(self, submission_path: Path, staged_root: Path) -> None:
        with ZipFile(submission_path, "r") as zf:
            for info in zf.infolist():
                member_path = PurePosixPath(info.filename)
                self._validate_zip_entry(member_path)
                dest_path = staged_root.joinpath(*member_path.parts)
                if info.is_dir():
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, dest_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

    def _validate_zip_entry(self, member_path: PurePosixPath) -> None:
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("Zip entry would escape extraction directory")

    def _discover_files(self, staged_root: Path) -> dict[str, list[Path]]:
        extensions = ["html", "css", "js", "php", "sql"]
        discovered: dict[str, list[Path]] = {ext: [] for ext in extensions}

        for path in staged_root.rglob("*"):
            if path.is_file():
                ext = path.suffix.lower().lstrip(".")
                if ext in discovered:
                    discovered[ext].append(path)

        return discovered
