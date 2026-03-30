"""Minimal PDF export helpers without external runtime dependencies."""
from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT_MARGIN = 50
RIGHT_MARGIN = 50
TOP_MARGIN = 60
BOTTOM_MARGIN = 60
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
FIELD_INDENT = 16


@dataclass(frozen=True)
class _PdfLine:
    """A renderable line in the PDF output."""
    text: str
    size: int = 10
    bold: bool = False
    indent: int = 0
    spacing_before: int = 0
    separator: bool = False


def _max_line_chars(font_size: int, indent: int = 0) -> int:
    """Calculate the maximum number of characters that will fit on a single line."""
    usable_width = CONTENT_WIDTH - indent
    avg_char_width = font_size * 0.52
    return max(int(usable_width / avg_char_width), 30)


def build_submission_report_pdf(report: Mapping[str, Any], submission_id: str) -> bytes:
    """Constructs a PDF report for an individual student submission."""
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    scores = report.get("scores") if isinstance(report.get("scores"), Mapping) else {}
    score_evidence = report.get("score_evidence") if isinstance(report.get("score_evidence"), Mapping) else {}
    meta_root = report.get("metadata") if isinstance(report.get("metadata"), Mapping) else {}
    meta = meta_root.get("submission_metadata") if isinstance(meta_root.get("submission_metadata"), Mapping) else {}
    components = report.get("components") if isinstance(report.get("components"), Mapping) else {}
    if not components:
        by_component = scores.get("by_component")
        components = by_component if isinstance(by_component, Mapping) else {}

    confidence = _first_non_empty(
        summary.get("confidence"),
        score_evidence.get("confidence", {}).get("level") if isinstance(score_evidence.get("confidence"), Mapping) else None,
    )
    manual_review = None
    if isinstance(score_evidence.get("review"), Mapping):
        manual_review = score_evidence["review"].get("recommended")

    sections: list[tuple[str, list[tuple[str, Any]]]] = [
        (
            "Summary",
            [
                ("Submission ID", submission_id),
                ("Overall Score", _format_score(_first_non_empty(summary.get("overall"), scores.get("overall")))),
                ("Grade", summary.get("grade")),
                ("Confidence", confidence),
                ("Manual Review Recommended", _format_bool(manual_review)),
                ("Findings", len(report.get("findings", []) or [])),
            ],
        ),
        (
            "Metadata",
            [
                ("Attempt Number", meta.get("attempt_number")),
                ("Active Attempt", _format_bool(meta.get("is_active"))),
                ("Submission Source", meta.get("source_type")),
                ("Student ID", meta.get("student_id")),
                ("Assignment ID", meta.get("assignment_id")),
                ("Original Filename", meta.get("original_filename")),
                ("Submitted At", meta.get("timestamp")),
            ],
        ),
    ]

    component_rows = [
        (str(name).upper(), _format_score(value.get("score") if isinstance(value, Mapping) else value))
        for name, value in components.items()
    ]
    if component_rows:
        sections.append(("Component Scores", component_rows))

    return build_key_value_pdf("Submission Report", sections)


def build_key_value_pdf(title: str, sections: Sequence[tuple[str, Sequence[tuple[str, Any]]]]) -> bytes:
    """Generates a PDF containing distinct sections of key-value pairs."""
    lines: list[_PdfLine] = [
        _PdfLine(_normalize_text(title), size=16, bold=True),
        _PdfLine(""),
    ]
    for heading, items in sections:
        filtered = [(label, value) for label, value in items if value not in (None, "", "N/A")]
        if not filtered:
            continue
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine(_normalize_text(heading), size=12, bold=True, spacing_before=4))
        for label, value in filtered:
            lines.extend(_format_field(label, _stringify_value(value)))
        lines.append(_PdfLine(""))
    return _build_pdf(lines)


def build_records_pdf(title: str, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]], record_label: str = "Entry") -> bytes:
    """Generates a PDF to display multiple records, such as an analytics export."""
    lines: list[_PdfLine] = [
        _PdfLine(_normalize_text(title), size=16, bold=True),
        _PdfLine(f"Total entries: {len(rows)}", size=10),
        _PdfLine(""),
    ]

    if not rows:
        lines.append(_PdfLine("No matching records were available for export."))
        return _build_pdf(lines)

    for index, row in enumerate(rows, start=1):
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine(
            f"{record_label} {index}",
            size=12,
            bold=True,
            spacing_before=4,
        ))
        for field in fieldnames:
            value = row.get(field, "") if isinstance(row, Mapping) else ""
            lines.extend(_format_field(field, _stringify_value(value)))
        lines.append(_PdfLine(""))

    return _build_pdf(lines)


