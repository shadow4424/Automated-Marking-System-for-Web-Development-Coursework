from __future__ import annotations
import csv
import io
import json
import textwrap
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

EXPORT_SCHEMA_VERSION = "2.0"

# ---------------------------------------------------------------------------
# Status constants  (strings, not enums, for JSON serialisability)
# ---------------------------------------------------------------------------

STATUS_PASS = "PASS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAIL = "FAIL"
STATUS_SKIPPED_BY_PROFILE = "SKIPPED_BY_PROFILE"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_MISSING_REQUIRED = "MISSING_REQUIRED"
STATUS_ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
STATUS_ERROR_DURING_ANALYSIS = "ERROR_DURING_ANALYSIS"
STATUS_NO_RELEVANT_FILES = "NO_RELEVANT_FILES"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExportFinding:
    finding_id: str        # e.g. "HTML.MISSING_DOCTYPE"
    component: str         # "html", "css", "js", etc.
    severity: str          # FAIL, WARN, INFO, THREAT, SKIPPED
    message: str
    finding_category: str  # missing, syntax, structure, behavioral, etc.
    evidence_summary: str  # truncated repr of evidence dict, max 300 chars


@dataclass
class RuleOutcome:
    requirement_id: str   # e.g. "HTML.VALID_DOCTYPE"
    component: str
    description: str
    stage: str            # "static", "runtime", "browser", "quality", "layout"
    status: str           # one of STATUS_* constants
    score: Any            # 0.0, 0.5, 1.0, or "SKIPPED"
    score_label: str      # human explanation of 1/0.5/0, empty if SKIPPED
    weight: float
    skipped_reason: str   # "" if not skipped


@dataclass
class ComponentResult:
    name: str             # "html", "css", "js", "php", "sql", "api"
    score: Any            # numeric 0.0–1.0 or "SKIPPED"
    score_pct: str        # "80.00%" or "SKIPPED"
    required: bool
    met: int
    partial: int
    failed: int
    skipped: int
    weight: float


@dataclass
class ExecutionEvidence:
    php_available: bool
    browser_available: bool
    behavioural_tests_run: bool
    browser_tests_run: bool
    behavioural_results: list  # list of dicts: {test_id, status, diagnostic}
    browser_results: list      # list of dicts: {test_id, status, diagnostic}


@dataclass
class ExportReport:
    export_schema_version: str
    # Identity
    run_id: str
    report_version: str
    generated_at: str
    # Submission metadata
    student_id: str
    assignment_id: str
    original_filename: str
    submitted_at: str
    profile: str
    scoring_mode: str
    pipeline_version: str
    # Overall result
    overall_score: Any         # float | None
    overall_pct: str
    overall_label: str
    confidence_level: str
    confidence_reasons: list   # list[str]
    confidence_flags: list     # list[str]
    manual_review: bool
    manual_review_reasons: list  # list[str]
    # Breakdown
    components: list           # list[ComponentResult]
    findings: list             # list[ExportFinding]
    rule_outcomes: list        # list[RuleOutcome]
    # Execution
    execution: ExecutionEvidence
    # Policy
    policy_notes: list         # list[str]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _map_requirement_status(status: str, skipped_reason: str | None) -> str:
    """Map pipeline status/skipped_reason pair to an export STATUS_* constant."""
    sr = (skipped_reason or "").lower()

    # Skipped variants first (most nuanced)
    if status.lower() in ("skipped",):
        if sr in ("component_not_required", "not_applicable") or not sr:
            return STATUS_SKIPPED_BY_PROFILE
        if sr in ("runtime_skipped", "browser_skipped"):
            return STATUS_NOT_RUN
        if sr == "environment_unavailable":
            return STATUS_ENVIRONMENT_UNAVAILABLE
        # anything else skipped
        return STATUS_SKIPPED_BY_PROFILE

    # FAIL / not_met with special skipped_reason overrides
    if status.lower() in ("fail", "not_met", "failed"):
        if sr == "no_relevant_files":
            return STATUS_NO_RELEVANT_FILES
        if sr == "missing_required":
            return STATUS_MISSING_REQUIRED
        return STATUS_FAIL

    # Positive outcomes
    if status.lower() in ("met", "pass"):
        return STATUS_PASS
    if status.lower() in ("partial",):
        return STATUS_PARTIAL

    # Fallback: upper-case the raw status
    return status.upper()


