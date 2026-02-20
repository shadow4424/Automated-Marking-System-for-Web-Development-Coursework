"""Export batch-analytics data as CSV tables and PNG charts.

Usage (CLI)::

    ams export-figures --run-id <run_id> [--runs-root <dir>] [--out <dir>]

The function reads the ``batch_analytics_<run_id>.json`` produced by
:mod:`ams.analytics.batch_analytics` and writes:

* ``score_distribution.csv``  – bucket counts
* ``score_distribution.png``  – bar chart of the distribution
* ``component_readiness.png`` – grouped bar chart per component
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt  # noqa: E402


def export_figures(
    *,
    run_id: str,
    runs_root: Path,
    out_dir: Path,
) -> None:
    """Read analytics JSON for *run_id* and write figures to *out_dir*."""
    analytics_dir = runs_root / run_id / "analytics"
    analytics_path = analytics_dir / f"batch_analytics_{run_id}.json"
    data: dict[str, Any] = json.loads(analytics_path.read_text(encoding="utf-8"))

    out_dir.mkdir(parents=True, exist_ok=True)

    _export_score_distribution(data, out_dir)
    _export_component_readiness(data, out_dir)
    _export_needs_attention(data, out_dir)


# ── score distribution ───────────────────────────────────────────────
def _export_score_distribution(data: dict[str, Any], out_dir: Path) -> None:
    buckets: dict[str, int] = data.get("overall", {}).get("buckets", {})

    # CSV
    csv_path = out_dir / "score_distribution.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["bucket", "count"])
        for label, count in buckets.items():
            writer.writerow([label, count])

    # PNG
    labels = list(buckets.keys())
    counts = list(buckets.values())

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(range(len(labels)), counts, color="#4A90D9")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Students")
    ax.set_title("Score Distribution")
    fig.tight_layout()
    fig.savefig(out_dir / "score_distribution.png", dpi=150)
    plt.close(fig)


# ── component readiness ──────────────────────────────────────────────
def _export_component_readiness(data: dict[str, Any], out_dir: Path) -> None:
    components: dict[str, dict[str, Any]] = data.get("components", {})
    if not components:
        return

    comp_names = list(components.keys())
    averages = [components[c].get("average", 0) for c in comp_names]
    pct_full = [components[c].get("pct_full", 0) for c in comp_names]
    pct_zero = [components[c].get("pct_zero", 0) for c in comp_names]

    x = range(len(comp_names))
    width = 0.25

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar([i - width for i in x], averages, width, label="Average", color="#4A90D9")
    ax.bar(list(x), pct_full, width, label="% Full marks", color="#7EC87E")
    ax.bar([i + width for i in x], pct_zero, width, label="% Zero", color="#E57373")
    ax.set_xticks(list(x))
    ax.set_xticklabels(comp_names, fontsize=9)
    ax.set_ylabel("Value")
    ax.set_title("Component Readiness")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_dir / "component_readiness.png", dpi=150)
    plt.close(fig)


# ── needs attention ──────────────────────────────────────────────────
def _export_needs_attention(data: dict[str, Any], out_dir: Path) -> None:
    items: list[dict[str, Any]] = data.get("needs_attention", [])

    # CSV
    csv_path = out_dir / "needs_attention.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["submission_id", "overall", "reason"])
        for item in items:
            writer.writerow([
                item.get("submission_id", ""),
                item.get("overall", ""),
                item.get("reason", ""),
            ])

    # Top reasons PNG
    from collections import Counter

    reasons = Counter(item.get("reason", "unknown") for item in items)
    if not reasons:
        return

    labels = list(reasons.keys())
    counts = list(reasons.values())

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(range(len(labels)), counts, color="#E57373")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Count")
    ax.set_title("Needs Attention — Top Reasons")
    fig.tight_layout()
    fig.savefig(out_dir / "needs_attention_top_reasons.png", dpi=150)
    plt.close(fig)
