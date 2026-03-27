"""Consistency evaluator for the AMS evaluation framework.

Runs the same submission N times and checks for non-determinism in scores,
component results, and findings. Outputs structured JSON and CSV files.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ams.core.config import ScoringMode
from ams.core.pipeline import AssessmentPipeline
from ams.evaluation.metrics import (
    compute_finding_consistency_rate,
    write_json,
    write_csv,
)

logger = logging.getLogger(__name__)

_COMPONENTS = ["html", "css", "js", "php", "sql", "api"]


def _run_once(
    pipeline: AssessmentPipeline,
    submission_path: Path,
    workspace: Path,
    profile: str,
    run_index: int,
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run the pipeline once and return a normalised result dict."""
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        metadata: dict = {}
        if profile_config_path:
            metadata["profile_config_path"] = str(profile_config_path)
        report_path = pipeline.run(
            submission_path=submission_path,
            workspace_path=workspace,
            profile=profile,
            metadata=metadata or None,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))

        scores = report.get("scores") or {}
        overall = scores.get("overall")
        by_comp = scores.get("by_component") or {}

        component_scores: dict[str, float | None] = {}
        for comp in _COMPONENTS:
            comp_data = by_comp.get(comp)
            if isinstance(comp_data, dict):
                raw = comp_data.get("score")
                component_scores[comp] = float(raw) if isinstance(raw, (int, float)) else None
            else:
                component_scores[comp] = None

        finding_ids = {
            f.get("id", "") for f in (report.get("findings") or [])
            if isinstance(f, dict) and f.get("id")
        }

        return {
            "run": run_index,
            "overall_score": float(overall) if isinstance(overall, (int, float)) else None,
            "component_scores": component_scores,
            "finding_ids": finding_ids,
            "error": None,
        }
    except Exception as exc:
        logger.warning("Run %d failed: %s", run_index, exc)
        return {
            "run": run_index,
            "overall_score": None,
            "component_scores": {},
            "finding_ids": set(),
            "error": str(exc),
        }