def _format_field(label: str, value: str) -> list[_PdfLine]:
    """Formats a single field, ensuring lengthy values are handled gracefully."""
    label_text = _normalize_text(label)
    value_text = _normalize_text(value)

    combined = f"{label_text}: {value_text}"
    max_chars = _max_line_chars(10, FIELD_INDENT)

    if len(combined) <= max_chars:
        return [_PdfLine(combined, size=10, indent=FIELD_INDENT, spacing_before=3)]

    items = [item.strip() for item in value_text.split(";") if item.strip()]
    if len(items) > 1:
        result: list[_PdfLine] = [
            _PdfLine(f"{label_text}:", size=10, bold=True, indent=FIELD_INDENT, spacing_before=3),
        ]
        sub_indent = FIELD_INDENT + 12
        sub_max = _max_line_chars(10, sub_indent)
        for item in items:
            for wrapped in textwrap.wrap(item, width=sub_max, break_long_words=True, break_on_hyphens=False):
                result.append(_PdfLine(f"- {wrapped}" if wrapped == item[:len(wrapped)] else f"  {wrapped}", size=10, indent=sub_indent))
        return result

    wrapped = textwrap.wrap(
        combined,
        width=max_chars,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not wrapped:
        wrapped = [""]
    result_lines: list[_PdfLine] = []
    for i, line in enumerate(wrapped):
        result_lines.append(_PdfLine(
            line,
            size=10,
            indent=FIELD_INDENT if i == 0 else FIELD_INDENT + 12,
            spacing_before=3 if i == 0 else 0,
        ))
    return result_lines


def _build_pdf(lines: Sequence[_PdfLine]) -> bytes:
    """Converts a flat list of text lines into raw PDF page streams."""
    pages: list[list[str]] = []
    current_page: list[str] = []
    y = PAGE_HEIGHT - TOP_MARGIN

    for line in lines:
        line_height = max(int(line.size * 1.5), 13)
        total_height = line_height + line.spacing_before

        if line.separator:
            total_height = 10

        if y - total_height < BOTTOM_MARGIN:
            pages.append(current_page)
            current_page = []
            y = PAGE_HEIGHT - TOP_MARGIN

        y -= line.spacing_before

        if line.separator:
            rule_y = y - 3
            x_start = LEFT_MARGIN
            x_end = PAGE_WIDTH - RIGHT_MARGIN
            current_page.append(
                f"0.78 0.78 0.78 RG 0.5 w {x_start} {rule_y} m {x_end} {rule_y} l S 0 0 0 RG"
            )
            y -= 10
            continue

        if line.text:
            x = LEFT_MARGIN + line.indent
            font_name = "F2" if line.bold else "F1"
            escaped = _escape_pdf_text(line.text)
            current_page.append(
                f"BT /{font_name} {line.size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET"
            )
        y -= line_height

    if not pages or current_page:
        pages.append(current_page)

    return _assemble_pdf(pages)


def _assemble_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Assembles the PDF document structure by linking the page streams."""
    page_count = max(len(pages), 1)
    page_ids = [5 + (index * 2) for index in range(page_count)]
    content_ids = [page_id + 1 for page_id in page_ids]
    max_id = content_ids[-1]

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] /Count {page_count} >>".encode("latin-1"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    }

    for page_id, content_id, commands in zip(page_ids, content_ids, pages):
        stream = "\n".join(commands).encode("latin-1")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\n".encode("latin-1") + b"stream\n" + stream + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("latin-1")

    chunks: list[bytes] = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = {0: 0}
    cursor = len(chunks[0])

    for object_id in range(1, max_id + 1):
        body = objects[object_id]
        header = f"{object_id} 0 obj\n".encode("latin-1")
        footer = b"\nendobj\n"
        offsets[object_id] = cursor
        chunks.extend([header, body, footer])
        cursor += len(header) + len(body) + len(footer)

    xref_offset = cursor
    chunks.append(f"xref\n0 {max_id + 1}\n".encode("latin-1"))
    chunks.append(b"0000000000 65535 f \n")
    for object_id in range(1, max_id + 1):
        chunks.append(f"{offsets[object_id]:010d} 00000 n \n".encode("latin-1"))
    chunks.append(f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("latin-1"))
    return b"".join(chunks)


def _wrap_line(text: str, size: int, indent: int = 0) -> list[_PdfLine]:
    """Wraps text into multiple lines ensuring it does not overflow the page boundaries."""
    normalized = _normalize_text(text)
    max_chars = _max_line_chars(size, indent)
    wrapped = textwrap.wrap(
        normalized,
        width=max_chars,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
    )
    if not wrapped:
        wrapped = [""]
    return [_PdfLine(line, size=size, indent=indent) for line in wrapped]


def _stringify_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "; ".join(_stringify_value(item) for item in value)
    return _normalize_text(value)


def _format_score(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0 <= float(value) <= 1:
            return f"{float(value) * 100:.2f}%"
        return f"{float(value):.2f}"
    if value in (None, ""):
        return None
    return _normalize_text(value)


def _format_bool(value: Any) -> str | None:
    if value is None:
        return None
    return "yes" if bool(value) else "no"


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _normalize_text(value: Any) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part for part in text.splitlines() if part.strip()) or text.replace("\n", " ")
    text = text.replace("\t", " ")
    text = " ".join(text.split())
    return text.encode("latin-1", "replace").decode("latin-1")


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_rich_submission_pdf(report: Any) -> bytes:
    """Produce a comprehensive assessment PDF from an ExportReport instance."""
    lines: list[_PdfLine] = []

    # Title block.
    lines.append(_PdfLine(_normalize_text("Submission Assessment Report"), size=16, bold=True))
    lines.append(_PdfLine(_normalize_text(f"Generated: {report.generated_at}"), size=9))
    if report.run_id:
        lines.append(_PdfLine(_normalize_text(f"Run ID: {report.run_id}"), size=9))
    lines.append(_PdfLine(""))

    # Section 1: Submission Details.
    lines.append(_PdfLine("", separator=True))
    lines.append(_PdfLine("Submission Details", size=13, bold=True, spacing_before=4))
    for label, val in [
        ("Student ID", report.student_id),
        ("Assignment ID", report.assignment_id),
        ("Original Filename", report.original_filename),
        ("Submitted At", report.submitted_at),
        ("Profile", report.profile),
        ("Scoring Mode", report.scoring_mode),
        ("Pipeline Version", report.pipeline_version),
    ]:
        if val:
            lines.extend(_format_field(label, _normalize_text(str(val))))
    lines.append(_PdfLine(""))

    # Section 2: Overall Result.
    lines.append(_PdfLine("", separator=True))
    lines.append(_PdfLine("Overall Result", size=13, bold=True, spacing_before=4))
    score_display = f"{report.overall_pct}  —  {report.overall_label}"
    lines.append(_PdfLine(_normalize_text(score_display), size=12, bold=True, indent=16, spacing_before=4))
    if report.confidence_level:
        lines.extend(_format_field("Confidence", report.confidence_level))
    for reason in report.confidence_reasons[:4]:
        lines.append(_PdfLine(_normalize_text(f"  - {reason}"), size=9, indent=24))
    manual_str = "Yes" if report.manual_review else "No"
    lines.extend(_format_field("Manual Review Recommended", manual_str))
    for reason in (report.manual_review_reasons[:3] if report.manual_review else []):
        lines.append(_PdfLine(_normalize_text(f"  - {reason}"), size=9, indent=24))
    lines.append(_PdfLine(""))

    # Section 3: Component Scores.
    if report.components:
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine("Component Scores", size=13, bold=True, spacing_before=4))
        for comp in report.components:
            req_note = "" if comp.required else "  (not required for this profile)"
            comp_label = _normalize_text(comp.name.upper())
            comp_score = _normalize_text(comp.score_pct + req_note)
            lines.extend(_format_field(comp_label, comp_score))
            if comp.required and any([comp.met, comp.partial, comp.failed, comp.skipped]):
                counts = f"Met: {comp.met}  Partial: {comp.partial}  Failed: {comp.failed}  Skipped: {comp.skipped}  (weight: {comp.weight:.2f})"
                lines.append(_PdfLine(_normalize_text(counts), size=9, indent=32, spacing_before=2))
        lines.append(_PdfLine(""))

    # Section 4: Key Findings.
    # Only show FAIL, WARN, THREAT findings (not INFO/SKIPPED) to keep PDF concise
    key_findings = [f for f in report.findings if f.severity in ("FAIL", "WARN", "THREAT")][:25]
    if key_findings:
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine("Key Findings", size=13, bold=True, spacing_before=4))
        current_comp = None
        for finding in key_findings:
            if finding.component != current_comp:
                current_comp = finding.component
                lines.append(_PdfLine(_normalize_text(current_comp.upper()), size=10, bold=True, indent=8, spacing_before=6))
            label = f"[{finding.severity}] {finding.finding_id}"
            lines.append(_PdfLine(_normalize_text(label), size=9, bold=True, indent=16, spacing_before=3))
            for wrapped_line in _wrap_line(finding.message, size=9, indent=24):
                lines.append(wrapped_line)
        lines.append(_PdfLine(""))

    # Section 5: Rule Outcomes.
    # Group by component; skip all-skipped rules to reduce noise
    non_skipped_outcomes = [r for r in report.rule_outcomes
                            if r.status not in ("SKIPPED_BY_PROFILE",)]
    if non_skipped_outcomes:
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine("Rule Outcomes", size=13, bold=True, spacing_before=4))
        # Group by component
        by_comp: dict[str, list] = {}
        for outcome in non_skipped_outcomes[:40]:  # Cap at 40 for PDF length
            by_comp.setdefault(outcome.component, []).append(outcome)
        for comp_name, outcomes in sorted(by_comp.items()):
            lines.append(_PdfLine(_normalize_text(comp_name.upper()), size=10, bold=True, indent=8, spacing_before=6))
            for outcome in outcomes:
                score_display = outcome.score_pct if hasattr(outcome, 'score_pct') else str(outcome.score)
                # Score_pct may not exist on RuleOutcome - compute inline
                if isinstance(outcome.score, (int, float)):
                    score_display = f"{float(outcome.score) * 100:.2f}%"
                else:
                    score_display = str(outcome.score)
                status_line = f"{outcome.requirement_id}  [{outcome.status}]  Score: {score_display}"
                lines.append(_PdfLine(_normalize_text(status_line), size=9, bold=True, indent=16, spacing_before=4))
                stage_info = f"Stage: {outcome.stage}  |  Weight: {outcome.weight:.2f}"
                lines.append(_PdfLine(_normalize_text(stage_info), size=8, indent=24))
                if outcome.description:
                    for dl in _wrap_line(outcome.description, size=9, indent=24):
                        lines.append(dl)
                if outcome.score_label:
                    label_text = f"Rationale: {outcome.score_label}"
                    for ll in _wrap_line(label_text, size=9, indent=24):
                        lines.append(ll)
        lines.append(_PdfLine(""))

    # Section 6: Execution Summary.
    lines.append(_PdfLine("", separator=True))
    lines.append(_PdfLine("Execution Summary", size=13, bold=True, spacing_before=4))
    php_str = "available" if report.execution.php_available else "unavailable"
    browser_str = "available" if report.execution.browser_available else "unavailable"
    lines.extend(_format_field("PHP Runtime", php_str))
    lines.extend(_format_field("Browser Runtime", browser_str))
    beh_count = len(report.execution.behavioural_results)
    brow_count = len(report.execution.browser_results)
    lines.extend(_format_field("Behavioural Tests", f"{beh_count} run" if report.execution.behavioural_tests_run else "not run"))
    lines.extend(_format_field("Browser Tests", f"{brow_count} run" if report.execution.browser_tests_run else "not run"))
    for result in report.execution.behavioural_results[:8]:
        entry = f"{result.get('test_id', '')}: {result.get('status', '')}"
        if result.get("diagnostic"):
            entry += f" — {result['diagnostic'][:80]}"
        lines.append(_PdfLine(_normalize_text(entry), size=8, indent=24, spacing_before=2))
    for result in report.execution.browser_results[:8]:
        entry = f"{result.get('test_id', '')}: {result.get('status', '')}"
        if result.get("diagnostic"):
            entry += f" — {result['diagnostic'][:80]}"
        lines.append(_PdfLine(_normalize_text(entry), size=8, indent=24, spacing_before=2))
    lines.append(_PdfLine(""))

    # Section 7: Marking Policy Notes.
    if report.policy_notes:
        lines.append(_PdfLine("", separator=True))
        lines.append(_PdfLine("Marking Policy Notes", size=13, bold=True, spacing_before=4))
        for note in report.policy_notes:
            for nl in _wrap_line(f"- {note}", size=9, indent=8):
                lines.append(nl)
        lines.append(_PdfLine(""))

    return _build_pdf(lines)
