"""Tests for strict ZIP validation."""
from __future__ import annotations

import zipfile
from pathlib import Path

from ams.web.validators import validate_is_zipfile


# Validate_is_zipfile.


class TestValidateIsZipfile:
    """Ensure validate_is_zipfile detects fake vs real ZIP files."""

    def test_valid_zip(self, tmp_path: Path) -> None:
        """A genuine ZIP should be accepted."""
        zip_path = tmp_path / "valid.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "world")
        assert validate_is_zipfile(zip_path) is True

    def test_fake_zip_extension(self, tmp_path: Path) -> None:
        """A plain text file renamed to.zip must be rejected."""
        fake = tmp_path / "fake.zip"
        fake.write_text("this is not a zip file")
        assert validate_is_zipfile(fake) is False

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """A path that doesn't exist must return False."""
        assert validate_is_zipfile(tmp_path / "nope.zip") is False

    def test_empty_file(self, tmp_path: Path) -> None:
        """A zero-byte file must return False."""
        empty = tmp_path / "empty.zip"
        empty.write_bytes(b"")
        assert validate_is_zipfile(empty) is False

    def test_binary_garbage(self, tmp_path: Path) -> None:
        """Random binary data must not be accepted."""
        garbage = tmp_path / "garbage.zip"
        garbage.write_bytes(b"\x00\x01\x02\x03\x04\x05")
        assert validate_is_zipfile(garbage) is False


# Validate_submission_archive (ingestion fail-safe.
