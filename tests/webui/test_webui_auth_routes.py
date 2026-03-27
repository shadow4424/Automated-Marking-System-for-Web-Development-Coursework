from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import create_run_dir, save_run_info
from ams.web.routes_batch import _write_batch_reports_zip, _write_run_index_batch
from ams.web.routes_runs import _write_run_index_mark
from ams.webui import create_app
from tests.webui.conftest import (
    _capture_job_submission,
    _client,
    _make_zip,
    _seed_batch_llm_error_run,
    _seed_batch_threat_run,
    _seed_mark_llm_error_run,
    _seed_mark_run,
    _stub_assignment,
    _stub_assignment_options,
    _stub_student_assignment_options,
    authenticate_client,
)


def test_webui_home_ok(tmp_path: Path):
    client, _ = _client(tmp_path)
    res = client.get("/")
    assert res.status_code == 302
