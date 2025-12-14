import json
import tempfile
from pathlib import Path

from ams.pipeline import AssessmentPipeline


def test_pipeline_writes_report_json(tmp_path: Path) -> None:
    submission_dir = tmp_path / "submission"
    submission_dir.mkdir()

    pipeline = AssessmentPipeline()
    with tempfile.TemporaryDirectory(prefix="ams-test-workspace-") as workspace_dir:
        report_path = pipeline.run(submission_dir, Path(workspace_dir))
        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data.get("findings") == []
        assert data.get("scores") is not None
