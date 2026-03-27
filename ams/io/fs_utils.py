from __future__ import annotations

import shutil
from pathlib import Path


def _remove_path_within(root_dir: Path, candidate: Path) -> bool:
    root = root_dir.resolve()
    path = candidate.resolve()
    try:
        path.relative_to(root)
    except Exception:
        return False

    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return True
    if path.exists():
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
    return False


def _prune_empty_parents(path: Path, *, stop_at: Path) -> None:
    stop = stop_at.resolve()
    current = path.resolve()
    while current != stop:
        if not current.exists() or not current.is_dir():
            current = current.parent
            continue
        try:
            next(current.iterdir())
            break
        except StopIteration:
            current.rmdir()
            current = current.parent
        except OSError:
            break
