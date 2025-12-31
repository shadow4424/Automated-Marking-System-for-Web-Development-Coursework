from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
from typing import Dict, List, Optional

from ams.core.pipeline import AssessmentPipeline
from ams.io.web_storage import safe_extract_zip, find_submission_root


@dataclass(frozen=True)
class BatchItem:
    id: str
    path: Path
    kind: str  # "dir" or "zip"


def discover_batch_items(submissions_dir: Path) -> List[BatchItem]:
    items: List[BatchItem] = []
    for entry in submissions_dir.iterdir():
        name = entry.name
        if name.startswith(".") or name in {"__MACOSX", ".DS_Store"}:
            continue
        if entry.is_dir():
            items.append(BatchItem(id=name, path=entry, kind="dir"))
        elif entry.is_file() and entry.suffix.lower() == ".zip":
            items.append(BatchItem(id=entry.stem, path=entry, kind="zip"))
    items.sort(key=lambda b: b.id)
    return items


def _empty_components() -> Dict[str, Optional[float]]:
    return {"html": None, "css": None, "js": None, "php": None, "sql": None}


def run_batch(
    submissions_dir: Path,
    out_root: Path,
    profile: str,
    keep_individual_runs: bool = True,
) -> dict:
    out_root.mkdir(parents=True, exist_ok=True)
    runs_root = out_root / "runs"
    if keep_individual_runs:
        runs_root.mkdir(parents=True, exist_ok=True)

    pipeline = AssessmentPipeline()
    working_dir = submissions_dir
    temp_ctx: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if submissions_dir.is_file() and submissions_dir.suffix.lower() == ".zip":
            temp_ctx = tempfile.TemporaryDirectory(prefix="ams-bundle-")
            extracted = Path(temp_ctx.name)
            safe_extract_zip(submissions_dir, extracted)
            working_dir = find_submission_root(extracted)
        else:
            working_dir = find_submission_root(submissions_dir)

        items = discover_batch_items(working_dir)
        records: List[dict] = []
        finding_counts: Counter[str] = Counter()
        failure_reason_counts: Counter[str] = Counter()

        for item in items:
            record = _process_one_submission(
                item=item,
                runs_root=runs_root,
                pipeline=pipeline,
                profile=profile,
                keep_individual_runs=keep_individual_runs,
                finding_counts=finding_counts,
                failure_reason_counts=failure_reason_counts,
            )
            records.append(record)

        summary = aggregate_batch(records, finding_counts, failure_reason_counts, profile=profile)
        write_outputs(out_root, records, summary, finding_counts, profile=profile)

        total = summary["total_submissions"]
        succeeded = summary["succeeded"]
        failed = summary["failed"]
        overall_stats = summary.get("overall_stats") or {}
        mean_val = overall_stats.get("mean")
        median_val = overall_stats.get("median")
        print(f"Batch complete. Total: {total}, Succeeded: {succeeded}, Failed: {failed}")
        if mean_val is not None and median_val is not None:
            print(f"Overall mean: {mean_val:.2f}, median: {median_val:.2f}")
        top_findings = summary.get("top_findings", [])[:5]
        if top_findings:
            print("Top findings:")
            for fid, count in top_findings:
                print(f"- {fid}: {count}")

        return {"records": records, "summary": summary}
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()


