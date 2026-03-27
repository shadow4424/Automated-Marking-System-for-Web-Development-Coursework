"""Shared metric computation utilities for the AMS evaluation framework.

All functions are pure (no side effects) and return plain dicts/values
so results can be serialised directly to JSON or CSV.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Binning helper
# ---------------------------------------------------------------------------

def bin_score(score: float | None) -> str:
    """Convert a continuous AMS score to a categorical label (0.0 / 0.5 / 1.0).

    Thresholds:
      >= 0.75  →  "1.0"  (full credit)
      >= 0.25  →  "0.5"  (partial credit)
      <  0.25  →  "0.0"  (no credit)
    """
    if score is None:
        return "None"
    if score >= 0.75:
        return "1.0"
    if score >= 0.25:
        return "0.5"
    return "0.0"


# ---------------------------------------------------------------------------
# Accuracy metrics
# ---------------------------------------------------------------------------

def compute_confusion_matrix(
    predictions: list[float | None],
    expectations: list[float | None],
    classes: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Compute confusion matrix where both values are first binned.

    Returns {expected_label: {predicted_label: count}}.
    """
    if classes is None:
        classes = ["0.0", "0.5", "1.0"]

    matrix: dict[str, dict[str, int]] = {
        c: {p: 0 for p in classes} for c in classes
    }

    for pred, exp in zip(predictions, expectations):
        pred_label = bin_score(pred)
        exp_label = bin_score(exp)
        if exp_label in matrix and pred_label in matrix.get(exp_label, {}):
            matrix[exp_label][pred_label] += 1
        # If label falls outside known classes, skip silently

    return matrix


def compute_overall_accuracy(
    predictions: list[float | None],
    expectations: list[float | None],
) -> float:
    """Exact bin-match rate: fraction where binned prediction == binned expectation."""
    if not predictions:
        return 0.0
    matches = sum(
        1 for p, e in zip(predictions, expectations)
        if bin_score(p) == bin_score(e)
    )
    return matches / len(predictions)


def compute_partial_agreement_rate(
    predictions: list[float | None],
    expectations: list[float | None],
) -> float:
    """Rate where |actual - expected| <= 0.5 (within one bin of correct)."""
    if not predictions:
        return 0.0
    within = sum(
        1 for p, e in zip(predictions, expectations)
        if p is not None and e is not None and abs(p - e) <= 0.5
    )
    return within / len(predictions)


def compute_per_component_accuracy(
    per_sub_results: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute per-component exact-match accuracy.

    per_sub_results: list of dicts, each with keys like
      'html_expected', 'html_actual', 'css_expected', 'css_actual', ...
    """
    components = ["html", "css", "js", "php", "sql", "api"]
    totals: dict[str, int] = {c: 0 for c in components}
    matches: dict[str, int] = {c: 0 for c in components}

    for row in per_sub_results:
        for comp in components:
            exp = row.get(f"{comp}_expected")
            act = row.get(f"{comp}_actual")
            if exp is None or act is None:
                continue
            totals[comp] += 1
            if bin_score(act) == bin_score(exp):
                matches[comp] += 1

    return {
        comp: (matches[comp] / totals[comp]) if totals[comp] > 0 else None
        for comp in components
        if totals[comp] > 0
    }


def compute_false_positives(
    predictions: list[float | None],
    expectations: list[float | None],
) -> int:
    """Count cases where AMS awarded credit (>= 0.25) but expected was 0.0."""
    return sum(
        1 for p, e in zip(predictions, expectations)
        if e is not None and bin_score(e) == "0.0"
        and p is not None and p >= 0.25
    )


def compute_false_negatives(
    predictions: list[float | None],
    expectations: list[float | None],
) -> int:
    """Count cases where AMS denied full credit (< 0.75) but expected was 1.0."""
    return sum(
        1 for p, e in zip(predictions, expectations)
        if e is not None and bin_score(e) == "1.0"
        and p is not None and p < 0.75
    )


# ---------------------------------------------------------------------------
# Consistency metrics
# ---------------------------------------------------------------------------

def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def compute_finding_consistency_rate(finding_sets: list[set[str]]) -> float:
    """Mean pairwise Jaccard similarity across all run pairs."""
    if len(finding_sets) < 2:
        return 1.0
    pairs = [
        jaccard_similarity(finding_sets[i], finding_sets[j])
        for i in range(len(finding_sets))
        for j in range(i + 1, len(finding_sets))
    ]
    return sum(pairs) / len(pairs) if pairs else 1.0


# ---------------------------------------------------------------------------
# Robustness metrics
# ---------------------------------------------------------------------------

def compute_failure_distribution(failure_records: list[dict]) -> dict[str, int]:
    """Count occurrences of each failure_category in the robustness records."""
    distribution: dict[str, int] = {}
    for record in failure_records:
        cat = record.get("failure_category", "UNKNOWN")
        distribution[cat] = distribution.get(cat, 0) + 1
    return distribution


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def write_json(data: dict | list, path: Path) -> None:
    """Write data as indented JSON to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def write_csv(rows: list[dict], path: Path, fieldnames: list[str]) -> None:
    """Write a list of dicts to a CSV file with the specified field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
