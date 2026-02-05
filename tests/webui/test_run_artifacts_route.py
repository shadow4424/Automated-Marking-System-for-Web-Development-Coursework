from __future__ import annotations

from pathlib import Path

from ams.webui import create_app


def test_run_artifact_serves_file(tmp_path: Path) -> None:
    run_id = "run_artifacts_test"
    run_dir = tmp_path / run_id
    file_dir = run_dir / "artifacts"
    file_dir.mkdir(parents=True, exist_ok=True)
    target = file_dir / "shot.png"
    target.write_text("img", encoding="utf-8")
    (run_dir / "run_info.json").write_text("{}", encoding="utf-8")

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    res = client.get(f"/runs/{run_id}/artifacts/artifacts/shot.png")
    assert res.status_code == 200
    assert res.get_data(as_text=True) == "img"


def test_run_artifact_blocks_traversal(tmp_path: Path) -> None:
    run_id = "run_artifacts_test"
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_info.json").write_text("{}", encoding="utf-8")

    app = create_app({"TESTING": True, "AMS_RUNS_ROOT": tmp_path})
    client = app.test_client()
    res = client.get(f"/runs/{run_id}/artifacts/../../etc/passwd")
    assert res.status_code == 403