def aggregate_batch(records: List[dict], finding_counts: Counter[str], failure_reason_counts: Counter[str], profile: str) -> dict:
    total = len(records)
    succeeded_records = [r for r in records if "error" not in r and r.get("overall") is not None]
    failed = total - len(succeeded_records)
    succeeded = len(succeeded_records)

    overall_scores = [float(r["overall"]) for r in succeeded_records if r.get("overall") is not None]
    overall_stats = None
    if overall_scores:
        overall_stats = {
            "mean": statistics.mean(overall_scores),
            "median": statistics.median(overall_scores),
            "min": min(overall_scores),
            "max": max(overall_scores),
        }

    profile_components = ["html", "css", "js"] if profile == "frontend" else ["html", "css", "js", "php", "sql"]
    component_stats: Dict[str, Optional[float]] = {}
    for component in profile_components:
        scores = [
            r["components"].get(component)
            for r in succeeded_records
            if r.get("components")
            and isinstance(r["components"].get(component), (int, float))
        ]
        component_stats[component] = statistics.mean(scores) if scores else None
    for comp in ["html", "css", "js", "php", "sql"]:
        if comp not in component_stats:
            component_stats[comp] = None

    buckets = {
        "zero": 0,
        "gt_0_to_0_5": 0,
        "gt_0_5_to_1": 0,
        "one": 0,
    }
    for score in overall_scores:
        if score == 0.0:
            buckets["zero"] += 1
        elif 0.0 < score <= 0.5:
            buckets["gt_0_to_0_5"] += 1
        elif 0.5 < score < 1.0:
            buckets["gt_0_5_to_1"] += 1
        elif score == 1.0:
            buckets["one"] += 1

    top_findings = sorted(finding_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    top_failure_reasons = sorted(failure_reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    return {
        "total_submissions": total,
        "succeeded": succeeded,
        "failed": failed,
        "overall_stats": overall_stats,
        "component_stats": component_stats,
        "buckets": buckets,
        "finding_frequency": dict(finding_counts),
        "top_findings": top_findings,
        "failure_reason_frequency": dict(failure_reason_counts),
        "top_failure_reasons": top_failure_reasons,
        "profile": profile,
    }


def write_outputs(
    out_root: Path,
    records: List[dict],
    summary: dict,
    finding_counts: Counter[str],
    profile: str,
) -> None:
    batch_summary = {
        "records": records,
        "summary": summary,
    }
    (out_root / "batch_summary.json").write_text(json.dumps(batch_summary, indent=2), encoding="utf-8")

    csv_path = out_root / "batch_summary.csv"
    fieldnames = ["id", "kind", "overall", "html", "css", "js", "php", "sql", "error"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in sorted(records, key=lambda r: r["id"]):
            comps = record.get("components") or {}
            writer.writerow(
                {
                    "id": record.get("id"),
                    "kind": record.get("kind"),
                    "overall": record.get("overall"),
                    "html": comps.get("html"),
                    "css": comps.get("css"),
                    "js": comps.get("js"),
                    "php": comps.get("php") if profile == "fullstack" else "",
                    "sql": comps.get("sql") if profile == "fullstack" else "",
                    "error": record.get("error", ""),
                }
            )

    freq_path = out_root / "findings_frequency.csv"
    with freq_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["finding_id", "count"])
        for fid, count in sorted(finding_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]:
            writer.writerow([fid, count])

    failure_freq = summary.get("failure_reason_frequency") or {}
    failure_path = out_root / "failure_reasons_frequency.csv"
    with failure_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["reason", "count"])
        for reason, count in sorted(failure_freq.items(), key=lambda kv: (-kv[1], kv[0])):
            writer.writerow([reason, count])

    buckets = summary.get("buckets") or {}
    bucket_path = out_root / "score_buckets.csv"
    with bucket_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["bucket", "count"])
        for bucket in ["zero", "gt_0_to_0_5", "gt_0_5_to_1", "one"]:
            writer.writerow([bucket, buckets.get(bucket, 0)])

    comp_stats = summary.get("component_stats") or {}
    comp_path = out_root / "component_means.csv"
    with comp_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["component", "mean"])
        for comp in ["html", "css", "js", "php", "sql"]:
            value = comp_stats.get(comp)
            if profile == "frontend" and comp in {"php", "sql"}:
                writer.writerow([comp, ""])
            else:
                writer.writerow([comp, value if value is not None else ""])


def _normalise_failure_reason(exc: Exception) -> str:
    cls_name = exc.__class__.__name__
    msg = str(exc).strip()
    if msg:
        msg = msg.replace("\n", " ")[:80]
        return f"{cls_name}: {msg}"
    return cls_name


def _process_one_submission(
    item: BatchItem,
    runs_root: Path,
    pipeline: AssessmentPipeline,
    profile: str,
    keep_individual_runs: bool,
    finding_counts: Counter[str],
    failure_reason_counts: Counter[str],
) -> dict:
    record = {
        "id": item.id,
        "path": str(item.path),
        "kind": item.kind,
        "overall": None,
        "components": _empty_components(),
        "report_path": None,
        "original_filename": item.path.name,
    }
    temp_ctx: Optional[tempfile.TemporaryDirectory[str]] = None
    try:
        if keep_individual_runs:
            run_dir = runs_root / item.id
            run_dir.mkdir(parents=True, exist_ok=True)
            workspace_path = run_dir
        else:
            temp_ctx = tempfile.TemporaryDirectory(prefix="ams-batch-")
            workspace_path = Path(temp_ctx.name)

        submission_root = item.path
        if item.kind == "zip":
            extracted = workspace_path / "extracted"
            extracted.mkdir(parents=True, exist_ok=True)
            safe_extract_zip(item.path, extracted)
            submission_root = find_submission_root(extracted)
        elif item.kind == "dir":
            # isolate by copying to workspace
            target = workspace_path / "submission"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item.path, target)
            submission_root = find_submission_root(target)

        report_path = pipeline.run(submission_path=submission_root, workspace_path=workspace_path, profile=profile)
        record["report_path"] = str(report_path)
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        scores = report_data.get("scores", {})
        record["overall"] = scores.get("overall")
        comps = scores.get("by_component", {}) or {}
        record["components"] = {k: comps.get(k, {}).get("score") for k in _empty_components().keys()}
        findings = report_data.get("findings", []) or []
        for f in findings:
            fid = f.get("id")
            if fid:
                finding_counts[fid] += 1
        record["status"] = "ok"
    except Exception as exc:  # pragma: no cover - defensive
        record["error"] = str(exc)
        record["status"] = "error"
        label = _normalise_failure_reason(exc)
        failure_reason_counts[label] += 1
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
    return record


__all__ = [
    "BatchItem",
    "discover_batch_items",
    "run_batch",
    "aggregate_batch",
    "write_outputs",
]
