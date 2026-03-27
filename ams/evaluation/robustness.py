"""Robustness evaluator for the AMS evaluation framework.

Runs the marking pipeline against malformed, adversarial, and edge-case
submissions. Classifies failures, measures recovery rates, and produces
structured outputs for dissertation analysis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ams.core.config import ScoringMode
from ams.core.pipeline import AssessmentPipeline
from ams.evaluation.dataset import get_robustness_entries, ManifestEntry
from ams.evaluation.metrics import compute_failure_distribution, write_json, write_csv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

FAILURE_CATEGORIES = (
    "STUDENT_ERROR",           # Submission has recognisable errors; pipeline completed
    "MISSING_FILES",           # Required files absent from submission
    "SYNTAX_ERROR",            # Files have syntax errors preventing meaningful analysis
    "RUNTIME_ERROR",           # Server-side code fails during execution
    "BROWSER_ERROR",           # Browser automation failure
    "ENVIRONMENT_UNAVAILABLE", # Docker/browser/PHP not available
    "PARSING_ERROR",           # AMS failed to parse submission files
    "SYSTEM_ERROR",            # Unexpected pipeline exception
)


def _classify_failure(
    report: dict[str, Any],
    exception: Exception | None,
) -> tuple[str, str, bool, bool]:
    """Classify the primary failure category from a report and any exception.

    Returns:
        (failure_category, stage_of_failure, fallback_used, result_reliable)
    """
    if exception is not None:
        return "SYSTEM_ERROR", "pipeline", False, False

    findings = report.get("findings") or []
    finding_ids = {str(f.get("id", "")).upper() for f in findings if isinstance(f, dict)}
    finding_cats = {str(f.get("finding_category", "")).lower() for f in findings if isinstance(f, dict)}
    severities = {str(f.get("severity", "")).upper() for f in findings if isinstance(f, dict)}
    env = report.get("environment") or {}

    # Check for threat detection
    if "THREAT" in severities:
        return "STUDENT_ERROR", "static", False, True

    # Check for missing files
    if any("MISSING" in fid or "NO_RELEVANT" in fid for fid in finding_ids):
        return "MISSING_FILES", "static", False, True

    # Check for environment unavailability
    if not env.get("docker_available", True) and not env.get("php_available", True):
        return "ENVIRONMENT_UNAVAILABLE", "behavioural", True, False

    # Check behavioural evidence for runtime errors
    beh_ev = report.get("behavioural_evidence") or []
    for ev in beh_ev:
        if isinstance(ev, dict) and str(ev.get("status", "")).lower() == "error":
            return "RUNTIME_ERROR", "behavioural", False, False

    # Check browser evidence for browser errors
    brow_ev = report.get("browser_evidence") or []
    for ev in brow_ev:
        if isinstance(ev, dict) and str(ev.get("status", "")).lower() == "error":
            return "BROWSER_ERROR", "browser", False, False

    # Check finding categories for syntax/parse errors
    if "syntax" in finding_cats or "parse" in finding_cats:
        return "SYNTAX_ERROR", "static", False, True

    # Check for parsing-related finding IDs
    if any(("PARSE" in fid or "SYNTAX" in fid) for fid in finding_ids):
        return "SYNTAX_ERROR", "static", False, True

    # Default: pipeline completed but submission has student errors
    return "STUDENT_ERROR", "static", False, True


def _run_robustness_case(
    pipeline: AssessmentPipeline,
    entry: ManifestEntry,
    submission_path: Path,
    workspace: Path,
    profile: str,
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run one robustness submission and return a classified result record."""
    workspace.mkdir(parents=True, exist_ok=True)
    if profile_config_path:
        run_profile = "custom_profile"
    else:
        run_profile = entry.profile if entry.profile else profile

    report: dict[str, Any] = {}
    exception: Exception | None = None
    pipeline_completed = False
    metadata: dict = {}
    if profile_config_path:
        metadata["profile_config_path"] = str(profile_config_path)

    try:
        report_path = pipeline.run(
            submission_path=submission_path,
            workspace_path=workspace,
            profile=run_profile,
            metadata=metadata or None,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        pipeline_completed = True
    except Exception as exc:
        logger.warning("Pipeline exception for %s: %s", entry.id, exc)
        exception = exc

    failure_category, stage, fallback_used, result_reliable = _classify_failure(
        report, exception
    )

    overall = None
    if pipeline_completed:
        scores = report.get("scores") or {}
        raw = scores.get("overall")
        overall = float(raw) if isinstance(raw, (int, float)) else None

    return {
        "id": entry.id,
        "category": entry.category,
        "profile": run_profile,
        "pipeline_completed": pipeline_completed,
        "overall_score": overall,
        "failure_category": failure_category,
        "stage_of_failure": stage,
        "fallback_used": fallback_used,
        "result_reliable": result_reliable,
        "error_message": str(exception) if exception else "",
        "notes": entry.notes,
    }


def run_robustness_evaluation(
    dataset_path: Path,
    out_dir: Path,
    profile: str = "frontend",
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run robustness evaluation over all robustness/ entries in the dataset.

    For each edge-case / adversarial submission:
    1. Runs the full AMS pipeline (wrapped in try/except)
    2. Classifies the failure category from report findings and exceptions
    3. Records recovery behaviour (fallback logic, result reliability)

    Writes:
      out_dir/robustness_summary.json
      out_dir/robustness_summary.csv
      out_dir/failure_breakdown.csv

    Returns the summary dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspaces = out_dir / "workspaces"

    pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_ONLY)
    entries = get_robustness_entries(dataset_path)

    if not entries:
        logger.warning("No robustness entries found in %s", dataset_path)

    records: list[dict] = []
    print(f"Running robustness evaluation: {len(entries)} submissions")

    for entry in entries:
        submission_path = entry.abs_path(dataset_path)
        if not submission_path.exists():
            logger.warning("Submission path missing: %s — recording as SYSTEM_ERROR", submission_path)
            records.append({
                "id": entry.id,
                "category": entry.category,
                "profile": profile,
                "pipeline_completed": False,
                "overall_score": None,
                "failure_category": "SYSTEM_ERROR",
                "stage_of_failure": "ingestion",
                "fallback_used": False,
                "result_reliable": False,
                "error_message": f"Submission path not found: {submission_path}",
                "notes": entry.notes,
            })
            continue

        workspace = workspaces / entry.id
        record = _run_robustness_case(pipeline, entry, submission_path, workspace, profile, profile_config_path)
        records.append(record)
        print(f"  [{entry.category}] {entry.id} → {record['failure_category']} "
              f"(completed={record['pipeline_completed']})")

    # ── Aggregate metrics ──────────────────────────────────────────────────
    total = len(records)
    completed = sum(1 for r in records if r["pipeline_completed"])
    unrecoverable = sum(1 for r in records if not r["result_reliable"])
    env_issues = sum(
        1 for r in records if r["failure_category"] == "ENVIRONMENT_UNAVAILABLE"
    )
    failure_distribution = compute_failure_distribution(records)
    recoverable_rate = (completed / total) if total > 0 else 0.0
    environment_issue_rate = (env_issues / total) if total > 0 else 0.0

    summary: dict[str, Any] = {
        "total_submissions": total,
        "pipeline_completed": completed,
        "unrecoverable_failures": unrecoverable,
        "recoverable_rate": round(recoverable_rate, 4),
        "environment_issue_rate": round(environment_issue_rate, 4),
        "failure_distribution": failure_distribution,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "profile": profile,
    }

    # ── Write outputs ──────────────────────────────────────────────────────
    write_json(summary, out_dir / "robustness_summary.json")

    write_csv(
        [{
            "total_submissions": total,
            "pipeline_completed": completed,
            "unrecoverable_failures": unrecoverable,
            "recoverable_rate": round(recoverable_rate, 4),
            "environment_issue_rate": round(environment_issue_rate, 4),
            **{f"cat_{k.lower()}": v for k, v in failure_distribution.items()},
            "generated_at": summary["generated_at"],
        }],
        out_dir / "robustness_summary.csv",
        fieldnames=[
            "total_submissions", "pipeline_completed", "unrecoverable_failures",
            "recoverable_rate", "environment_issue_rate",
            *[f"cat_{k.lower()}" for k in failure_distribution],
            "generated_at",
        ],
    )

    breakdown_fields = [
        "id", "category", "profile", "pipeline_completed", "overall_score",
        "failure_category", "stage_of_failure", "fallback_used",
        "result_reliable", "error_message", "notes",
    ]
    write_csv(records, out_dir / "failure_breakdown.csv", fieldnames=breakdown_fields)

    print(f"Robustness evaluation complete:")
    print(f"  Total submissions:      {total}")
    print(f"  Pipeline completed:     {completed}")
    print(f"  Recoverable rate:       {recoverable_rate:.2%}")
    print(f"  Environment issues:     {env_issues}")
    print(f"  Failure distribution:   {failure_distribution}")
    print(f"  Results written to:     {out_dir}")

    return summary
