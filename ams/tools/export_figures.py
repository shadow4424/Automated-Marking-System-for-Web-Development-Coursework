from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

try:
    import matplotlib

    matplotlib.use("Agg")  # Deterministic, headless
    import matplotlib.pyplot as plt  # noqa: E402
except ImportError:  # pragma: no cover - dependency is optional at runtime
    matplotlib = None
    plt = None


def _normalise_buckets(buckets: Mapping[str, int]) -> Dict[str, int]:
    agg = {"0.0": 0, "0.5": 0, "1.0": 0}
    for key, val in buckets.items():
        label = str(key).lower()
        target = "0.0"
        if "0.5" in label or "partial" in label or "0-0.5" in label:
            target = "0.5"
        elif ("1" in label or "full" in label) and "0.5" not in label and "partial" not in label:
            target = "1.0"
        agg[target] = agg.get(target, 0) + int(val or 0)
    return agg


def _write_score_distribution(data: Mapping[str, object], out_dir: Path, run_id: str) -> None:
    dist_csv = out_dir / "score_distribution.csv"
    buckets_raw = data.get("overall", {}).get("buckets", {}) if isinstance(data, Mapping) else {}
    buckets = _normalise_buckets(buckets_raw or {})
    with dist_csv.open("w", encoding="utf-8") as fh:
        fh.write("bucket,count\n")
        for bucket in ("0.0", "0.5", "1.0"):
            fh.write(f"{bucket},{buckets.get(bucket,0)}\n")

    fig, ax = plt.subplots(figsize=(5, 3))
    labels = ["0.0", "0.5", "1.0"]
    counts = [buckets.get(k, 0) for k in labels]
    ax.bar(labels, counts, color="#4C7DCB")
    title_profile = data.get("profile", "")
    suffix = f" ({run_id})" if run_id else ""
    if title_profile:
        suffix = f" ({run_id} · {title_profile})"
    ax.set_title(f"Overall score distribution{suffix}")
    ax.set_xlabel("Overall score")
    ax.set_ylabel("Count")
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(out_dir / "score_distribution.png")
    plt.close(fig)


def _ordered_components(comps: Iterable[str]) -> List[str]:
    order = ["html", "css", "js", "php", "sql"]
    known = [c for c in order if c in comps]
    rest = sorted([c for c in comps if c not in order])
    return known + rest


def _write_component_readiness(data: Mapping[str, object], out_dir: Path) -> None:
    comp_csv = out_dir / "component_readiness.csv"
    comps: Mapping[str, Mapping[str, object]] = data.get("components", {}) or {}
    with comp_csv.open("w", encoding="utf-8") as fh:
        fh.write("component,average,pct_zero,pct_half,pct_full,skipped\n")
        for comp in _ordered_components(comps.keys()):
            stats = comps.get(comp, {}) or {}
            fh.write(
                f"{comp},{stats.get('average','')},{stats.get('pct_zero','')},{stats.get('pct_half','')},{stats.get('pct_full','')},{stats.get('skipped','')}\n"
            )

    # Chart
    labels = _ordered_components(comps.keys())
    zeros = [_safe_pct(comps.get(c, {}).get("pct_zero")) for c in labels]
    halves = [_safe_pct(comps.get(c, {}).get("pct_half")) for c in labels]
    fulls = [_safe_pct(comps.get(c, {}).get("pct_full")) for c in labels]
    fig, ax = plt.subplots(figsize=(max(5, len(labels) * 0.9), 3))
    ax.bar(labels, zeros, label="0.0", color="#d9534f")
    bottom_half = [z for z in zeros]
    ax.bar(labels, halves, bottom=bottom_half, label="0.5", color="#f0ad4e")
    bottom_full = [z + h for z, h in zip(zeros, halves)]
    ax.bar(labels, fulls, bottom=bottom_full, label="1.0", color="#5cb85c")
    ax.set_ylabel("Percent of submissions")
    ax.set_title("Component readiness (% 0/0.5/1)")
    ax.set_ylim(0, 100)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "component_readiness.png")
    plt.close(fig)


def _safe_pct(val: object) -> float:
    try:
        return float(val)
    except Exception:
        return 0.0


def _write_needs_attention(data: Mapping[str, object], out_dir: Path) -> None:
    needs_csv = out_dir / "needs_attention.csv"
    needs: Sequence[Mapping[str, object]] = data.get("needs_attention", []) or []
    with needs_csv.open("w", encoding="utf-8") as fh:
        fh.write("submission_id,overall,reason\n")
        for entry in needs:
            fh.write(f"{entry.get('submission_id','')},{entry.get('overall','')},{entry.get('reason','')}\n")

    reasons = [str(entry.get("reason", "other")) for entry in needs]
    counts: Dict[str, int] = {}
    for r in reasons:
        counts[r] = counts.get(r, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    fig, ax = plt.subplots(figsize=(6, 3))
    if top:
        labels = [r for r, _ in top]
        vals = [v for _, v in top]
        ax.bar(labels, vals, color="#4C7DCB")
        ax.set_ylabel("Submissions")
        ax.set_title("Top needs-attention reasons")
    else:
        ax.text(0.5, 0.5, "No submissions need attention", ha="center", va="center")
        ax.set_xticks([])
        ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(out_dir / "needs_attention_top_reasons.png")
    plt.close(fig)


def export_figures(run_id: str, runs_root: Path, out_dir: Path) -> None:
    if plt is None:
        raise RuntimeError("matplotlib is required for export-figures. Install with 'pip install .[dev]'")
    run_dir = runs_root / run_id
    analytics_dir = run_dir / "analytics"
    analytics_json = analytics_dir / f"batch_analytics_{run_id}.json"
    if not analytics_json.exists():
        return
    data = json.loads(analytics_json.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_score_distribution(data, out_dir, run_id)
    _write_component_readiness(data, out_dir)
    _write_needs_attention(data, out_dir)


__all__ = ["export_figures"]
