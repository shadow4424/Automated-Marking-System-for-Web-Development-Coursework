from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Tuple, List
from zipfile import ZipFile


def get_runs_root(app) -> Path:
    root = app.config.get("AMS_RUNS_ROOT") or "ams_web_runs"
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    return root_path


def create_run_dir(runs_root: Path, mode: str, profile: str) -> Tuple[str, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(4)
    run_id = f"{timestamp}_{mode}_{profile}_{suffix}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    with ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            member_path = PurePosixPath(info.filename)
            _validate_zip_entry(member_path)
            target = dest_dir.joinpath(*member_path.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    dst.write(src.read())


def _validate_zip_entry(member_path: PurePosixPath) -> None:
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError("Zip entry would escape extraction directory")


def find_submission_root(extracted_dir: Path) -> Path:
    """Resolve the actual submission root within an extracted zip.

    If there is exactly one top-level directory and no files, descend into it.
    Otherwise, return the extracted_dir.
    """
    junk = {"__MACOSX", ".DS_Store", "Thumbs.db"}
    entries: List[Path] = [p for p in extracted_dir.iterdir() if not p.name.startswith(".") and p.name not in junk]
    top_level_dirs = [p for p in entries if p.is_dir()]
    top_level_files = [p for p in entries if p.is_file()]
    if len(top_level_dirs) == 1 and not top_level_files:
        return top_level_dirs[0]
    return extracted_dir


def save_run_info(run_dir: Path, info: Mapping[str, object]) -> None:
    (run_dir / "run_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def load_run_info(run_dir: Path):
    info_path = run_dir / "run_info.json"
    if not info_path.exists():
        return None
    return json.loads(info_path.read_text(encoding="utf-8"))


def list_runs(runs_root: Path) -> list[dict]:
    runs: list[dict] = []
    if not runs_root.exists():
        return runs
    candidates = [p for p in runs_root.iterdir() if p.is_dir()]
    for run_dir in sorted(candidates, key=lambda p: p.name, reverse=True):
        info = load_run_info(run_dir)
        if info:
            info["id"] = run_dir.name
            index_path = run_dir / "run_index.json"
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    info["submissions"] = index.get("submissions", [])
                except Exception:
                    info["submissions"] = []
            runs.append(info)
    return runs


def allowed_download(filename: str, allowed: Iterable[str]) -> bool:
    allowed_set = set(allowed)
    return filename in allowed_set or any(filename.startswith(prefix) for prefix in allowed_set)


__all__ = [
    "get_runs_root",
    "create_run_dir",
    "safe_extract_zip",
    "save_run_info",
    "load_run_info",
    "list_runs",
    "allowed_download",
    "find_submission_root",
]
