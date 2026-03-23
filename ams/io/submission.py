from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from ams.core.profiles import get_profile_spec
from ams.core.models import SubmissionContext


class SubmissionProcessor:
    JUNK_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db"}
    BINARY_EXTENSIONS = {".exe", ".dll", ".bin"}
    MAX_FILE_BYTES = 5 * 1024 * 1024

    def prepare(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str | None = None,
        resolved_config: object | None = None,
    ) -> SubmissionContext:
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

        filtered_items = self._filter_unsupported(staged_root)
        resolved_root, operations = self._resolve_submission_root(staged_root)
        if operations.get("nested_zip_unwrapped"):
            filtered_items.extend(self._filter_unsupported(resolved_root))
        filtered_items = sorted(set(filtered_items))

        discovered_files = self._discover_files(resolved_root)
        validation = self._validation_flags(resolved_root, profile, resolved_config=resolved_config)
        identity = self._extract_student_identity(submission_path)
        metadata = {
            "submission_name": submission_path.name,
            "original_filename": submission_path.name,
            "input_type": input_type,
            "staged_root": str(staged_root),
            "resolved_root": str(resolved_root),
            "normalization": {
                "wrapper_flattened": operations.get("wrapper_flattened", False),
                "nested_zip_unwrapped": operations.get("nested_zip_unwrapped", False),
                "filtered_items": filtered_items,
            },
            "discovered_counts": {ext: len(paths) for ext, paths in discovered_files.items()},
            "validation": validation,
            "student_identity": identity,
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

    def _resolve_submission_root(self, staged_root: Path) -> tuple[Path, dict]:
        root, wrapper_ops = self._resolve_wrapper_root(staged_root)
        operations = {"wrapper_flattened": wrapper_ops, "nested_zip_unwrapped": False}

        if self._should_unwrap_nested_zip(root):
            zip_path = next(root.glob("*.zip"))
            self._extract_zip(zip_path, root)
            zip_path.unlink(missing_ok=True)
            operations["nested_zip_unwrapped"] = True
            root, wrapper_ops_after = self._resolve_wrapper_root(root)
            operations["wrapper_flattened"] = operations["wrapper_flattened"] or wrapper_ops_after
        return root, operations

    def _resolve_wrapper_root(self, root: Path) -> tuple[Path, bool]:
        entries = [p for p in root.iterdir() if p.name not in self.JUNK_NAMES and not p.name.startswith(".")]
        top_level_dirs = [p for p in entries if p.is_dir()]
        top_level_files = [p for p in entries if p.is_file()]
        if len(top_level_dirs) == 1 and not top_level_files:
            return top_level_dirs[0], True
        return root, False

    def _should_unwrap_nested_zip(self, root: Path) -> bool:
        zip_files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".zip"]
        if len(zip_files) != 1:
            return False
        for path in root.iterdir():
            if path.is_dir() or path.is_file() and path.suffix.lower() != ".zip":
                return False
        return True

    def _extract_zip(self, zip_path: Path, dest_dir: Path) -> None:
        with ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                member_path = PurePosixPath(info.filename)
                self._validate_zip_entry(member_path)
                dest_path = dest_dir.joinpath(*member_path.parts)
                if info.is_dir():
                    dest_path.mkdir(parents=True, exist_ok=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, dest_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

    def _filter_unsupported(self, staged_root: Path) -> list[str]:
        filtered: list[str] = []
        for path in list(staged_root.rglob("*")):
            if not path.exists():
                continue
            rel = path.relative_to(staged_root)
            if path.name in self.JUNK_NAMES:
                filtered.append(str(rel))
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                continue
            if path.is_file():
                if path.suffix.lower() in self.BINARY_EXTENSIONS:
                    filtered.append(str(rel))
                    path.unlink(missing_ok=True)
                    continue
                if path.stat().st_size > self.MAX_FILE_BYTES:
                    filtered.append(str(rel))
                    path.unlink(missing_ok=True)
        return filtered

    def _validation_flags(
        self,
        resolved_root: Path,
        profile: str | None,
        *,
        resolved_config: object | None = None,
    ) -> dict:
        if resolved_config is not None:
            required_files = list(getattr(getattr(resolved_config, "profile", None), "required_files", []) or [])
        elif profile:
            required_files = list(get_profile_spec(profile).required_files)
        else:
            required_files = []
        if not required_files:
            return {"missing_required_files": []}
        missing = []
        for ext in required_files:
            if not list(resolved_root.rglob(f"*{ext}")):
                missing.append(ext)
        return {"missing_required_files": missing}

    def _discover_files(self, staged_root: Path) -> dict[str, list[Path]]:
        extensions = ["html", "css", "js", "php", "sql"]
        discovered: dict[str, list[Path]] = {ext: [] for ext in extensions}

        for path in staged_root.rglob("*"):
            if path.is_file():
                ext = path.suffix.lower().lstrip(".")
                if ext in discovered:
                    discovered[ext].append(path)

        return discovered

    def _extract_student_identity(self, submission_path: Path) -> dict:
        stem = submission_path.stem
        tokens = stem.replace("-", " ").replace("_", " ").replace(".", " ").split()
        stop = {"submission", "cw", "coursework", "web", "final", "zip", "mark", "batch"}
        student_id = None
        name_tokens: list[str] = []
        for tok in tokens:
            if tok.lower() in stop:
                continue
            if tok.isdigit() and len(tok) >= 5 and student_id is None:
                student_id = tok
                continue
            name_tokens.append(tok)
        name_raw = " ".join(name_tokens)
        name_normalized = " ".join(name_tokens).strip()
        if name_normalized:
            name_normalized = " ".join(word.capitalize() for word in name_normalized.split())
        return {
            "name_raw": name_raw,
            "name_normalized": name_normalized or None,
            "student_id": student_id,
        }


__all__ = ["SubmissionProcessor"]
