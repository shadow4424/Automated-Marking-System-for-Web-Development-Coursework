from __future__ import annotations

from pathlib import Path

from ams.core import db


def test_list_assignments_includes_additional_teachers(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "ams_users.db"
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH", db_path)

    db.init_db()
    db.create_user("teacher_owner", "Owner", "Teacher", "owner@example.com", "Pass123", role="teacher")
    db.create_user("teacher_peer", "Peer", "Teacher", "peer@example.com", "Pass123", role="teacher")

    assert db.create_assignment(
        assignment_id="assignment1",
        teacher_id="teacher_owner",
        title="Assignment 1",
        assigned_teachers=["teacher_peer"],
    )

    assignment = db.get_assignment("assignment1")
    assert assignment is not None
    assert assignment["teacher_ids"] == ["teacher_owner", "teacher_peer"]
    assert assignment["assigned_teachers"] == ["teacher_peer"]

    owner_assignments = db.list_assignments(teacher_id="teacher_owner")
    peer_assignments = db.list_assignments(teacher_id="teacher_peer")

    assert [item["assignmentID"] for item in owner_assignments] == ["assignment1"]
    assert [item["assignmentID"] for item in peer_assignments] == ["assignment1"]
