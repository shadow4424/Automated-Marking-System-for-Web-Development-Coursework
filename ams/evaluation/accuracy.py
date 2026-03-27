"""Accuracy evaluator for the AMS evaluation framework.

Runs the marking pipeline against a labelled dataset and compares results
against ground-truth expected scores. Outputs structured JSON and CSV files
suitable for dissertation analysis.
"""
from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ams.core.config import ScoringMode
from ams.core.pipeline import AssessmentPipeline
from ams.evaluation.dataset import get_accuracy_entries, ManifestEntry
from ams.evaluation.metrics import (
    compute_confusion_matrix,
    compute_overall_accuracy,
    compute_partial_agreement_rate,
    compute_per_component_accuracy,
    compute_false_positives,
    compute_false_negatives,
    write_json,
    write_csv,
)

logger = logging.getLogger(__name__)

# Components tracked for per-component accuracy
_COMPONENTS = ["html", "css", "js", "php", "sql", "api"]


def _run_pipeline(
    submission_path: Path,
    workspace_path: Path,
    profile: str,
    pipeline: AssessmentPipeline,
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run the pipeline and return parsed report.json, or an error dict."""
    try:
        metadata: dict[str, Any] = {}
        if profile_config_path:
            metadata["profile_config_path"] = str(profile_config_path)
        report_path = pipeline.run(
            submission_path=submission_path,
            workspace_path=workspace_path,
            profile=profile,
            metadata=metadata or None,
        )
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Pipeline failed for %s: %s", submission_path, exc)
        return {"_pipeline_error": str(exc)}


def _extract_scores(report: dict[str, Any]) -> tuple[float | None, dict[str, float | None]]:
    """Extract overall and per-component scores from a report dict."""
    scores = report.get("scores") or {}
    overall = scores.get("overall")
    by_comp = scores.get("by_component") or {}
    components: dict[str, float | None] = {}
    for comp in _COMPONENTS:
        comp_data = by_comp.get(comp)
        if isinstance(comp_data, dict):
            raw = comp_data.get("score")
            components[comp] = float(raw) if isinstance(raw, (int, float)) else None
        else:
            components[comp] = None
    return (float(overall) if isinstance(overall, (int, float)) else None), components


def _build_per_sub_row(
    entry: ManifestEntry,
    actual_overall: float | None,
    actual_components: dict[str, float | None],
    error: str | None,
) -> dict[str, Any]:
    """Build one row for per_submission_comparison.csv."""
    from ams.evaluation.metrics import bin_score
    row: dict[str, Any] = {
        "id": entry.id,
        "category": entry.category,
        "profile": entry.profile,
        "expected_overall": entry.expected_overall,
        "actual_overall": actual_overall,
        "exact_match": (
            bin_score(actual_overall) == bin_score(entry.expected_overall)
            if actual_overall is not None and entry.expected_overall is not None
            else False
        ),
        "within_0.5": (
            abs(actual_overall - entry.expected_overall) <= 0.5
            if actual_overall is not None and entry.expected_overall is not None
            else False
        ),
        "pipeline_error": error or "",
        "notes": entry.notes,
    }
    for comp in _COMPONENTS:
        row[f"{comp}_expected"] = entry.expected_components.get(comp)
        row[f"{comp}_actual"] = actual_components.get(comp)
        exp = entry.expected_components.get(comp)
        act = actual_components.get(comp)
        row[f"{comp}_match"] = (
            bin_score(act) == bin_score(exp)
            if act is not None and exp is not None
            else None
        )
    return row


def run_accuracy_evaluation(
    dataset_path: Path,
    out_dir: Path,
    profile: str = "frontend",
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run accuracy evaluation against all labelled entries in dataset_path.

    For each submission:
    1. Runs the full AMS pipeline (STATIC_ONLY for determinism)
    2. Extracts actual scores from report.json
    3. Compares to expected scores from manifest.json

    Writes:
      out_dir/evaluation_summary.json   — aggregate metrics
      out_dir/evaluation_summary.csv    — one-row summary
      out_dir/per_submission_comparison.csv — one row per submission

    Returns the summary dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspaces = out_dir / "workspaces"

    pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_ONLY)
    entries = get_accuracy_entries(dataset_path)

    if not entries:
        logger.warning("No accuracy entries found in %s", dataset_path)

    per_sub_rows: list[dict] = []
    predictions: list[float | None] = []
    expectations: list[float | None] = []

    for entry in entries:
        submission_path = entry.abs_path(dataset_path)
        if not submission_path.exists():
            logger.warning("Submission path missing: %s — skipping", submission_path)
            row = _build_per_sub_row(entry, None, {}, f"Path not found: {submission_path}")
            per_sub_rows.append(row)
            continue

        workspace = workspaces / entry.id
        workspace.mkdir(parents=True, exist_ok=True)

        # Use custom_profile override when profile_config_path is provided
        if profile_config_path:
            run_profile = "custom_profile"
        else:
            run_profile = entry.profile if entry.profile else profile
        report = _run_pipeline(submission_path, workspace, run_profile, pipeline, profile_config_path)

        error = report.get("_pipeline_error")
        actual_overall, actual_components = _extract_scores(report)

        row = _build_per_sub_row(entry, actual_overall, actual_components, error)
        per_sub_rows.append(row)
        predictions.append(actual_overall)
        expectations.append(entry.expected_overall)

        status = "ERROR" if error else f"actual={actual_overall:.2f}" if actual_overall is not None else "None"
        logger.info("  [%s] %s → %s (expected %s)", entry.category, entry.id, status, entry.expected_overall)

    # ── Compute aggregate metrics ──────────────────────────────────────────
    valid_pairs = [(p, e) for p, e in zip(predictions, expectations) if p is not None and e is not None]
    valid_preds = [p for p, _ in valid_pairs]
    valid_exps = [e for _, e in valid_pairs]

    summary: dict[str, Any] = {
        "total_submissions": len(entries),
        "evaluated_successfully": len(valid_pairs),
        "pipeline_errors": len(entries) - len(valid_pairs),
        "overall_accuracy": compute_overall_accuracy(valid_preds, valid_exps),
        "partial_agreement_rate": compute_partial_agreement_rate(valid_preds, valid_exps),
        "false_positives": compute_false_positives(valid_preds, valid_exps),
        "false_negatives": compute_false_negatives(valid_preds, valid_exps),
        "confusion_matrix": compute_confusion_matrix(valid_preds, valid_exps),
        "per_component_accuracy": compute_per_component_accuracy(per_sub_rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "profile": profile,
        "profile_config_path": str(profile_config_path) if profile_config_path else None,
        "per_submission": per_sub_rows,
    }

    # ── Write outputs ──────────────────────────────────────────────────────
    write_json(summary, out_dir / "evaluation_summary.json")

    # CSV summary (single row)
    comp_acc = summary["per_component_accuracy"]
    write_csv(
        [{
            "total_submissions": summary["total_submissions"],
            "evaluated_successfully": summary["evaluated_successfully"],
            "pipeline_errors": summary["pipeline_errors"],
            "overall_accuracy": round(summary["overall_accuracy"], 4),
            "partial_agreement_rate": round(summary["partial_agreement_rate"], 4),
            "false_positives": summary["false_positives"],
            "false_negatives": summary["false_negatives"],
            "html_accuracy": round(comp_acc.get("html", 0) or 0, 4),
            "css_accuracy": round(comp_acc.get("css", 0) or 0, 4),
            "js_accuracy": round(comp_acc.get("js", 0) or 0, 4),
            "generated_at": summary["generated_at"],
            "dataset_path": str(dataset_path),
        }],
        out_dir / "evaluation_summary.csv",
        fieldnames=[
            "total_submissions", "evaluated_successfully", "pipeline_errors",
            "overall_accuracy", "partial_agreement_rate",
            "false_positives", "false_negatives",
            "html_accuracy", "css_accuracy", "js_accuracy",
            "generated_at", "dataset_path",
        ],
    )

    # Per-submission CSV
    per_sub_fields = (
        ["id", "category", "profile", "expected_overall", "actual_overall",
         "exact_match", "within_0.5", "pipeline_error", "notes"]
        + [f"{c}_{suffix}" for c in _COMPONENTS for suffix in ("expected", "actual", "match")]
    )
    write_csv(per_sub_rows, out_dir / "per_submission_comparison.csv", fieldnames=per_sub_fields)

    print(f"Accuracy evaluation complete: {len(entries)} submissions")
    print(f"  Overall accuracy:        {summary['overall_accuracy']:.2%}")
    print(f"  Partial agreement rate:  {summary['partial_agreement_rate']:.2%}")
    print(f"  False positives:         {summary['false_positives']}")
    print(f"  False negatives:         {summary['false_negatives']}")
    print(f"  Results written to:      {out_dir}")

    return summary
