from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Mapping, Sequence


FINDING_LABELS = {
    "PHP.MISSING_FILES": ("Missing required backend files (PHP)", ""),
    "SQL.MISSING_FILES": ("Missing required backend files (SQL)", ""),
    "CSS.MISSING_FILES": ("CSS missing", "CSS files required but not found"),
    "JS.MISSING_FILES": ("JavaScript missing", "JS files required but not found"),
    "HTML.MISSING_FILES": ("HTML missing", "HTML files required but not found"),
    "PHP.REQ.MISSING_FILES": ("Missing required backend files (PHP)", ""),
    "SQL.REQ.MISSING_FILES": ("Missing required backend files (SQL)", ""),
    "CSS.REQ.MISSING_FILES": ("CSS missing", "CSS files required but not found"),
    "JS.REQ.MISSING_FILES": ("JavaScript missing", "JS files required but not found"),
    "HTML.REQ.MISSING_FILES": ("HTML missing", "HTML files required but not found"),
    "CONFIG.MISSING_REQUIRED_RULES": ("Configuration issue", "Required component has no required rules configured"),
    "CONSISTENCY.JS_MISSING_HTML_ID": ("Cross-file consistency", "JS references HTML ID that does not exist"),
    "CONSISTENCY.JS_MISSING_HTML_CLASS": ("Cross-file consistency", "JS references HTML class that does not exist"),
    "CONSISTENCY.CSS_MISSING_HTML_ID": ("Cross-file consistency", "CSS references HTML ID that does not exist"),
    "CONSISTENCY.CSS_MISSING_HTML_CLASS": ("Cross-file consistency", "CSS references HTML class that does not exist"),
    "CONSISTENCY.PHP_EXPECTS_MISSING_FORM_FIELD": ("Cross-file consistency", "PHP accesses form field not defined in HTML"),
    "CONSISTENCY.FORM_FIELD_UNUSED_IN_PHP": ("Cross-file consistency", "HTML form field not accessed in PHP"),
    "CONSISTENCY.MISSING_LINK_TARGET": ("Cross-file consistency", "Link target does not exist"),
    "CONSISTENCY.MISSING_FORM_ACTION_TARGET": ("Cross-file consistency", "Form action target does not exist"),
    "BEHAVIOUR.PHP_SMOKE_FAIL": ("Runtime/behavioural issues", "PHP entrypoint execution failed"),
    "BEHAVIOUR.PHP_SMOKE_TIMEOUT": ("Runtime/behavioural issues", "PHP smoke test timed out"),
    "BEHAVIOUR.PHP_FORM_RUN_FAIL": ("Runtime/behavioural issues", "PHP form injection failed"),
    "BEHAVIOUR.PHP_FORM_RUN_TIMEOUT": ("Runtime/behavioural issues", "PHP form injection timed out"),
    "BEHAVIOUR.SQL_EXEC_FAIL": ("Runtime/behavioural issues", "SQL execution failed"),
    "BEHAVIOUR.SQL_EXEC_TIMEOUT": ("Runtime/behavioural issues", "SQL execution timed out"),
    "BROWSER.PAGE_LOAD_FAIL": ("Browser/runtime issues", "Browser page load failed"),
    "BROWSER.PAGE_LOAD_TIMEOUT": ("Browser/runtime issues", "Browser page load timed out"),
    "BROWSER.CONSOLE_ERRORS_PRESENT": ("Browser/runtime issues", "Console errors observed"),
}


