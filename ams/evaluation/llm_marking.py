"""LLM/AI partial marking evaluator for the AMS evaluation framework.

Compares pipeline results in STATIC_ONLY mode vs STATIC_PLUS_LLM mode to
measure the impact of AI-assisted partial credit on borderline submissions.

This evaluator specifically targets submissions that:
  - Fail one or more static checks on partial_allowed=True rules
  - Show clear student intent that the LLM can recognise and reward

Produces structured outputs for dissertation analysis.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ams.core.config import ScoringMode
from ams.core.pipeline import AssessmentPipeline
from ams.evaluation.dataset import get_llm_attempt_entries, ManifestEntry
from ams.evaluation.metrics import write_json, write_csv

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Report parsing helpers
# ---------------------------------------------------------------------------

def _extract_overall(report: dict[str, Any]) -> float | None:
    scores = report.get("scores") or {}
    raw = scores.get("overall")
    return float(raw) if isinstance(raw, (int, float)) else None


def _extract_llm_partial_credit(report: dict[str, Any]) -> list[dict]:
    """Extract partial_credit records from the llm_evidence section."""
    llm_ev = report.get("llm_evidence") or {}
    return llm_ev.get("partial_credit") or []


def _extract_llm_adjusted_rules(report: dict[str, Any]) -> list[dict]:
    """Extract score_evidence rationale items where llm_adjusted=True."""
    score_ev = report.get("score_evidence") or {}
    rationale = score_ev.get("rationale") or []
    return [r for r in rationale if isinstance(r, dict) and r.get("llm_adjusted")]


def _detect_llm_error(report: dict[str, Any]) -> bool:
    """Return True if any LLM error was recorded in report metadata."""
    meta = report.get("metadata") or {}
    if meta.get("llm_error_detected"):
        return True
    # Also check findings for LLM-error finding
    for f in report.get("findings") or []:
        fid = str(f.get("id") or "").upper()
        if "LLM_ERROR" in fid or "LLM.ERROR" in fid:
            return True
    return False


# ---------------------------------------------------------------------------
# Single-submission runner
# ---------------------------------------------------------------------------

def _run_pipeline(
    pipeline: AssessmentPipeline,
    submission_path: Path,
    workspace: Path,
    profile: str,
    profile_config_path: Path | None = None,
) -> dict[str, Any]:
    """Run pipeline and return parsed report dict, or error dict."""
    workspace.mkdir(parents=True, exist_ok=True)
    metadata: dict = {}
    if profile_config_path:
        metadata["profile_config_path"] = str(profile_config_path)
    try:
        report_path = pipeline.run(
            submission_path=submission_path,
            workspace_path=workspace,
            profile=profile,
            metadata=metadata or None,
        )
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Pipeline failed for %s: %s", submission_path, exc)
        return {"_pipeline_error": str(exc)}


# ---------------------------------------------------------------------------
# Per-submission comparison
# ---------------------------------------------------------------------------

def _compare_submission(
    entry: ManifestEntry,
    submission_path: Path,
    workspaces: Path,
    static_pipeline: AssessmentPipeline,
    llm_pipeline: AssessmentPipeline,
    profile: str,
    static_profile_config: Path | None,
    llm_profile_config: Path | None,
) -> dict[str, Any]:
    """Run one submission in both modes and return a comparison record."""
    run_profile = "custom_profile" if (static_profile_config or llm_profile_config) else profile

    # ── Static-only run ────────────────────────────────────────────────────
    static_ws = workspaces / entry.id / "static"
    static_report = _run_pipeline(
        static_pipeline, submission_path, static_ws,
        "custom_profile" if static_profile_config else profile,
        static_profile_config,
    )
    static_error = static_report.get("_pipeline_error")
    static_overall = _extract_overall(static_report) if not static_error else None

    # ── LLM-enhanced run ───────────────────────────────────────────────────
    llm_ws = workspaces / entry.id / "llm"
    llm_report = _run_pipeline(
        llm_pipeline, submission_path, llm_ws,
        "custom_profile" if llm_profile_config else profile,
        llm_profile_config,
    )
    llm_error = llm_report.get("_pipeline_error")
    llm_overall = _extract_overall(llm_report) if not llm_error else None

    # ── LLM-specific evidence ──────────────────────────────────────────────
    partial_credit_items = _extract_llm_partial_credit(llm_report) if not llm_error else []
    adjusted_rules = _extract_llm_adjusted_rules(llm_report) if not llm_error else []
    llm_error_detected = _detect_llm_error(llm_report) if not llm_error else True

    # ── Compute deltas ─────────────────────────────────────────────────────
    score_delta: float | None = None
    if static_overall is not None and llm_overall is not None:
        score_delta = round(llm_overall - static_overall, 4)

    llm_upgraded = (
        score_delta is not None and score_delta > 0
    )

    return {
        "id": entry.id,
        "category": entry.category,
        "profile": run_profile,
        "static_overall": static_overall,
        "llm_overall": llm_overall,
        "score_delta": score_delta,
        "llm_upgraded": llm_upgraded,
        "llm_error_detected": llm_error_detected,
        "partial_credit_items": partial_credit_items,
        "adjusted_rules_count": len(adjusted_rules),
        "adjusted_rules": [r.get("rule_id", "") for r in adjusted_rules],
        "static_pipeline_error": static_error or "",
        "llm_pipeline_error": llm_error or "",
        "notes": entry.notes,
    }


# ---------------------------------------------------------------------------
# Public evaluation entry point
# ---------------------------------------------------------------------------

def run_llm_marking_evaluation(
    dataset_path: Path,
    out_dir: Path,
    profile: str = "frontend",
    static_profile_config: Path | None = None,
    llm_profile_config: Path | None = None,
) -> dict[str, Any]:
    """Compare STATIC_ONLY vs STATIC_PLUS_LLM marking on attempt submissions.

    For each attempt submission:
    1. Runs the pipeline in STATIC_ONLY mode (baseline)
    2. Runs the pipeline in STATIC_PLUS_LLM mode (LLM-enhanced)
    3. Compares scores, finds which rules the LLM adjusted, and measures
       partial credit rates

    Writes:
      out_dir/llm_marking_summary.json
      out_dir/llm_marking_summary.csv
      out_dir/llm_marking_comparison.csv     (one row per submission)
      out_dir/llm_partial_credit_breakdown.csv (one row per partial credit item)

    Returns the summary dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    workspaces = out_dir / "workspaces"

    static_pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_ONLY)
    llm_pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_PLUS_LLM)

    entries = get_llm_attempt_entries(dataset_path)
    if not entries:
        logger.warning(
            "No LLM attempt entries found in %s. "
            "Run generate_dataset.py to create the dataset.",
            dataset_path,
        )

    records: list[dict] = []
    print(f"Running LLM marking evaluation: {len(entries)} attempt submissions")

    for entry in entries:
        submission_path = entry.abs_path(dataset_path)
        if not submission_path.exists():
            logger.warning("Submission path missing: %s", submission_path)
            records.append({
                "id": entry.id,
                "category": entry.category,
                "profile": profile,
                "static_overall": None,
                "llm_overall": None,
                "score_delta": None,
                "llm_upgraded": False,
                "llm_error_detected": False,
                "partial_credit_items": [],
                "adjusted_rules_count": 0,
                "adjusted_rules": [],
                "static_pipeline_error": f"Path not found: {submission_path}",
                "llm_pipeline_error": f"Path not found: {submission_path}",
                "notes": entry.notes,
            })
            continue

        record = _compare_submission(
            entry=entry,
            submission_path=submission_path,
            workspaces=workspaces,
            static_pipeline=static_pipeline,
            llm_pipeline=llm_pipeline,
            profile=profile,
            static_profile_config=static_profile_config,
            llm_profile_config=llm_profile_config,
        )
        records.append(record)
        delta_str = f"+{record['score_delta']:.4f}" if (record["score_delta"] or 0) > 0 else str(record["score_delta"])
        print(
            f"  [{entry.category}] {entry.id}: "
            f"static={record['static_overall']}, llm={record['llm_overall']}, "
            f"delta={delta_str}, upgraded={record['llm_upgraded']}, "
            f"llm_error={record['llm_error_detected']}"
        )

    # ── Aggregate metrics ──────────────────────────────────────────────────
    total = len(records)
    llm_ran_ok = [r for r in records if not r["llm_pipeline_error"] and not r["llm_error_detected"]]
    upgraded = [r for r in records if r["llm_upgraded"]]
    errors = [r for r in records if r["llm_error_detected"] or r["llm_pipeline_error"]]

    partial_credit_rate = len(upgraded) / total if total > 0 else 0.0
    llm_error_rate = len(errors) / total if total > 0 else 0.0

    deltas = [r["score_delta"] for r in records if r["score_delta"] is not None]
    mean_score_delta = round(sum(deltas) / len(deltas), 4) if deltas else 0.0

    # Count rules the LLM adjusted across all records
    all_adjusted_rules: dict[str, int] = {}
    for r in records:
        for rule in r["adjusted_rules"]:
            all_adjusted_rules[rule] = all_adjusted_rules.get(rule, 0) + 1

    summary: dict[str, Any] = {
        "total_submissions": total,
        "llm_ran_successfully": len(llm_ran_ok),
        "llm_upgraded_count": len(upgraded),
        "llm_error_count": len(errors),
        "partial_credit_rate": round(partial_credit_rate, 4),
        "llm_error_rate": round(llm_error_rate, 4),
        "mean_score_delta": mean_score_delta,
        "rules_llm_adjusted": all_adjusted_rules,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_path": str(dataset_path),
        "profile": profile,
    }

    # ── Write outputs ──────────────────────────────────────────────────────
    write_json(summary, out_dir / "llm_marking_summary.json")

    write_csv(
        [{
            "total_submissions": total,
            "llm_ran_successfully": len(llm_ran_ok),
            "llm_upgraded_count": len(upgraded),
            "llm_error_count": len(errors),
            "partial_credit_rate": round(partial_credit_rate, 4),
            "llm_error_rate": round(llm_error_rate, 4),
            "mean_score_delta": mean_score_delta,
            "generated_at": summary["generated_at"],
        }],
        out_dir / "llm_marking_summary.csv",
        fieldnames=[
            "total_submissions", "llm_ran_successfully",
            "llm_upgraded_count", "llm_error_count",
            "partial_credit_rate", "llm_error_rate",
            "mean_score_delta", "generated_at",
        ],
    )

    # Per-submission comparison CSV
    comparison_rows = [
        {
            "id": r["id"],
            "category": r["category"],
            "profile": r["profile"],
            "static_overall": r["static_overall"],
            "llm_overall": r["llm_overall"],
            "score_delta": r["score_delta"],
            "llm_upgraded": r["llm_upgraded"],
            "llm_error_detected": r["llm_error_detected"],
            "adjusted_rules_count": r["adjusted_rules_count"],
            "static_pipeline_error": r["static_pipeline_error"],
            "llm_pipeline_error": r["llm_pipeline_error"],
            "notes": r["notes"],
        }
        for r in records
    ]
    write_csv(
        comparison_rows,
        out_dir / "llm_marking_comparison.csv",
        fieldnames=[
            "id", "category", "profile",
            "static_overall", "llm_overall", "score_delta",
            "llm_upgraded", "llm_error_detected", "adjusted_rules_count",
            "static_pipeline_error", "llm_pipeline_error", "notes",
        ],
    )

    # Per partial-credit item breakdown CSV
    breakdown_rows: list[dict] = []
    for r in records:
        for item in r.get("partial_credit_items") or []:
            hs = item.get("hybrid_score") or {}
            breakdown_rows.append({
                "submission_id": r["id"],
                "finding_id": item.get("finding_id", ""),
                "static_score": hs.get("static_score"),
                "llm_score": hs.get("llm_score"),
                "final_score": hs.get("final_score"),
                "intent_detected": hs.get("intent_detected"),
                "reasoning": str(hs.get("reasoning") or "")[:200],
            })
    if breakdown_rows:
        write_csv(
            breakdown_rows,
            out_dir / "llm_partial_credit_breakdown.csv",
            fieldnames=[
                "submission_id", "finding_id",
                "static_score", "llm_score", "final_score",
                "intent_detected", "reasoning",
            ],
        )

    print("LLM marking evaluation complete:")
    print(f"  Total submissions:      {total}")
    print(f"  LLM ran successfully:   {len(llm_ran_ok)}")
    print(f"  LLM upgraded scores:    {len(upgraded)}")
    print(f"  Partial credit rate:    {partial_credit_rate:.2%}")
    print(f"  LLM error rate:         {llm_error_rate:.2%}")
    print(f"  Mean score delta:       {mean_score_delta:+.4f}")
    print(f"  Rules LLM adjusted:     {list(all_adjusted_rules.keys())}")
    print(f"  Results written to:     {out_dir}")

    return summary
