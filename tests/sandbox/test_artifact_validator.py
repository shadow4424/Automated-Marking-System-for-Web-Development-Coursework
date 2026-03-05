"""Tests for the post-execution artifact validator."""
from __future__ import annotations

from pathlib import Path

import pytest

from ams.sandbox.artifact_validator import validate_screenshot
from ams.core.models import Severity


@pytest.fixture
def workspace(tmp_path: Path):
    """Create a workspace directory with a standard artifact folder."""
    browser_dir = tmp_path / "artifacts" / "browser"
    browser_dir.mkdir(parents=True)
    return tmp_path


class TestValidateScreenshot:
    def test_valid_screenshot(self, workspace: Path):
        """A screenshot above the minimum size should be accepted."""
        png = workspace / "artifacts" / "browser" / "screenshot.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 600)
        path, findings = validate_screenshot(workspace)
        assert path is not None
        assert path == png
        assert findings == []

    def test_missing_screenshot(self, workspace: Path):
        """No screenshots at all should emit CAPTURE_FAIL finding."""
        path, findings = validate_screenshot(workspace)
        assert path is None
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARN
        assert "missing" in findings[0].message.lower() or "corrupt" in findings[0].message.lower()

    def test_too_small_screenshot(self, workspace: Path):
        """A screenshot below minimum size should be rejected."""
        png = workspace / "artifacts" / "browser" / "tiny.png"
        png.write_bytes(b"\x89PNG" + b"\x00" * 10)
        path, findings = validate_screenshot(workspace)
        assert path is None
        assert len(findings) == 1

    def test_no_artifact_directory(self, tmp_path: Path):
        """Workspace without any artifact directories should emit finding."""
        path, findings = validate_screenshot(tmp_path)
        assert path is None
        assert len(findings) == 1

    def test_picks_first_valid_from_multiple(self, workspace: Path):
        """When multiple screenshots exist, the first valid one wins."""
        browser_dir = workspace / "artifacts" / "browser"
        (browser_dir / "aaa.png").write_bytes(b"\x89PNG" + b"\x00" * 600)
        (browser_dir / "zzz.png").write_bytes(b"\x89PNG" + b"\x00" * 700)
        path, findings = validate_screenshot(workspace)
        assert path is not None
        assert path.name == "aaa.png"  # sorted, 'a' before 'z'
        assert findings == []

    def test_submission_artifacts_path(self, tmp_path: Path):
        """Screenshots under submission/artifacts/browser should also be found."""
        browser_dir = tmp_path / "submission" / "artifacts" / "browser"
        browser_dir.mkdir(parents=True)
        png = browser_dir / "screenshot.png"
        png.write_bytes(b"\x89PNG" + b"\x00" * 600)
        path, findings = validate_screenshot(tmp_path)
        assert path is not None
        assert path == png
        assert findings == []

    def test_custom_source_tag(self, tmp_path: Path):
        """The source tag should be passed through to findings."""
        path, findings = validate_screenshot(tmp_path, source="my_test")
        assert findings[0].source == "my_test"