def build_teacher_analytics(batch_summary: Mapping[str, object]) -> Dict[str, object]:
    records: List[Mapping[str, object]] = batch_summary.get("records", []) or []
    summary: Mapping[str, object] = batch_summary.get("summary", {}) or {}
    profile = summary.get("profile", "unknown")
    overall_stats = summary.get("overall_stats") or {}
    buckets = summary.get("buckets") or {}
    total = summary.get("total_submissions", len(records)) or 1

    component_readiness = _component_readiness(records)
    runtime_health = _runtime_health(records)
    student_issues, runner_limits = _student_and_runner_issues(records, total)
    needs_attention = _needs_attention(records, profile)

    analytics = {
        "profile": profile,
        "overall": {
            "mean": overall_stats.get("mean"),
            "median": overall_stats.get("median"),
            "min": overall_stats.get("min"),
            "max": overall_stats.get("max"),
            "total": total,
            "buckets": {
                "No attempt (0)": buckets.get("zero", 0),
                "Partial (0–0.5]": buckets.get("gt_0_to_0_5", 0),
                "Good partial (0.5–1)": buckets.get("gt_0_5_to_1", 0),
                "Full marks (1)": buckets.get("one", 0),
            },
        },
        "components": component_readiness,
        "runtime_health": runtime_health,
        "student_issues": student_issues,
        "runner_limitations": runner_limits,
        "needs_attention": needs_attention,
        "common_issues": [],
        "other_checks": [],
    }
    return analytics


def _component_readiness(records: List[Mapping[str, object]]) -> Dict[str, dict]:
    comps = ["html", "css", "js", "php", "sql"]
    result: Dict[str, dict] = {}
    for comp in comps:
        scores: List[float] = []
        for rec in records:
            score = rec.get("components", {}).get(comp)
            if isinstance(score, (int, float)):
                scores.append(float(score))
        n = len(scores)
        zeros = len([s for s in scores if s == 0])
        full = len([s for s in scores if s == 1])
        half = len([s for s in scores if s == 0.5])
        avg = sum(scores) / n if n else None
        result[comp] = {
            "average": avg,
            "pct_zero": (zeros / n * 100) if n else None,
            "pct_half": (half / n * 100) if n else None,
            "pct_full": (full / n * 100) if n else None,
            "skipped": len([1 for rec in records if rec.get("components", {}).get(comp) == "SKIPPED"]),
        }
    return result


def _needs_attention(records: List[Mapping[str, object]], profile: str) -> List[dict]:
    attention: List[dict] = []
    for rec in sorted(records, key=lambda r: r.get("id", "")):
        status = rec.get("status", "ok")
        overall = rec.get("overall")
        comps = rec.get("components", {})
        flags: List[str] = []
        if status != "ok":
            flags.append("error")
        if overall == 0 or overall is None:
            flags.append("no score")
        if profile == "fullstack":
            for comp in ["php", "sql"]:
                if comps.get(comp) == 0:
                    flags.append(f"{comp} missing")
        reason = _primary_reason(rec)
        if flags or reason != "other":
            attention.append(
                {
                    "submission_id": rec.get("id"),
                    "overall": overall,
                    "status": status,
                    "flags": flags,
                    "reason": reason,
                    "report_path": rec.get("report_path"),
                }
            )
        rec["primary_reason"] = reason
    return attention


def _primary_reason(rec: Mapping[str, object]) -> str:
    findings = rec.get("findings", []) or []
    priorities: Sequence[tuple[str, str]] = [
        ("missing", "missing required files"),
        ("BEHAVIOUR.", "behavioural runtime issue"),
        ("SYNTAX", "syntax issue"),
        ("BROWSER.", "browser runtime issue"),
        ("CONSISTENCY.", "consistency issue"),
    ]
    for prefix, label in priorities:
        for f in findings:
            if f.get("finding_category") == "missing" and prefix == "missing":
                return label
            if prefix in f.get("id", ""):
                return label
    return "other"


def _runtime_health(records: List[Mapping[str, object]]) -> dict:
    behavioural_counts = Counter()
    browser_counts = Counter()
    console_errors = 0
    total = len(records) or 1
    for rec in records:
        findings = rec.get("findings", []) or []
        for status in _status_flags(findings, prefix="BEHAVIOUR."):
            behavioural_counts[status] += 1
        for status in _status_flags(findings, prefix="BROWSER.PAGE_LOAD"):
            browser_counts[status] += 1
        if any(f.get("id") == "BROWSER.CONSOLE_ERRORS_PRESENT" for f in findings):
            console_errors += 1
    return {
        "behavioural": dict(behavioural_counts),
        "browser": dict(browser_counts),
        "console_error_pct": (console_errors / total * 100) if total else 0,
        "behavioural_skipped_pct": (behavioural_counts.get("skipped", 0) / total * 100) if total else 0,
        "browser_skipped_pct": (browser_counts.get("skipped", 0) / total * 100) if total else 0,
    }


