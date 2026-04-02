from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def read_json_file(path: Path) -> Any:
    """Read and decode JSON from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def try_read_json(path: Path, *, default: Any = None) -> Any:
    """Read JSON from disk and return a default value on failure."""
    try:
        return read_json_file(path)
    except Exception:
        return default


def write_json_file(
    path: Path,
    data: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
) -> None:
    """Write JSON to disk using the common project formatting."""
    path.write_text(json.dumps(data, indent=indent, sort_keys=sort_keys), encoding="utf-8")


def parse_llm_json_block(text: str) -> Any:
    """Parse raw JSON or JSON wrapped in a fenced code block."""
    content = str(text or "").strip()
    if not content:
        raise ValueError("Empty JSON content")

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = _JSON_FENCE_RE.search(content)
        if match:
            return json.loads(match.group(1).strip())
        raise


__all__ = [
    "parse_llm_json_block",
    "read_json_file",
    "try_read_json",
    "write_json_file",
]
