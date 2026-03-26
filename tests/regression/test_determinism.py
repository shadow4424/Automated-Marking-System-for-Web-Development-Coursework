from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List, Dict, Any

import pytest
from ams.core.pipeline import AssessmentPipeline
from ams.core.models import Finding
from ams.core.config import ScoringMode

def canonical_sort(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort findings by rule_id, severity, and message for deterministic comparison."""
    def sort_key(f):
        return (
            f.get("id", ""),
            f.get("severity", ""),
            f.get("message", ""),
            f.get("category", ""),
        )
    return sorted(findings, key=sort_key)

def clean_timestamps(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively remove timestamp and path fields from a dictionary."""
    if isinstance(data, dict):
        return {
            k: clean_timestamps(v)
            for k, v in data.items()
            if k not in {
                "timestamp",
                "generated_at",
                "duration_ms",
                "path",
                "submission_path",
                "workspace_path",
                "full_path",
                "entry",
                "matched_paths",
                "contributing_paths",
                "searched_dirs",
            }
        }
    elif isinstance(data, list):
        return [clean_timestamps(item) for item in data]
    return data

@pytest.fixture
def golden_submission(build_submission) -> Path:
    """Create a golden submission with known semi-valid content."""
    return build_submission({
        "index.html": """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Golden Submission</title>
            <link rel="stylesheet" href="style.css">
        </head>
        <body>
            <header>
                <nav>
                    <ul>
                        <li><a href="#home">Home</a></li>
                    </ul>
                </nav>
            </header>
            <main>
                <h1>Welcome</h1>
                <p>This is a determinism test.</p>
            </main>
            <footer>
                <p>&copy; 2023</p>
            </footer>
            <script src="script.js"></script>
        </body>
        </html>
        """,
        "style.css": """
        body { font-family: sans-serif; }
        header { background: #333; colour: white; }
        @media (max-width: 600px) {
            header { background: #000; }
        }
        """,
        "script.js": """
        console.log("Hello deterministic world");
        function test() { return true; }
        """
    })

def test_pipeline_determinism(golden_submission, tmp_path):
    """
    Verify that the pipeline produces identical results for the same input across two runs.
    
    Checks:
    1. Overall score matches exactly.
    2. Component scores match exactly.
    3. Findings match exactly (canonicalized).
    4. Report structure matches strict schema.
    """
    
    # Run 1
    pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_ONLY)
    workspace1 = tmp_path / "run1"
    report_path1 = pipeline.run(golden_submission, workspace1, profile="frontend")
    report1 = json.loads(report_path1.read_text(encoding="utf-8"))
    
    # Run 2
    workspace2 = tmp_path / "run2"
    report_path2 = pipeline.run(golden_submission, workspace2, profile="frontend")
    report2 = json.loads(report_path2.read_text(encoding="utf-8"))
    
    # 1. Compare Scores
    score1 = report1["scores"].get("overall", 0)
    score2 = report2["scores"].get("overall", 0)
    assert score1 == score2, f"Scores differed: {score1} vs {score2}"
    
    comp1 = report1["scores"].get("by_component", {})
    comp2 = report2["scores"].get("by_component", {})
    # Compare component scores, ignoring rationales which might have slight ordering variances (though shouldn't)
    for key in comp1:
        assert comp1[key]["score"] == comp2[key]["score"], f"Component {key} score differed"
        
    # 2. Compare Findings
    findings1 = report1.get("findings", [])
    findings2 = report2.get("findings", [])
    
    assert len(findings1) == len(findings2), "Different number of findings"
    
    # Canonicalize and clean timestamps
    sorted_f1 = canonical_sort(clean_timestamps(findings1))
    sorted_f2 = canonical_sort(clean_timestamps(findings2))
    
    import difflib
    if sorted_f1 != sorted_f2:
        diff = difflib.unified_diff(
            json.dumps(sorted_f1, indent=2).splitlines(),
            json.dumps(sorted_f2, indent=2).splitlines(),
            fromfile='run1',
            tofile='run2',
            lineterm=''
        )
        print("\n".join(diff))
    
    assert sorted_f1 == sorted_f2, "Findings differed after canonical sort"

    # 3. Verify Constraints
    assert report1["report_version"] == "1.0"
    assert "metadata" in report1
    assert "pipeline_version" in report1["metadata"]

