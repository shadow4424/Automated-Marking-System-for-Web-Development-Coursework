from __future__ import annotations

from pathlib import Path

from ams.io.submission import SubmissionProcessor


def test_extract_id_and_name_from_numeric_prefix() -> None:
    proc = SubmissionProcessor()
    identity = proc._extract_student_identity(Path("11074020_Dale_Mccance.zip"))
    assert identity["student_id"] == "11074020"
    assert identity["name_normalized"] == "Dale Mccance"


def test_extract_id_and_name_with_dash() -> None:
    proc = SubmissionProcessor()
    identity = proc._extract_student_identity(Path("John-Smith-123456.zip"))
    assert identity["student_id"] == "123456"
    assert identity["name_normalized"] == "John Smith"


def test_extract_name_when_no_id() -> None:
    proc = SubmissionProcessor()
    identity = proc._extract_student_identity(Path("frontend_good_student.zip"))
    assert identity["student_id"] is None
    assert identity["name_normalized"] == "Frontend Good Student"