def _score_label(score: Any) -> str:
    """Return a human-readable explanation of a numeric score."""
    if not isinstance(score, (int, float)):
        return ""
    if score >= 0.9:
        return "Strong or complete evidence of the expected solution."
    if score >= 0.4:
        return "Evident attempt, but important issues, incomplete integration, or major faults."
    return "Absent, unrelated, or too broken to demonstrate the intended requirement."


def _score_pct(score: Any) -> str:
    """Format a score as a percentage string, or return str(score) for non-numerics."""
    if isinstance(score, (int, float)):
        return f"{float(score) * 100:.2f}%"
    return str(score)


def _overall_label(score: Any) -> str:
    """Return a grade label based on overall numeric score."""
    if score is None:
        return "Unknown"
    if isinstance(score, (int, float)):
        if score >= 0.70:
            return "Pass"
        if score >= 0.40:
            return "Marginal Fail"
        return "Fail"
    return "Unknown"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


def build_export_report(report_json: dict, run_id: str = "") -> ExportReport:
    """Map from the actual report.json structure to a canonical ExportReport."""
    meta = report_json.get("metadata") or {}
    sub_meta = (meta.get("submission_metadata") or {}) if isinstance(meta, dict) else {}
    scores = report_json.get("scores") or {}
    score_ev = report_json.get("score_evidence") or {}
    env = report_json.get("environment") or {}
    policy = report_json.get("marking_policy") or {}

    # Overall score — try scores.overall first, fall back to score_evidence.overall.final
    overall_raw = scores.get("overall")
    if overall_raw is None and isinstance(score_ev.get("overall"), dict):
        overall_raw = score_ev["overall"].get("final") or score_ev["overall"].get("raw_average")
    overall_score = float(overall_raw) if overall_raw is not None else None

    # Confidence
    conf = (score_ev.get("confidence") or {}) if isinstance(score_ev, dict) else {}
    review = (score_ev.get("review") or {}) if isinstance(score_ev, dict) else {}

    # Components — from score_evidence.components
    comp_data = (score_ev.get("components") or {}) if isinstance(score_ev, dict) else {}
    components = []
    for name, data in (comp_data.items() if isinstance(comp_data, dict) else []):
        raw_score = data.get("score") if isinstance(data, dict) else None
        components.append(ComponentResult(
            name=name,
            score=raw_score,
            score_pct=_score_pct(raw_score),
            required=bool(data.get("required", True)) if isinstance(data, dict) else True,
            met=int(data.get("met", 0)) if isinstance(data, dict) else 0,
            partial=int(data.get("partial", 0)) if isinstance(data, dict) else 0,
            failed=int(data.get("failed", 0)) if isinstance(data, dict) else 0,
            skipped=int(data.get("skipped", 0)) if isinstance(data, dict) else 0,
            weight=float(data.get("weight", 0.0)) if isinstance(data, dict) else 0.0,
        ))

    # If no components from score_evidence, fall back to scores.by_component
    if not components and isinstance(scores.get("by_component"), dict):
        for name, data in scores["by_component"].items():
            raw_score = data.get("score") if isinstance(data, dict) else data
            components.append(ComponentResult(
                name=name,
                score=raw_score,
                score_pct=_score_pct(raw_score),
                required=True,
                met=0, partial=0, failed=0, skipped=0, weight=0.0,
            ))

    # Findings
    findings_raw = report_json.get("findings") or []
    findings = []
    for f in (findings_raw if isinstance(findings_raw, list) else []):
        if not isinstance(f, dict):
            continue
        ev = f.get("evidence") or {}
        ev_summary = str(ev)[:300] if ev else ""
        findings.append(ExportFinding(
            finding_id=str(f.get("id", "")),
            component=str(f.get("category", "")),
            severity=str(f.get("severity", "")),
            message=str(f.get("message", "")),
            finding_category=str(f.get("finding_category", "other")),
            evidence_summary=ev_summary,
        ))

    # Rule outcomes — from score_evidence.requirements
    reqs_raw = (score_ev.get("requirements") or []) if isinstance(score_ev, dict) else []
    rule_outcomes = []
    for req in (reqs_raw if isinstance(reqs_raw, list) else []):
        if not isinstance(req, dict):
            continue
        raw_status = str(req.get("status", ""))
        skipped_reason = req.get("skipped_reason") or ""
        mapped_status = _map_requirement_status(raw_status, skipped_reason or None)
        raw_score = req.get("score")
        rule_outcomes.append(RuleOutcome(
            requirement_id=str(req.get("requirement_id", "")),
            component=str(req.get("component", "")),
            description=str(req.get("description", "")),
            stage=str(req.get("stage", "")),
            status=mapped_status,
            score=raw_score,
            score_label=_score_label(raw_score),
            weight=float(req.get("weight", 1.0)),
            skipped_reason=str(skipped_reason),
        ))

    # Execution evidence
    beh_raw = report_json.get("behavioural_evidence") or []
    brow_raw = report_json.get("browser_evidence") or []
    beh_results = []
    for e in (beh_raw if isinstance(beh_raw, list) else []):
        if not isinstance(e, dict):
            continue
        diag = (e.get("stderr") or e.get("stdout") or "")
        diag_line = diag.splitlines()[0][:200] if diag else ""
        beh_results.append({
            "test_id": str(e.get("test_id", "")),
            "status": str(e.get("status", "")).upper(),
            "diagnostic": diag_line,
        })
    brow_results = []
    for e in (brow_raw if isinstance(brow_raw, list) else []):
        if not isinstance(e, dict):
            continue
        console = e.get("console_errors") or []
        diag = e.get("notes") or (console[0] if console else "")
        brow_results.append({
            "test_id": str(e.get("test_id", "")),
            "status": str(e.get("status", "")).upper(),
            "diagnostic": str(diag)[:200],
        })

    execution = ExecutionEvidence(
        php_available=bool(env.get("php_available", False)),
        browser_available=bool(env.get("browser_available", False)),
        behavioural_tests_run=bool(env.get("behavioural_tests_run", False)),
        browser_tests_run=bool(env.get("browser_tests_run", False)),
        behavioural_results=beh_results,
        browser_results=brow_results,
    )

    policy_notes_list = []
    if isinstance(policy, dict):
        raw_notes = policy.get("notes") or []
        policy_notes_list = list(raw_notes) if isinstance(raw_notes, list) else []
    if not policy_notes_list:
        policy_notes_list = [
            "SKIPPED = Component not applicable to this profile; no impact on marks.",
            "MISSING = Component required for this profile but absent; scores 0.",
            "CONFIG warnings = Marker configuration issues; do not affect student scores.",
        ]

    return ExportReport(
        export_schema_version=EXPORT_SCHEMA_VERSION,
        run_id=run_id,
        report_version=str(report_json.get("report_version", "1.0")),
        generated_at=str(report_json.get("generated_at", "")),
        student_id=str(sub_meta.get("student_id", "") or ""),
        assignment_id=str(sub_meta.get("assignment_id", "") or ""),
        original_filename=str(sub_meta.get("original_filename", "") or ""),
        submitted_at=str(sub_meta.get("timestamp", "") or ""),
        profile=str(meta.get("profile", "") or ""),
        scoring_mode=str(meta.get("scoring_mode", "") or ""),
        pipeline_version=str(meta.get("pipeline_version", "") or ""),
        overall_score=overall_score,
        overall_pct=_score_pct(overall_score) if overall_score is not None else "N/A",
        overall_label=_overall_label(overall_score),
        confidence_level=str(conf.get("level", "") or ""),
        confidence_reasons=list(conf.get("reasons", []) or []),
        confidence_flags=list(conf.get("flags", []) or []),
        manual_review=bool(review.get("recommended", False)),
        manual_review_reasons=list(review.get("reasons", []) or []),
        components=components,
        findings=findings,
        rule_outcomes=rule_outcomes,
        execution=execution,
        policy_notes=policy_notes_list,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_export_report(report: ExportReport) -> None:
    """Raise ValueError if the report is too empty to export meaningfully."""
    if (
        report.student_id == ""
        and report.assignment_id == ""
        and report.run_id == ""
        and report.generated_at == ""
    ):
        raise ValueError("No submission identity found")

    if (
        report.overall_score is None
        and not report.rule_outcomes
        and not report.findings
    ):
        raise ValueError("Report contains no scoreable data")

    if (
        not report.components
        and not report.rule_outcomes
        and not report.findings
    ):
        raise ValueError(
            "Report contains no assessment data (no components, rule outcomes, or findings)"
        )


# ---------------------------------------------------------------------------
# Export: JSON
# ---------------------------------------------------------------------------


def export_json(report: ExportReport) -> str:
    """Serialize ExportReport to a rich structured JSON string."""
    data = asdict(report)
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Export: TXT
# ---------------------------------------------------------------------------

_SEP_MAJOR = "=" * 80
_SEP_MINOR = "-" * 40


def export_txt(report: ExportReport) -> str:
    """Render an 80-char-wide human-readable text report."""
    lines: list[str] = []

    def _section(title: str) -> None:
        lines.append(_SEP_MAJOR)
        lines.append(title)
        lines.append(_SEP_MAJOR)

    def _subsection(title: str) -> None:
        lines.append(_SEP_MINOR)
        lines.append(title)
        lines.append(_SEP_MINOR)

    def _wrap(text: str) -> str:
        return textwrap.fill(
            text, width=76, initial_indent="    ", subsequent_indent="    "
        )

    # Section 1: Header
    _section("SUBMISSION ASSESSMENT REPORT")
    lines.append(f"  Run ID:    {report.run_id}")
    lines.append(f"  Generated: {report.generated_at}")
    lines.append("")

    # Section 2: Submission details
    _section("SUBMISSION DETAILS")
    lines.append(f"  Student ID:       {report.student_id}")
    lines.append(f"  Assignment ID:    {report.assignment_id}")
    lines.append(f"  Original File:    {report.original_filename}")
    lines.append(f"  Submitted At:     {report.submitted_at}")
    lines.append(f"  Profile:          {report.profile}")
    lines.append(f"  Scoring Mode:     {report.scoring_mode}")
    lines.append(f"  Pipeline Version: {report.pipeline_version}")
    lines.append("")

    # Section 3: Overall result
    _section("OVERALL RESULT")
    lines.append(f"  Score:                      {report.overall_pct}  [{report.overall_label}]")
    lines.append(f"  Confidence:                 {report.confidence_level}")
    lines.append(f"  Manual Review Recommended:  {'Yes' if report.manual_review else 'No'}")
    if report.confidence_reasons:
        lines.append("  Confidence Reasons:")
        for r in report.confidence_reasons:
            lines.append(f"  - {r}")
    if report.manual_review and report.manual_review_reasons:
        lines.append("  Manual Review Reasons:")
        for r in report.manual_review_reasons:
            lines.append(f"  - {r}")
    lines.append("")

    # Section 4: Component scores
    _section("COMPONENT SCORES")
    if report.components:
        for comp in report.components:
            req_label = "required" if comp.required else "not required for profile"
            lines.append(f"  {comp.name.upper()}: {comp.score_pct}  ({req_label})")
            lines.append(
                f"    Met: {comp.met}  Partial: {comp.partial}  "
                f"Failed: {comp.failed}  Skipped: {comp.skipped}  (weight: {comp.weight})"
            )
    else:
        lines.append("  No component data available.")
    lines.append("")

    # Section 5: Findings (skip if empty)
    if report.findings:
        _section("FINDINGS")
        severity_order = ["THREAT", "FAIL", "WARN", "INFO", "SKIPPED"]
        by_severity: dict[str, list] = {s: [] for s in severity_order}
        other: list = []
        for f in report.findings:
            sev = f.severity.upper()
            if sev in by_severity:
                by_severity[sev].append(f)
            else:
                other.append(f)
        ordered = []
        for sev in severity_order:
            ordered.extend(by_severity[sev])
        ordered.extend(other)

        for f in ordered:
            lines.append(
                f"  [{f.severity}] {f.finding_id}  ({f.component} / {f.finding_category})"
            )
            lines.append(_wrap(f.message))
        lines.append("")

    # Section 6: Rule outcomes (skip if empty)
    if report.rule_outcomes:
        _section("RULE OUTCOMES")
        # Group by component
        comp_map: dict[str, list] = {}
        for ro in report.rule_outcomes:
            comp_map.setdefault(ro.component, []).append(ro)

        for comp_name, outcomes in comp_map.items():
            _subsection(comp_name.upper())
            for ro in outcomes:
                score_display = (
                    _score_pct(ro.score) if isinstance(ro.score, (int, float))
                    else str(ro.score)
                )
                lines.append(f"  {ro.requirement_id}  [{ro.status}]  Score: {score_display}")
                lines.append(f"    Stage: {ro.stage}  |  Weight: {ro.weight}")
                lines.append(_wrap(ro.description))
                if ro.score_label:
                    lines.append(_wrap(f"Score rationale: {ro.score_label}"))
        lines.append("")

    # Section 7: Execution summary
    _section("EXECUTION SUMMARY")
    php_str = "available" if report.execution.php_available else "unavailable"
    brow_str = "available" if report.execution.browser_available else "unavailable"
    lines.append(f"  PHP Runtime:         {php_str}")
    lines.append(f"  Browser Runtime:     {brow_str}")

    beh_count = len(report.execution.behavioural_results)
    beh_run_str = (
        f"run ({beh_count} tests)" if report.execution.behavioural_tests_run else "not run"
    )
    brow_count = len(report.execution.browser_results)
    brow_run_str = (
        f"run ({brow_count} tests)" if report.execution.browser_tests_run else "not run"
    )
    lines.append(f"  Behavioural Tests:   {beh_run_str}")
    lines.append(f"  Browser Tests:       {brow_run_str}")

    if report.execution.behavioural_results:
        lines.append("  Behavioural Test Results:")
        for e in report.execution.behavioural_results:
            diag = f" - {e['diagnostic']}" if e["diagnostic"] else ""
            lines.append(f"    {e['test_id']}: {e['status']}{diag}")

    if report.execution.browser_results:
        lines.append("  Browser Test Results:")
        for e in report.execution.browser_results:
            diag = f" - {e['diagnostic']}" if e["diagnostic"] else ""
            lines.append(f"    {e['test_id']}: {e['status']}{diag}")
    lines.append("")

    # Section 8: Policy notes
    _section("MARKING POLICY NOTES")
    for note in report.policy_notes:
        lines.append(f"  - {note}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export: CSV helpers
# ---------------------------------------------------------------------------


def export_csv_summary(report: ExportReport) -> str:
    """One-row CSV with overall scores and component breakdown."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    headers = [
        "run_id", "student_id", "assignment_id", "profile", "scoring_mode", "generated_at",
        "overall_score", "overall_pct", "overall_label", "confidence_level",
        "manual_review_recommended",
        "html_score", "css_score", "js_score", "php_score", "sql_score", "api_score",
        "total_findings", "fail_findings", "warn_findings", "threat_findings",
        "total_rules", "met_rules", "partial_rules", "failed_rules", "skipped_rules",
    ]
    writer.writerow(headers)

    # Component score lookup
    comp_by_name = {c.name.lower(): c for c in report.components}

    def _comp_score(name: str) -> str:
        c = comp_by_name.get(name)
        return c.score_pct if c is not None else ""

    # Finding counts
    fail_findings = sum(1 for f in report.findings if f.severity.upper() == "FAIL")
    warn_findings = sum(1 for f in report.findings if f.severity.upper() == "WARN")
    threat_findings = sum(1 for f in report.findings if f.severity.upper() == "THREAT")

    # Rule counts
    _fail_statuses = {STATUS_FAIL, STATUS_NO_RELEVANT_FILES, STATUS_MISSING_REQUIRED}
    _skipped_statuses = {STATUS_SKIPPED_BY_PROFILE, STATUS_NOT_RUN, STATUS_ENVIRONMENT_UNAVAILABLE}
    met_rules = sum(1 for r in report.rule_outcomes if r.status == STATUS_PASS)
    partial_rules = sum(1 for r in report.rule_outcomes if r.status == STATUS_PARTIAL)
    failed_rules = sum(1 for r in report.rule_outcomes if r.status in _fail_statuses)
    skipped_rules = sum(1 for r in report.rule_outcomes if r.status in _skipped_statuses)

    row = [
        report.run_id,
        report.student_id,
        report.assignment_id,
        report.profile,
        report.scoring_mode,
        report.generated_at,
        report.overall_score if report.overall_score is not None else "",
        report.overall_pct,
        report.overall_label,
        report.confidence_level,
        "Yes" if report.manual_review else "No",
        _comp_score("html"),
        _comp_score("css"),
        _comp_score("js"),
        _comp_score("php"),
        _comp_score("sql"),
        _comp_score("api"),
        len(report.findings),
        fail_findings,
        warn_findings,
        threat_findings,
        len(report.rule_outcomes),
        met_rules,
        partial_rules,
        failed_rules,
        skipped_rules,
    ]
    writer.writerow(row)
    return buf.getvalue()


def export_csv_findings(report: ExportReport) -> str:
    """One row per finding."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    headers = [
        "run_id", "student_id", "assignment_id",
        "finding_id", "component", "severity", "finding_category", "message", "evidence_summary",
    ]
    writer.writerow(headers)
    for f in report.findings:
        writer.writerow([
            report.run_id,
            report.student_id,
            report.assignment_id,
            f.finding_id,
            f.component,
            f.severity,
            f.finding_category,
            f.message,
            f.evidence_summary,
        ])
    return buf.getvalue()


def export_csv_rules(report: ExportReport) -> str:
    """One row per rule outcome."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    headers = [
        "run_id", "student_id", "assignment_id",
        "requirement_id", "component", "stage", "description",
        "status", "score", "score_label", "weight", "skipped_reason",
    ]
    writer.writerow(headers)
    for ro in report.rule_outcomes:
        writer.writerow([
            report.run_id,
            report.student_id,
            report.assignment_id,
            ro.requirement_id,
            ro.component,
            ro.stage,
            ro.description,
            ro.status,
            ro.score if ro.score is not None else "",
            ro.score_label,
            ro.weight,
            ro.skipped_reason,
        ])
    return buf.getvalue()


def export_csv_zip(report: ExportReport) -> bytes:
    """Create an in-memory ZIP containing three CSVs: summary, findings, rules."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("submission_summary.csv", export_csv_summary(report))
        zf.writestr("submission_findings.csv", export_csv_findings(report))
        zf.writestr("submission_rules.csv", export_csv_rules(report))
        readme = (
            "AMS Export Bundle\n"
            "=================\n"
            "submission_summary.csv  - One row per submission with overall scores and component breakdown\n"
            "submission_findings.csv - One row per finding (linting/static analysis results)\n"
            "submission_rules.csv    - One row per rule outcome with scoring rationale\n"
        )
        zf.writestr("README.txt", readme)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Export: PDF
# ---------------------------------------------------------------------------


def export_pdf(report: ExportReport) -> bytes:
    """Generate a rich PDF report from the canonical ExportReport."""
    from ams.pdf_exports import build_rich_submission_pdf  # lazy import to avoid circular deps
    return build_rich_submission_pdf(report)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "STATUS_PASS", "STATUS_PARTIAL", "STATUS_FAIL",
    "STATUS_SKIPPED_BY_PROFILE", "STATUS_NOT_RUN", "STATUS_MISSING_REQUIRED",
    "STATUS_ENVIRONMENT_UNAVAILABLE", "STATUS_ERROR_DURING_ANALYSIS", "STATUS_NO_RELEVANT_FILES",
    "ExportFinding", "RuleOutcome", "ComponentResult", "ExecutionEvidence", "ExportReport",
    "build_export_report", "validate_export_report",
    "export_json", "export_txt", "export_csv_summary", "export_csv_findings",
    "export_csv_rules", "export_csv_zip", "export_pdf",
]
