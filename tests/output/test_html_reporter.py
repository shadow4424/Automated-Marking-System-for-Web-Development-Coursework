from __future__ import annotations

from pathlib import Path

from ams.io.html_reporter import HTMLReporter


def test_html_reporter_handles_missing_score_evidence_and_none_evidence(tmp_path: Path) -> None:
    report_data = {
        "metadata": None,
        "scores": {"overall": 0.0},
        "score_evidence": None,
        "findings": [
            {
                "id": "SANDBOX.THREAT.TEST",
                "category": "security",
                "message": "Threat detected in submission.",
                "severity": "FAIL",
                "evidence": None,
            }
        ],
    }

    html_path = HTMLReporter().generate(report_data, tmp_path)

    assert html_path == tmp_path / "report.html"
    assert html_path.exists()
    body = html_path.read_text(encoding="utf-8")
    assert "Assessment Report" in body
    assert "Threat detected in submission." in body
