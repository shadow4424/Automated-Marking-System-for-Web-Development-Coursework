from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from ams.core.pipeline import AssessmentPipeline


@dataclass(frozen=True)
class EvalCase:
    name: str
    profile: str
    path: Path
    expected: Mapping[str, object]


def load_expectations(fixtures_root: Path) -> Dict[str, dict]:
    expectations_path = fixtures_root / "expectations.json"
    data = json.loads(expectations_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expectations root must be a dict keyed by profile")
    return data


def discover_cases(fixtures_root: Path, expectations: Mapping[str, object]) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for profile in sorted(expectations.keys()):
        profile_expectations = expectations[profile] or {}
        if not isinstance(profile_expectations, dict):
            continue
        for case_name in sorted(profile_expectations.keys()):
            case_dir = fixtures_root / profile / case_name
            if not case_dir.exists():
                continue
            expected = profile_expectations[case_name] or {}
            cases.append(EvalCase(name=case_name, profile=profile, path=case_dir, expected=expected))
    return cases


def run_case(case: EvalCase, out_root: Path) -> dict:
    workspace = out_root / case.profile / case.name
    workspace.mkdir(parents=True, exist_ok=True)
    pipeline = AssessmentPipeline()
    report_path = pipeline.run(submission_path=case.path, workspace_path=workspace, profile=case.profile)
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    scores = report_data.get("scores", {})
    findings = report_data.get("findings", []) or []
    categories = derive_categories(findings)
    return {
        "profile": case.profile,
        "case": case.name,
        "overall": scores.get("overall"),
        "by_component": scores.get("by_component", {}),
        "report_path": str(report_path),
        "categories": sorted(categories),
        "findings": findings,
    }


def check_expectations(case: EvalCase, result: Mapping[str, object]) -> List[str]:
    failures: List[str] = []
    expected_overall: Optional[Iterable[float]] = case.expected.get("overall")  # type: ignore[assignment]
    if expected_overall:
        try:
            low, high = float(expected_overall[0]), float(expected_overall[1])  # type: ignore[index]
            overall_value = float(result.get("overall", 0.0))
            if overall_value < low or overall_value > high:
                failures.append(f"overall {overall_value:.2f} outside band [{low}, {high}]")
        except Exception as exc:  # pragma: no cover - defensive
            failures.append(f"invalid overall expectation: {exc}")

    expected_components: Mapping[str, object] = case.expected.get("components", {})  # type: ignore[assignment]
    by_component: Mapping[str, Mapping[str, object]] = result.get("by_component", {})  # type: ignore[assignment]
    for component, expected_score in sorted(expected_components.items()):
        result_entry = by_component.get(component, {})
        actual_score = result_entry.get("score")
        if expected_score == "SKIPPED":
            if actual_score != "SKIPPED":
                failures.append(f"{component} expected SKIPPED got {actual_score}")
            continue
        try:
            expected_numeric = float(expected_score)  # type: ignore[arg-type]
        except Exception:
            failures.append(f"{component} expected score invalid: {expected_score}")
            continue
        try:
            actual_numeric = float(actual_score)
        except Exception:
            failures.append(f"{component} expected {expected_numeric} got {actual_score}")
            continue
        if actual_numeric != expected_numeric:
            failures.append(f"{component} expected {expected_numeric} got {actual_numeric}")
    return failures


def _sorted_components(by_component: Mapping[str, Mapping[str, object]]) -> List[str]:
    order = ["html", "css", "js", "php", "sql"]
    return [c for c in order if c in by_component] + sorted([c for c in by_component.keys() if c not in order])


def write_summary(out_root: Path, results: List[dict], failures: Mapping[tuple, List[str]]) -> None:
    summary = []
    for entry in results:
        case_key = (entry["profile"], entry["case"])
        case_failures = failures.get(case_key, [])
        summary.append(
            {
                "profile": entry["profile"],
                "case": entry["case"],
                "overall": entry.get("overall"),
                "by_component": entry.get("by_component"),
                "report_path": entry.get("report_path"),
                "pass": len(case_failures) == 0,
                "failures": case_failures,
                "categories": entry.get("categories", []),
            }
        )

    summary = sorted(summary, key=lambda r: (r["profile"], r["case"]))
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    csv_path = out_root / "summary.csv"
    fieldnames = ["profile", "case", "overall", "html", "css", "js", "php", "sql", "pass", "categories"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary:
            comps = row.get("by_component") or {}
            ordered = _sorted_components(comps)

            def _score(comp: str):
                entry = comps.get(comp) or {}
                return entry.get("score")

            writer.writerow(
                {
                    "profile": row["profile"],
                    "case": row["case"],
                    "overall": row.get("overall"),
                    "html": _score("html") if "html" in ordered else "",
                    "css": _score("css") if "css" in ordered else "",
                    "js": _score("js") if "js" in ordered else "",
                    "php": _score("php") if "php" in ordered else "",
                    "sql": _score("sql") if "sql" in ordered else "",
                    "pass": row["pass"],
                    "categories": ";".join(row.get("categories") or []),
                }
            )


def evaluate(fixtures_root: Path, out_root: Path, profile: Optional[str] = None) -> dict:
    expectations = load_expectations(fixtures_root)
    cases = discover_cases(fixtures_root, expectations)
    if profile and profile != "all":
        cases = [c for c in cases if c.profile == profile]

    results: List[dict] = []
    failure_map: Dict[tuple, List[str]] = {}
    for case in cases:
        result = run_case(case, out_root)
        failures = check_expectations(case, result)
        results.append(result)
        if failures:
            failure_map[(case.profile, case.name)] = failures

    write_summary(out_root, results, failure_map)
    _write_metrics(out_root, results, cases)
    total = len(cases)
    failed = len(failure_map)
    passed = total - failed
    failing_cases = [
        {"profile": profile, "case": case, "reasons": reasons}
        for (profile, case), reasons in sorted(failure_map.items(), key=lambda x: x[0])
    ]
    return {"total": total, "passed": passed, "failed": failed, "failing_cases": failing_cases, "out_root": out_root}


def derive_categories(findings: List[Mapping[str, object]]) -> Set[str]:
    cats: Set[str] = set()
    for f in findings:
        fid = f.get("id", "")
        sev = f.get("severity", "")
        cat = (f.get("finding_category") or "").lower()
        if cat == "missing":
            cats.add("missing")
        if "SYNTAX" in fid or cat == "syntax":
            if sev in {"WARN", "FAIL"}:
                cats.add("syntax")
        if fid.startswith("BEHAVIOUR."):
            if "SKIPPED" in fid:
                cats.add("runner_limitation")
            elif sev in {"WARN", "FAIL"} or "TIMEOUT" in fid:
                cats.add("runtime_behavioural")
        if fid.startswith("BROWSER."):
            if "SKIPPED" in fid:
                cats.add("runner_limitation")
            elif sev in {"WARN", "FAIL"} or "TIMEOUT" in fid or fid == "BROWSER.CONSOLE_ERRORS_PRESENT":
                cats.add("runtime_browser")
        if fid.startswith("CONSISTENCY.") and sev in {"WARN", "FAIL"}:
            cats.add("consistency")
    if not cats:
        cats.add("other")
    return cats


def _write_metrics(out_root: Path, results: List[dict], cases: List[EvalCase]) -> None:
    buckets = [0.0, 0.5, 1.0]
    cm: Dict[Tuple[float, float], int] = {(e, a): 0 for e in buckets for a in buckets}
    correct = 0
    total = len(results) or 1
    rows = []
    for res, case in zip(results, cases):
        exp_val = case.expected.get("expected_overall", res.get("overall", 0.0))
        try:
            exp = float(exp_val)
        except Exception:
            exp = float(res.get("overall", 0.0))
        act = float(res.get("overall", 0.0))
        if (exp, act) in cm:
            cm[(exp, act)] += 1
        if exp == act:
            correct += 1
        rows.append(
            {
                "case": case.name,
                "expected_overall": exp,
                "actual_overall": act,
                "match": exp == act,
                "expected_categories": ";".join(case.expected.get("expected_issue_categories", []) or []),
                "observed_categories": ";".join(res.get("categories", [])),
            }
        )
    accuracy = correct / total
    summary = {
        "total": total,
        "accuracy": accuracy,
        "confusion": {f"{e}->{a}": v for (e, a), v in cm.items()},
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "evaluation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    results_csv = out_root / "evaluation_results.csv"
    with results_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "case",
                "expected_overall",
                "actual_overall",
                "match",
                "expected_categories",
                "observed_categories",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


__all__ = [
    "EvalCase",
    "load_expectations",
    "discover_cases",
    "run_case",
    "check_expectations",
    "write_summary",
    "evaluate",
    "derive_categories",
]
