from __future__ import annotations

import json
from pathlib import Path

from ams.tools.evaluation import discover_cases, evaluate, load_expectations


FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures"


def test_discover_cases_reads_expectations() -> None:
    expectations = load_expectations(FIXTURES_ROOT)
    cases = discover_cases(FIXTURES_ROOT, expectations)
    names = {(c.profile, c.name) for c in cases}
    assert ("frontend", "good_1") in names
    assert ("fullstack", "offtopic_1") in names
    # Stable ordering is profile then case name
    assert cases == sorted(cases, key=lambda c: (c.profile, c.name))


def test_evaluate_runs_and_writes_reports(tmp_path: Path) -> None:
    out_root = tmp_path / "eval_out"
    summary = evaluate(fixtures_root=FIXTURES_ROOT, out_root=out_root, profile="all")

    assert summary["failed"] == 0
    assert summary["total"] >= 8

    summary_json = out_root / "summary.json"
    summary_csv = out_root / "summary.csv"
    assert summary_json.exists()
    assert summary_csv.exists()

    data = json.loads(summary_json.read_text(encoding="utf-8"))
    assert len(data) == summary["total"]
    # CSV has header plus one row per case
    csv_lines = summary_csv.read_text(encoding="utf-8").splitlines()
    assert csv_lines[0].startswith("profile,case,overall")
    assert len(csv_lines) == summary["total"] + 1
