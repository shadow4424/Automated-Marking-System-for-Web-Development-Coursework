"""Teacher analytics helper utilities shared by assignment management routes."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any, Mapping, Sequence

from flask import Response, current_app

from ams.core.llm_factory import get_llm_provider
from ams.llm.utils import clean_json_response
from ams.pdf_exports import build_records_pdf

logger = logging.getLogger(__name__)

TEACHING_INSIGHT_PRIORITIES = {"high", "medium", "low"}
TEACHING_INSIGHT_TYPES = {"pattern", "strength", "weakness", "anomaly", "cause", "recommendation", "trend"}
RELIABILITY_EVIDENCE_KEYS = {
    "manual_review",
    "fully_evaluated",
    "partially_evaluated",
    "not_analysable",
    "confidence_mix",
    "limitation_incidents",
    "major_limitations",
    "runtime_skip_count",
    "browser_skip_count",
    "runtime_failure_count",
    "browser_failure_count",
}
PERCENTLIKE_PATH_HINTS = ("percent", "score", "average", "median", "mean", "min", "max", "gap")
COUNTLIKE_PATH_HINTS = (
    "count",
    "students",
    "submissions",
    "incident",
    "rule_count",
    "rules_affected",
    "active_in_scope",
    "assigned_students",
    "missing_assigned",
    "manual_review",
    "fully_evaluated",
    "partially_evaluated",
    "not_analysable",
    "runtime_skip_count",
    "browser_skip_count",
    "runtime_failure_count",
    "browser_failure_count",
    "categories",
    "evaluable",
)
NUMERIC_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?%?(?!\w)")
STRUCTURAL_RANGE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?%?(?!\w)")
STRUCTURAL_RANGE_HINTS = (
    "band",
    "interval",
    "bucket",
    "label",
    "range",
    "mark band",
    "score band",
    "grade band",
)
SEMANTIC_CAUSE_MARKERS = ("because", "due to", "caused by", "as a result", "likely because", "driven by")
SEMANTIC_CLAIM_EVIDENCE_KEYS = {
    "strongest_requirement",
    "weakest_requirement",
    "requirement_coverage_summary",
    "component_performance_summary",
    "top_failing_rule",
    "top_failing_rules",
    "major_rule_categories",
    "major_limitations",
    "static_vs_behavioural_mismatch",
    "high_priority_flagged_submissions",
    "confidence_mix",
    "manual_review",
}


# Build a CSV download response.
def _csv_response(filename: str, fieldnames: list[str], rows: list[dict]) -> Response:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Build a JSON download response.
def _json_response(filename: str, rows: list[dict]) -> Response:
    return Response(
        json.dumps(rows, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Build a plain-text download response.
def _txt_response(filename: str, title: str, fieldnames: list[str], rows: list[dict]) -> Response:
    lines = []
    lines.append("=" * 70)
    lines.append(title.upper())
    lines.append("=" * 70)
    lines.append("")

    for i, row in enumerate(rows, 1):
        lines.append(f"--- Entry {i} ---")
        for field in fieldnames:
            val = row.get(field, "")
            lines.append(f"  {field}: {val}")
        lines.append("")

    lines.append("=" * 70)
    lines.append(f"Total entries: {len(rows)}")
    return Response(
        "\n".join(lines),
        mimetype="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Build a PDF download response.
def _pdf_response(filename: str, title: str, fieldnames: list[str], rows: list[dict]) -> Response:
    """Generate a PDF report as a direct file download."""
    pdf = build_records_pdf(title, fieldnames, rows, record_label="Row")
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Filter top failing rule rows for export or display.
def _filtered_top_rule_rows(analytics: Mapping[str, object], args) -> list[dict]:
    rows = list(analytics.get("top_failing_rules", []) or [])
    severity = str(args.get("severity", "")).strip().upper()
    component = str(args.get("component", "")).strip().lower()
    impact_type = str(args.get("impact_type", "")).strip().lower()
    return [
        row
        for row in rows
        if (not severity or str(row.get("severity", "")).upper() == severity)
        and (not component or str(row.get("component", "")).lower() == component)
        and (not impact_type or str(row.get("impact_type", "")).lower() == impact_type)
    ]


# Normalise teaching insights into a consistent structure.
def _normalize_teaching_insights(insights: Sequence[object] | None) -> list[dict]:
    normalized: list[dict] = []
    for index, insight in enumerate(list(insights or []), start=1):
        if isinstance(insight, Mapping):
            insight_type = str(insight.get("type") or insight.get("insight_type") or f"insight_{index}")
            priority = str(insight.get("priority") or "medium").strip().lower()
            if priority not in TEACHING_INSIGHT_PRIORITIES:
                priority = "medium"
            evidence_keys = [
                str(key)
                for key in list(insight.get("evidence_keys", insight.get("supporting_metric_keys", [])) or [])
                if str(key).strip()
            ]
            normalized.append(
                {
                    "insight_type": insight_type,
                    "type": insight_type,
                    "priority": priority,
                    "title": str(insight.get("title") or "").strip(),
                    "text": str(insight.get("text") or "").strip(),
                    "supporting_metric_keys": evidence_keys,
                    "evidence_keys": evidence_keys,
                }
            )
            continue
        text = str(insight or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "insight_type": f"insight_{index}",
                "type": f"insight_{index}",
                "priority": "medium",
                "title": "",
                "text": text,
                "supporting_metric_keys": [],
                "evidence_keys": [],
            }
        )
    return normalized


# Build a structured validation failure payload.
def _validation_failure(
    category: str,
    message: str,
    *,
    field: str | None = None,
    value: object = None,
) -> dict[str, Any]:
    failure: dict[str, Any] = {
        "category": str(category or "schema_error"),
        "message": str(message or "Validation failed."),
    }
    if field:
        failure["field"] = field
    if value is not None:
        if isinstance(value, (str, int, float, bool)) or value is None:
            failure["value"] = value
        else:
            failure["value"] = str(value)[:200]
    return failure


# Walk nested context data and yield numeric values with paths.
def _iter_numeric_context_values(value: object, path: str = ""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_numeric_context_values(child, child_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _iter_numeric_context_values(child, child_path)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield path, float(value)


# Decide whether a numeric path should be treated like a percentage.
def _is_percentlike_numeric_path(path: str, value: float) -> bool:
    path_lower = str(path or "").lower()
    if any(token in path_lower for token in PERCENTLIKE_PATH_HINTS):
        return True
    if any(token in path_lower for token in COUNTLIKE_PATH_HINTS):
        return False
    return not float(value).is_integer()


# Build the numeric grounding set used for claim validation.
def _build_numeric_grounding(context: Mapping[str, object]) -> dict[str, Any]:
    active_in_scope = int(context.get("active_in_scope") or 0)
    assigned_students = int(context.get("assigned_students") or 0)
    denominators = [value for value in {active_in_scope, assigned_students} if value > 0]
    exact_counts: set[int] = set()
    percentlike_values: list[float] = []

    for path, numeric_value in _iter_numeric_context_values(context):
        if _is_percentlike_numeric_path(path, numeric_value):
            percentlike_values.append(float(numeric_value))
            continue
        if float(numeric_value).is_integer():
            exact_count = int(numeric_value)
            exact_counts.add(exact_count)
            for denominator in denominators:
                if 0 <= exact_count <= denominator:
                    percentlike_values.append((exact_count / denominator) * 100.0)
        else:
            percentlike_values.append(float(numeric_value))

    return {
        "counts": exact_counts,
        "percentlike_values": percentlike_values,
    }


# Extract numeric mentions from generated text.
def _extract_numeric_mentions(text: str) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    source = str(text or "")
    lowered = source.lower()
    structural_range_spans: list[tuple[int, int]] = []
    for range_match in STRUCTURAL_RANGE_RE.finditer(source):
        start, end = range_match.span()
        window = lowered[max(0, start - 20) : min(len(lowered), end + 20)]
        local_context = source[max(0, start - 4) : min(len(source), end + 8)]
        if any(token in window for token in STRUCTURAL_RANGE_HINTS) or "'" in local_context or '"' in local_context:
            structural_range_spans.append((start, end))
    for match in NUMERIC_TOKEN_RE.finditer(source):
        start, end = match.span()
        if any(span_start <= start and end <= span_end for span_start, span_end in structural_range_spans):
            continue
        raw = match.group(0)
        bare = raw[:-1] if raw.endswith("%") else raw
        try:
            value = float(bare)
        except ValueError:
            continue
        window = lowered[max(0, start - 18) : min(len(lowered), end + 24)]
        kind = "count"
        if raw.endswith("%") or "." in bare or any(
            token in window for token in ("percent", "percentage", "score", "mark", "average", "median", "mean")
        ):
            kind = "percent"
        mentions.append(
            {
                "raw": raw,
                "value": value,
                "kind": kind,
                "start": start,
                "end": end,
                "window": window,
            }
        )
    return mentions


# Choose the tolerance used for numeric grounding checks.
def _numeric_tolerance(raw: str, kind: str) -> float:
    if kind == "count":
        return 0.0
    bare = str(raw or "").rstrip("%")
    if "." not in bare:
        return 0.51
    decimals = len(bare.split(".", 1)[1])
    if decimals == 1:
        return 0.11
    return 0.02


# Validate numeric claims against the available context.
def _validate_numeric_grounding(text: str, context: Mapping[str, object], field: str) -> dict[str, Any] | None:
    grounding = _build_numeric_grounding(context)
    allowed_counts = grounding["counts"]
    allowed_percentlike = grounding["percentlike_values"]
    for mention in _extract_numeric_mentions(text):
        if mention["kind"] == "count":
            if not float(mention["value"]).is_integer() or int(mention["value"]) not in allowed_counts:
                return _validation_failure(
                    "numeric_mismatch",
                    f"Numeric claim '{mention['raw']}' is not grounded in the analytics payload.",
                    field=field,
                    value=mention["raw"],
                )
            continue
        tolerance = _numeric_tolerance(mention["raw"], mention["kind"])
        if not any(abs(float(mention["value"]) - float(candidate)) <= tolerance for candidate in allowed_percentlike):
            return _validation_failure(
                "numeric_mismatch",
                f"Numeric claim '{mention['raw']}' is not a supported rounded value from the analytics payload.",
                field=field,
                value=mention["raw"],
            )
    return None


# Check whether a value supports a majority claim.
def is_majority(value: object, active_in_scope: int) -> bool:
    try:
        return float(value or 0) > (active_in_scope / 2)
    except (TypeError, ValueError):
        return False


# Check whether the evidence keys support a majority claim.
def _supports_majority_claim(evidence_keys: Sequence[str], context: Mapping[str, object]) -> bool:
    active_in_scope = int(context.get("active_in_scope") or 0)
    if active_in_scope <= 0:
        return False

    evidence = set(str(key) for key in evidence_keys)
    if "dominant_score_band" in evidence:
        dominant = dict(context.get("dominant_score_band", {}) or {})
        if is_majority(dominant.get("count"), active_in_scope):
            return True
    if "score_band_distribution" in evidence:
        for band in list(context.get("score_band_distribution", []) or []):
            if is_majority((band or {}).get("count"), active_in_scope):
                return True
    if "confidence_mix" in evidence:
        mix = dict(context.get("confidence_mix", {}) or {})
        for level in ("high", "medium", "low"):
            if is_majority((mix.get(level) or {}).get("count"), active_in_scope):
                return True
    if "manual_review" in evidence and is_majority(context.get("manual_review"), active_in_scope):
        return True
    if "top_failing_rule" in evidence:
        top_rule = dict(context.get("top_failing_rule", {}) or {})
        if is_majority(top_rule.get("submissions_affected"), active_in_scope):
            return True
    if "top_failing_rules" in evidence:
        for item in list(context.get("top_failing_rules", []) or []):
            if is_majority((item or {}).get("submissions_affected"), active_in_scope):
                return True
    if "major_limitations" in evidence:
        for item in list(context.get("major_limitations", []) or []):
            if is_majority((item or {}).get("incident_count"), active_in_scope):
                return True
    if "requirement_coverage_summary" in evidence:
        for row in list(context.get("requirement_coverage_summary", []) or []):
            row = dict(row or {})
            if any(
                is_majority(row.get(key), active_in_scope)
                for key in ("met_count", "partial_count", "unmet_count", "not_evaluable_count")
            ):
                return True
    if "high_priority_flagged_submissions" in evidence:
        flagged = dict(context.get("high_priority_flagged_submissions", {}) or {})
        if any(
            is_majority(flagged.get(key), active_in_scope)
            for key in ("count", "medium_or_higher_count", "low_confidence_count", "manual_review_count")
        ):
            return True
    return False


# Validate confidence-scope claims in generated text.
def _validate_confidence_scope_claim(
    text: str,
    context: Mapping[str, object],
    *,
    field: str = "text",
) -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    mix = dict(context.get("confidence_mix", {}) or {})
    active_in_scope = int(context.get("active_in_scope") or 0)
    for level in ("low", "medium", "high"):
        if f"{level} confidence" not in lowered:
            continue
        level_data = dict(mix.get(level, {}) or {})
        expected_count = int(level_data.get("count", 0) or 0)
        expected_percent = float(level_data.get("percent", 0) or 0.0)
        majority_match = re.search(
            rf"(most students|most submissions|majority)[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence",
            lowered,
        )
        if majority_match and active_in_scope and expected_count <= (active_in_scope / 2):
            return _validation_failure(
                "unsupported_claim",
                f"{level.title()} confidence is described as a majority without supporting cohort evidence.",
                field=field,
                value=text,
            )
        fraction_match = re.search(
            rf"(\d+(?:\.\d+)?)\s+out of\s+(\d+)[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence",
            lowered,
        )
        if fraction_match:
            claimed_count = int(float(fraction_match.group(1)))
            claimed_total = int(float(fraction_match.group(2)))
            if claimed_count != expected_count or (active_in_scope and claimed_total != active_in_scope):
                return _validation_failure(
                    "unsupported_claim",
                    f"{level.title()} confidence scope claim is not supported by confidence mix data.",
                    field=field,
                    value=text,
                )
        percent_match = re.search(
            rf"(\d+(?:\.\d+)?)%[^.]*{level}(?:\s+\w+){{0,3}}\s+confidence",
            lowered,
        )
        if percent_match:
            claimed_percent = float(percent_match.group(1))
            if abs(claimed_percent - expected_percent) > _numeric_tolerance(percent_match.group(1) + "%", "percent"):
                return _validation_failure(
                    "unsupported_claim",
                    f"{level.title()} confidence percentage claim is not supported by confidence mix data.",
                    field=field,
                    value=text,
                )
    return None


# Validate manual-review claims in generated text.
def _validate_manual_review_claim(
    text: str,
    context: Mapping[str, object],
    *,
    field: str = "text",
) -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    if "manual review" not in lowered:
        return None
    expected_count = int(context.get("manual_review", 0) or 0)
    active_in_scope = int(context.get("active_in_scope", 0) or 0)
    direct_match = re.search(r"manual review(?: is)? recommended(?: for)?\s+(\d+(?:\.\d+)?)", lowered)
    if direct_match:
        claimed_count = int(float(direct_match.group(1)))
        if claimed_count != expected_count:
            return _validation_failure(
                "unsupported_claim",
                "Manual review scope claim is not supported by the analytics payload.",
                field=field,
                value=text,
            )
    fraction_match = re.search(r"(\d+(?:\.\d+)?)\s+out of\s+(\d+)[^.]*manual review", lowered)
    if fraction_match:
        claimed_count = int(float(fraction_match.group(1)))
        claimed_total = int(float(fraction_match.group(2)))
        if claimed_count != expected_count or (active_in_scope and claimed_total != active_in_scope):
            return _validation_failure(
                "unsupported_claim",
                "Manual review scope claim is not supported by the analytics payload.",
                field=field,
                value=text,
            )
    return None


# Validate higher-level semantic claims in generated text.
def _validate_semantic_claims(
    text: str,
    evidence_keys: Sequence[str],
    context: Mapping[str, object],
    *,
    field: str = "text",
) -> dict[str, Any] | None:
    lowered = str(text or "").lower()
    evidence = {str(key).strip() for key in evidence_keys if str(key).strip()}
    if any(marker in lowered for marker in ("most students", "most submissions", "majority", "dominant")):
        if not _supports_majority_claim(list(evidence), context):
            return _validation_failure(
                "unsupported_claim",
                "Majority or dominant language is not supported by the provided analytics.",
                field=field,
                value=text,
            )
    if "most common" in lowered and not evidence.intersection({"top_failing_rule", "top_failing_rules", "major_rule_categories"}):
        return _validation_failure(
            "unsupported_claim",
            "Most-common language requires rule-level evidence keys.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "strongest" in lowered and not evidence.intersection(
        {"strongest_requirement", "component_performance_summary", "requirement_coverage_summary"}
    ):
        return _validation_failure(
            "unsupported_claim",
            "Strongest-area language requires requirement or component evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "weakest" in lowered and not evidence.intersection(
        {"weakest_requirement", "component_performance_summary", "requirement_coverage_summary"}
    ):
        return _validation_failure(
            "unsupported_claim",
            "Weakest-area language requires requirement or component evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "manual review" in lowered and not evidence.intersection(
        {"manual_review", "high_priority_flagged_submissions", "major_limitations"}
    ):
        return _validation_failure(
            "unsupported_claim",
            "Manual-review language requires review or limitation evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if "confidence" in lowered and not evidence.intersection(
        {"confidence_mix", "high_priority_flagged_submissions", "major_limitations", "manual_review"}
    ):
        return _validation_failure(
            "unsupported_claim",
            "Confidence language requires confidence or limitation evidence.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    if any(marker in lowered for marker in SEMANTIC_CAUSE_MARKERS) and not evidence.intersection(
        SEMANTIC_CLAIM_EVIDENCE_KEYS
    ):
        return _validation_failure(
            "unsupported_claim",
            "Causal language requires stronger supporting evidence keys.",
            field="evidence_keys",
            value=list(evidence_keys),
        )
    confidence_scope_error = _validate_confidence_scope_claim(text, context, field=field)
    if confidence_scope_error is not None:
        return confidence_scope_error
    manual_review_error = _validate_manual_review_claim(text, context, field=field)
    if manual_review_error is not None:
        return manual_review_error
    return None


# Build a deterministic teaching-summary fallback.
def _user_facing_teaching_summary_fallback(
    reason: Mapping[str, object] | None,
    *,
    validation_rejected: bool,
) -> str:
    if not validation_rejected:
        return "LLM summary was unavailable. Deterministic wording remains in place."
    code = str((reason or {}).get("category") or "").strip().lower()
    label_map = {
        "invalid_json": "invalid JSON response",
        "schema_error": "schema validation failed",
        "missing_required_fields": "required fields were missing",
        "invalid_priority": "priority validation failed",
        "invalid_type": "insight type validation failed",
        "unsupported_evidence_key": "unsupported evidence key detected",
        "numeric_mismatch": "numeric validation failed",
        "unsupported_claim": "unsupported claim detected",
        "too_many_insights": "too many insights were returned",
        "too_few_insights": "too few insights were returned",
    }
    label = label_map.get(code, "validation failed")
    return (
        "LLM summary was generated but rejected during validation; "
        f"deterministic wording is shown instead ({label})."
    )


# Validate the enhanced teaching summary payload.
def _validate_enhanced_teaching_summary(
    candidate: object,
    context: Mapping[str, object],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(candidate, Mapping):
        return None, _validation_failure("schema_error", "LLM summary root must be a JSON object.", field="summary")
    if str(candidate.get("summary_mode") or "").strip() != "llm_teacher_insight":
        return None, _validation_failure(
            "missing_required_fields",
            "LLM summary must include summary_mode='llm_teacher_insight'.",
            field="summary_mode",
            value=candidate.get("summary_mode"),
        )
    headline = str(candidate.get("headline") or "").strip()
    if not headline or len(headline) < 24 or len(headline) > 240:
        return None, _validation_failure(
            "missing_required_fields" if not headline else "schema_error",
            "Headline is missing or outside the supported length.",
            field="headline",
            value=headline,
        )
    raw_insights = candidate.get("insights")
    if not isinstance(raw_insights, list):
        return None, _validation_failure("schema_error", "Insights must be returned as a JSON array.", field="insights")
    if len(raw_insights) < 4:
        return None, _validation_failure(
            "too_few_insights",
            "LLM summary returned fewer than 4 insights.",
            field="insights",
            value=len(raw_insights),
        )
    if len(raw_insights) > 6:
        return None, _validation_failure(
            "too_many_insights",
            "LLM summary returned more than 6 insights.",
            field="insights",
            value=len(raw_insights),
        )

    allowed_evidence_keys = {str(key).strip() for key in context.keys() if str(key).strip()}
    headline_numeric_error = _validate_numeric_grounding(headline, context, "headline")
    if headline_numeric_error is not None:
        return None, headline_numeric_error

    has_strength_or_pattern = False
    has_weakness = False
    has_recommendation = False
    has_reliability_interpretation = False
    reliability_issues_present = any(
        bool(context.get(key))
        for key in (
            "manual_review",
            "partially_evaluated",
            "not_analysable",
            "limitation_incidents",
            "runtime_skip_count",
            "browser_skip_count",
            "runtime_failure_count",
            "browser_failure_count",
        )
    )
    validated: list[dict] = []
    seen_titles: set[str] = set()
    for index, generated in enumerate(raw_insights, start=1):
        if not isinstance(generated, Mapping):
            return None, _validation_failure(
                "schema_error",
                "Each insight must be a JSON object.",
                field=f"insights[{index}]",
            )
        priority = str(generated.get("priority") or "").strip().lower()
        insight_type = str(generated.get("type") or generated.get("insight_type") or "").strip().lower()
        title = str(generated.get("title") or "").strip()
        text = str(generated.get("text") or "").strip()
        evidence_keys = [
            str(key).strip()
            for key in list(generated.get("evidence_keys", generated.get("supporting_metric_keys", [])) or [])
            if str(key).strip()
        ]
        if priority not in TEACHING_INSIGHT_PRIORITIES:
            return None, _validation_failure(
                "invalid_priority",
                "Insight priority is not one of high, medium, or low.",
                field=f"insights[{index}].priority",
                value=priority,
            )
        if insight_type not in TEACHING_INSIGHT_TYPES:
            return None, _validation_failure(
                "invalid_type",
                "Insight type is not part of the supported teacher-insight taxonomy.",
                field=f"insights[{index}].type",
                value=insight_type,
            )
        if not title or len(title) > 90:
            return None, _validation_failure(
                "missing_required_fields" if not title else "schema_error",
                "Insight title is missing or too long.",
                field=f"insights[{index}].title",
                value=title,
            )
        if title.lower() in seen_titles:
            return None, _validation_failure(
                "schema_error",
                "Insight titles must be unique within the summary.",
                field=f"insights[{index}].title",
                value=title,
            )
        seen_titles.add(title.lower())
        if not text or len(text) < 40 or len(text) > 420:
            return None, _validation_failure(
                "missing_required_fields" if not text else "schema_error",
                "Insight text is missing or outside the supported length.",
                field=f"insights[{index}].text",
                value=text,
            )
        if not evidence_keys:
            return None, _validation_failure(
                "missing_required_fields",
                "Each insight must include at least one evidence key.",
                field=f"insights[{index}].evidence_keys",
            )
        unsupported_key = next((key for key in evidence_keys if key not in allowed_evidence_keys), None)
        if unsupported_key is not None:
            return None, _validation_failure(
                "unsupported_evidence_key",
                "Insight references an evidence key that is not present in the analytics payload.",
                field=f"insights[{index}].evidence_keys",
                value=unsupported_key,
            )
        semantic_error = _validate_semantic_claims(text, evidence_keys, context, field=f"insights[{index}].text")
        if semantic_error is not None:
            return None, semantic_error
        numeric_error = _validate_numeric_grounding(text, context, f"insights[{index}].text")
        if numeric_error is not None:
            return None, numeric_error
        if insight_type in {"strength", "pattern"}:
            has_strength_or_pattern = True
        if insight_type == "weakness":
            has_weakness = True
        if insight_type == "recommendation":
            has_recommendation = True
        if set(evidence_keys).intersection(RELIABILITY_EVIDENCE_KEYS):
            has_reliability_interpretation = True
        validated.append(
            {
                "insight_type": insight_type,
                "type": insight_type,
                "priority": priority,
                "title": title,
                "text": text,
                "supporting_metric_keys": evidence_keys,
                "evidence_keys": evidence_keys,
            }
        )
    if not has_strength_or_pattern or not has_weakness or not has_recommendation:
        return None, _validation_failure(
            "schema_error",
            "The summary must include at least one strength or positive pattern, one weakness, and one recommendation.",
            field="insights",
        )
    if reliability_issues_present and not has_reliability_interpretation:
        return None, _validation_failure(
            "unsupported_claim",
            "Reliability issues are present but no reliability-aware insight was returned.",
            field="insights",
        )
    return (
        {
            "summary_mode": "llm_teacher_insight",
            "headline": headline,
            "insights": validated,
        },
        None,
    )


# Check whether the LLM teaching summary is enabled.
def _llm_summary_enabled() -> bool:
    if current_app.testing and "AMS_ENABLE_ANALYTICS_LLM_SUMMARY" not in current_app.config:
        return False
    value = current_app.config.get("AMS_ENABLE_ANALYTICS_LLM_SUMMARY", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return value is not False


# Optionally enhance teaching insights with an LLM summary.
def _maybe_enhance_teaching_insights(analytics: Mapping[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    baseline = _normalize_teaching_insights(analytics.get("teaching_insights"))
    context = dict(analytics.get("teaching_insight_context", {}) or {})
    deterministic_result = {
        "summary_mode": "deterministic",
        "headline": "",
        "insights": baseline,
    }
    if not baseline:
        return deterministic_result, "deterministic", {}
    if not _llm_summary_enabled():
        return deterministic_result, "deterministic", {}

    try:
        provider = get_llm_provider()
        prompt_payload = {
            "assignment_analytics": context,
            "valid_evidence_keys": sorted(str(key) for key in context.keys()),
        }
        system_prompt = (
            "You are generating a teacher-facing assignment analytics insight summary for an automated marking system.\n"
            "Use only the structured analytics provided for this one assignment.\n"
            "Your job is to interpret the evidence for a teacher or marker, not to restate metrics.\n"
            "Do not paraphrase the deterministic summary. Do not merely restate counts, percentages, or rankings. "
            "Do not give generic advice that could apply to any cohort.\n"
            "Every insight must explain significance: what the pattern means, why it may be happening, why it matters, "
            "or what the teacher should do next.\n"
            "Prioritise the most important cohort pattern first, then meaningful strengths, recurring weaknesses, "
            "notable anomalies, grounded contributing factors, and practical next actions.\n"
            "If reliability or confidence issues are present, treat them as part of the interpretation rather than a footnote.\n"
            "Remain grounded in the provided analytics only. Do not invent facts, causes, or recommendations that are not plausibly supported.\n"
            "Write for teachers and markers in concise academic/admin language.\n"
            "Avoid shallow outputs such as 'JavaScript is strongest and SQL is weakest', "
            "'4 submissions were partially evaluated', or 'Rule X affected 4 submissions' unless you explain what that means.\n"
            "Return JSON only in this exact structure:\n"
            "{\n"
            '  "summary_mode": "llm_teacher_insight",\n'
            '  "headline": "<one-sentence overall interpretation>",\n'
            '  "insights": [\n'
            "    {\n"
            '      "priority": "high|medium|low",\n'
            '      "type": "pattern|strength|weakness|anomaly|cause|recommendation|trend",\n'
            '      "title": "<short teacher-facing label>",\n'
            '      "text": "<1-3 sentence grounded interpretation>",\n'
            '      "evidence_keys": ["<analytics_key>", "<analytics_key>"]\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Provide 4 to 6 insights. Include at least one weakness, at least one recommendation, and at least one positive pattern or strength. "
            "If reliability issues are present, include at least one reliability-aware interpretation."
        )
        response = provider.complete(
            json.dumps(prompt_payload, indent=2, sort_keys=True),
            system_prompt=system_prompt,
            temperature=0.2,
            max_tokens=1400,
            json_mode=True,
        )
        if not response.success:
            reason = _validation_failure(
                "generation_unavailable",
                str(response.error or "LLM summary enhancement failed"),
                field="provider",
            )
            logger.info("Teaching insight generation failed: %s", json.dumps(reason, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "unavailable",
                "fallback_reason_code": reason["category"],
                "fallback_reason": _user_facing_teaching_summary_fallback(None, validation_rejected=False),
            }
        try:
            payload = json.loads(clean_json_response(response.content))
        except json.JSONDecodeError:
            reason = _validation_failure("invalid_json", "LLM summary response could not be parsed as valid JSON.")
            logger.info("Teaching insight validation failed: %s", json.dumps(reason, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "rejected",
                "fallback_reason_code": reason["category"],
                "fallback_reason": _user_facing_teaching_summary_fallback(reason, validation_rejected=True),
            }
        validated, reason = _validate_enhanced_teaching_summary(payload, context)
        if validated is None:
            logger.info("Teaching insight validation failed: %s", json.dumps(reason or {}, sort_keys=True))
            return deterministic_result, "deterministic", {
                "validation_status": "rejected",
                "fallback_reason_code": str((reason or {}).get("category") or "schema_error"),
                "fallback_reason": _user_facing_teaching_summary_fallback(reason, validation_rejected=True),
            }
        return validated, "llm", {}
    except Exception as exc:
        reason = _validation_failure(
            "generation_unavailable",
            str(exc or "LLM summary enhancement failed"),
            field="provider",
        )
        logger.info("Teaching insight generation failed: %s", json.dumps(reason, sort_keys=True))
        return deterministic_result, "deterministic", {
            "validation_status": "unavailable",
            "fallback_reason_code": "generation_unavailable",
            "fallback_reason": _user_facing_teaching_summary_fallback(None, validation_rejected=False),
        }