def run_consistency_evaluation(
    submission_path: Path,
    out_dir: Path,
    runs: int = 5,
    profile: str = "frontend",
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run the same submission N times and measure result consistency.

    Checks:
    - Score consistency rate: fraction of runs matching run 0's overall score
    - Finding consistency rate: mean pairwise Jaccard similarity of finding ID sets
    - Full-output match rate: fraction of runs identical to run 0 (score + components + findings)
    - Per-component consistency: fraction of runs where each component score matches run 0

    Writes:
      out_dir/consistency_report.json
      out_dir/consistency_report.csv

    Returns the report dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspaces = out_dir / "workspaces"

    pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_ONLY)
    run_results: list[dict] = []

    run_profile = "custom_profile" if profile_config_path else profile
    print(f"Running consistency check: {runs} runs on {submission_path.name}")
    for i in range(runs):
        workspace = workspaces / f"run_{i:02d}"
        result = _run_once(pipeline, submission_path, workspace, run_profile, i, profile_config_path)
        run_results.append(result)
        status = f"score={result['overall_score']}" if result["error"] is None else f"ERROR: {result['error']}"
        print(f"  Run {i}: {status}")

    # ── Reference values from run 0 ────────────────────────────────────────
    ref = run_results[0]
    ref_score = ref["overall_score"]
    ref_components = ref["component_scores"]
    ref_findings = ref["finding_ids"]

    # ── Score consistency ──────────────────────────────────────────────────
    successful_runs = [r for r in run_results if r["error"] is None]
    scores = [r["overall_score"] for r in successful_runs]
    score_matches = sum(1 for s in scores if s == ref_score)
    score_consistency_rate = score_matches / len(successful_runs) if successful_runs else 0.0

    # ── Finding consistency ────────────────────────────────────────────────
    finding_sets = [r["finding_ids"] for r in successful_runs]
    finding_consistency_rate = compute_finding_consistency_rate(finding_sets)

    # ── Full-output match rate ─────────────────────────────────────────────
    full_matches = sum(
        1 for r in successful_runs
        if (
            r["overall_score"] == ref_score
            and r["component_scores"] == ref_components
            and r["finding_ids"] == ref_findings
        )
    )
    full_output_match_rate = full_matches / len(successful_runs) if successful_runs else 0.0

    # ── Per-component consistency ──────────────────────────────────────────
    component_consistency: dict[str, float] = {}
    for comp in _COMPONENTS:
        ref_val = ref_components.get(comp)
        if ref_val is None:
            continue
        comp_matches = sum(
            1 for r in successful_runs
            if r["component_scores"].get(comp) == ref_val
        )
        component_consistency[comp] = comp_matches / len(successful_runs) if successful_runs else 0.0

    # ── Flag inconsistencies ───────────────────────────────────────────────
    inconsistencies: list[dict] = []
    for r in run_results:
        if r["error"]:
            inconsistencies.append({
                "run": r["run"],
                "type": "PIPELINE_ERROR",
                "detail": r["error"],
            })
            continue
        if r["overall_score"] != ref_score:
            inconsistencies.append({
                "run": r["run"],
                "type": "SCORE_MISMATCH",
                "detail": f"run 0 score={ref_score}, this run score={r['overall_score']}",
            })
        for comp in _COMPONENTS:
            ref_c = ref_components.get(comp)
            this_c = r["component_scores"].get(comp)
            if ref_c is not None and this_c is not None and ref_c != this_c:
                inconsistencies.append({
                    "run": r["run"],
                    "type": "COMPONENT_SCORE_MISMATCH",
                    "detail": f"{comp}: run 0={ref_c}, this run={this_c}",
                })
        extra_findings = r["finding_ids"] - ref_findings
        missing_findings = ref_findings - r["finding_ids"]
        if extra_findings:
            inconsistencies.append({
                "run": r["run"],
                "type": "EXTRA_FINDINGS",
                "detail": f"Extra findings vs run 0: {sorted(extra_findings)[:10]}",
            })
        if missing_findings:
            inconsistencies.append({
                "run": r["run"],
                "type": "MISSING_FINDINGS",
                "detail": f"Missing findings vs run 0: {sorted(missing_findings)[:10]}",
            })

    # ── Serialisable run records (sets → sorted lists) ─────────────────────
    serialisable_runs = [
        {
            "run": r["run"],
            "overall_score": r["overall_score"],
            "component_scores": r["component_scores"],
            "finding_ids": sorted(r["finding_ids"]),
            "error": r["error"],
        }
        for r in run_results
    ]

    report: dict[str, Any] = {
        "submission": str(submission_path),
        "profile": profile,
        "runs_requested": runs,
        "runs_successful": len(successful_runs),
        "score_consistency_rate": round(score_consistency_rate, 4),
        "finding_consistency_rate": round(finding_consistency_rate, 4),
        "full_output_match_rate": round(full_output_match_rate, 4),
        "scores_per_run": scores,
        "component_consistency": {k: round(v, 4) for k, v in component_consistency.items()},
        "inconsistencies": inconsistencies,
        "run_details": serialisable_runs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Write outputs ──────────────────────────────────────────────────────
    write_json(report, out_dir / "consistency_report.json")

    # Per-run CSV rows
    csv_rows = [
        {
            "run": r["run"],
            "overall_score": r["overall_score"],
            "html_score": r["component_scores"].get("html"),
            "css_score": r["component_scores"].get("css"),
            "js_score": r["component_scores"].get("js"),
            "finding_count": len(r["finding_ids"]),
            "score_matches_run0": r["overall_score"] == ref_score,
            "error": r["error"] or "",
        }
        for r in run_results
    ]
    write_csv(
        csv_rows,
        out_dir / "consistency_report.csv",
        fieldnames=["run", "overall_score", "html_score", "css_score", "js_score",
                    "finding_count", "score_matches_run0", "error"],
    )

    print(f"Consistency evaluation complete:")
    print(f"  Score consistency rate:       {score_consistency_rate:.2%}")
    print(f"  Finding consistency rate:     {finding_consistency_rate:.2%}")
    print(f"  Full-output match rate:       {full_output_match_rate:.2%}")
    print(f"  Inconsistencies detected:     {len(inconsistencies)}")
    print(f"  Results written to:           {out_dir}")

    return report
