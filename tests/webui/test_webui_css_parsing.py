from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

CSS_BASE_SELECTOR_CASES = [
    ("ams/static/css/components/cards.css", ".card"),
    ("ams/static/css/components/tables.css", ".table-container"),
    ("ams/static/css/components/layout.css", ".toolbar"),
    ("ams/static/css/components/tabs.css", ".detail-tab-nav"),
    ("ams/static/css/components/forms.css", ".form-group"),
    ("ams/static/css/components/alerts.css", ".alert"),
    ("ams/static/css/components/findings.css", "details"),
    ("ams/static/css/components/data-display.css", ".stat-card"),
]

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _first_selector(css_text: str) -> str | None:
    """Return the first rule's selector from *css_text* using pure Python."""
    stripped = _COMMENT_RE.sub("", css_text)
    brace = stripped.find("{")
    if brace == -1:
        return None
    return stripped[:brace].strip()


@pytest.mark.parametrize("relative_path, expected_selector", CSS_BASE_SELECTOR_CASES)
def test_shared_css_base_selector_is_first_parsed_rule(
    relative_path: str,
    expected_selector: str,
) -> None:
    css_path = REPO_ROOT / relative_path
    css_text = css_path.read_text(encoding="utf-8")

    first = _first_selector(css_text)

    assert first is not None, f"No CSS rules found in {relative_path}"
    assert first == expected_selector, (
        f"Expected the first selector in {relative_path} to be "
        f"{expected_selector!r}, got {first!r}"
    )
