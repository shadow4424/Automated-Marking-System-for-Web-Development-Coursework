from __future__ import annotations

import tomllib
from pathlib import Path


def test_pyproject_demo_extra_includes_matplotlib() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject.get("project", {}).get("optional-dependencies", {})
    assert "demo" in extras
    demo_extras = extras.get("demo") or []
    assert any("matplotlib" in pkg for pkg in demo_extras)
