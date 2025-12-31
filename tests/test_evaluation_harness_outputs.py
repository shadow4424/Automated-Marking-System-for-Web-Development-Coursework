from __future__ import annotations

import json
from pathlib import Path

from ams.tools.evaluation import evaluate
from ams.core.pipeline import AssessmentPipeline


def _make_case(tmp_path: Path, profile: str, name: str, html: str, expected_overall: float) -> None:
    case_dir = tmp_path / profile / name
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "index.html").write_text(html, encoding="utf-8")
    return case_dir


def test_evaluation_outputs_files(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    expectations = {
        "frontend": {
            "ok": {"expected_overall": 1.0, "expected_issue_categories": []},
            "bad": {"expected_overall": 0.0, "expected_issue_categories": ["missing"]},
        }
    }
    (fixtures / "frontend").mkdir(parents=True, exist_ok=True)
    (fixtures / "expectations.json").write_text(json.dumps(expectations, indent=2), encoding="utf-8")
    _make_case(fixtures, "frontend", "ok", "<html><body><form><input></form></body></html>", 1.0)
    _make_case(fixtures, "frontend", "bad", "", 0.0)

    out_root = tmp_path / "out"
    summary = evaluate(fixtures_root=fixtures, out_root=out_root, profile="all")
    assert summary["total"] == 2
    assert (out_root / "evaluation_results.csv").exists()
    summary_json = json.loads((out_root / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert "accuracy" in summary_json
    assert "0.0->0.0" in summary_json["confusion"]
    results_csv = (out_root / "evaluation_results.csv").read_text(encoding="utf-8")
    assert "ok" in results_csv and "bad" in results_csv