def _status_flags(findings: List[Mapping[str, object]], prefix: str) -> set[str]:
    flags: set[str] = set()
    for f in findings:
        fid = f.get("id", "")
        if not fid.startswith(prefix):
            continue
        if fid.endswith("PASS"):
            flags.add("pass")
        elif fid.endswith("TIMEOUT"):
            flags.add("timeout")
        elif fid.endswith("SKIPPED"):
            flags.add("skipped")
        else:
            flags.add("fail")
    return flags


def _student_and_runner_issues(records: List[Mapping[str, object]], total: int) -> tuple[List[dict], List[dict]]:
    categories = defaultdict(list)
    runner_limits = defaultdict(list)
    for rec in records:
        sid = rec.get("id")
        findings = rec.get("findings", []) or []
        for f in findings:
            severity = f.get("severity")
            fid = f.get("id", "")
            cat = f.get("finding_category")
            if fid.startswith("BEHAVIOUR.") and "SKIPPED" in fid:
                runner_limits["behavioural"].append(sid)
                continue
            if fid.startswith("BROWSER.PAGE_LOAD") and "SKIPPED" in fid:
                runner_limits["browser"].append(sid)
                continue
            if severity not in {"WARN", "FAIL"}:
                continue
            if fid == "BROWSER.CONSOLE_ERRORS_PRESENT":
                categories["browser_runtime"].append((sid, fid))
                continue
            if cat == "missing":
                if fid.startswith("PHP.") or fid.startswith("SQL."):
                    categories["missing_backend"].append((sid, fid))
                else:
                    categories["missing_frontend"].append((sid, fid))
                continue
            if fid.startswith("CONSISTENCY."):
                categories["consistency"].append((sid, fid))
                continue
            if fid.startswith("BEHAVIOUR."):
                categories["behavioural"].append((sid, fid))
                continue
            if fid.startswith("BROWSER."):
                categories["browser_runtime"].append((sid, fid))
                continue
            if fid.endswith("SYNTAX_ERROR") or fid.endswith("PARSE_ERROR"):
                categories["syntax"].append((sid, fid))
                continue
            categories["other"].append((sid, fid))

    def _summary(cat: str, entries: list[tuple[str, str]]) -> dict:
        if not entries:
            return {}
        subs = [s for s, _ in entries]
        ids = [fid for _, fid in entries]
        return {
            "category": cat,
            "students_affected": len(set(subs)),
            "percent": (len(set(subs)) / total * 100) if total else 0,
            "finding_ids": sorted(set(ids)),
            "examples": list(dict.fromkeys(subs))[:3],
        }

    student_sections = [
        _summary("missing_backend", categories["missing_backend"]),
        _summary("missing_frontend", categories["missing_frontend"]),
        _summary("syntax", categories["syntax"]),
        _summary("behavioural_runtime", categories["behavioural"]),
        _summary("browser_runtime", categories["browser_runtime"]),
        _summary("consistency", categories["consistency"]),
        _summary("other", categories["other"]),
    ]
    student_sections = [s for s in student_sections if s]

    runner_sections = [
        _summary("behavioural_skipped", [(s, "BEHAVIOUR.SKIPPED") for s in runner_limits["behavioural"]]),
        _summary("browser_skipped", [(s, "BROWSER.PAGE_LOAD_SKIPPED") for s in runner_limits["browser"]]),
    ]
    runner_sections = [r for r in runner_sections if r]
    return student_sections, runner_sections


__all__ = ["build_teacher_analytics", "FINDING_LABELS"]
