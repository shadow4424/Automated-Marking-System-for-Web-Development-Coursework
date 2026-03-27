"""Insight and student-facing analytics helpers."""
from __future__ import annotations

import statistics
from typing import List, Mapping, Sequence

from ams.analytics.assignment_analytics import (
    FINDING_LABELS,
    FUNCTIONAL_ANALYTICS_STAGES,
    SEVERITY_PRIORITY,
    SMALL_COHORT_THRESHOLD,
    STATIC_ANALYTICS_STAGES,
)
from ams.core.profiles import get_relevant_components

def _teaching_insights(
    *,
    context: Mapping[str, object],
) -> List[dict]:
    insights: List[dict] = []
    assigned_students = int(context.get("assigned_students", 0))
    active_in_scope = int(context.get("active_in_scope", 0))
    missing_assigned = int(context.get("missing_assigned", 0))
    coverage_percent = int(context.get("coverage_percent", 0))
    partially_evaluated = int(context.get("partially_evaluated", 0))
    not_analysable = int(context.get("not_analysable", 0))
    manual_review = int(context.get("manual_review", 0))
    limitation_incidents = int(context.get("limitation_incidents", 0))
    small_cohort_enabled = bool(context.get("small_cohort_enabled"))
    strongest = dict(context.get("strongest_requirement", {}) or {})
    weakest = dict(context.get("weakest_requirement", {}) or {})
    top_rule = dict(context.get("top_failing_rule", {}) or {})
    major_limitations = list(context.get("major_limitations", []) or [])

    if assigned_students:
        if active_in_scope == 0:
            coverage_text = "No assigned students currently have an active submission in scope."
            coverage_priority = "high"
        elif missing_assigned == 0:
            coverage_text = "All assigned students currently have an active submission in scope."
            coverage_priority = "low"
        else:
            coverage_text = (
                f"{active_in_scope} of {assigned_students} assigned students currently have an active submission in scope; "
                f"{missing_assigned} are still missing."
            )
            coverage_priority = "medium"
        insights.append(
            {
                "insight_type": "coverage",
                "priority": coverage_priority,
                "text": coverage_text,
                "supporting_metric_keys": [
                    "assigned_students",
                    "active_in_scope",
                    "missing_assigned",
                    "coverage_percent",
                ],
            }
        )

    if strongest and weakest:
        if strongest.get("title") == weakest.get("title"):
            requirement_text = f"{strongest.get('title', 'Requirement coverage')} is the only requirement area with enough evaluable evidence to summarise so far."
        elif small_cohort_enabled:
            requirement_text = (
                f"{strongest.get('title', 'Requirement coverage')} is strongest ({strongest.get('students_met', 0)} fully met), "
                f"while {weakest.get('title', 'Requirement coverage')} is weakest ({weakest.get('students_met', 0)} fully met)."
            )
        else:
            requirement_text = (
                f"{strongest.get('title', 'Requirement coverage')} is currently the strongest requirement area by full attainment, "
                f"while {weakest.get('title', 'Requirement coverage')} is the weakest."
            )
        insights.append(
            {
                "insight_type": "requirement_balance",
                "priority": "medium",
                "text": requirement_text,
                "supporting_metric_keys": [
                    "strongest_requirement",
                    "weakest_requirement",
                ],
            }
        )

    if top_rule:
        affected = int(top_rule.get("submissions_affected", top_rule.get("students_affected", 0)) or 0)
        top_rule_text = (
            f"{top_rule.get('label', 'The top failing rule')} ({top_rule.get('rule_id', '')}) is the most common rule-level issue, "
            f"affecting {affected} active submission{'s' if affected != 1 else ''}"
        )
        if not small_cohort_enabled and coverage_percent:
            top_rule_text += f" ({int(round(float(top_rule.get('percent', 0) or 0)))}%)."
        else:
            top_rule_text += "."
        insights.append(
            {
                "insight_type": "rule_pattern",
                "priority": "medium" if affected <= 1 else "high",
                "text": top_rule_text,
                "supporting_metric_keys": [
                    "top_failing_rule",
                    "active_in_scope",
                ],
            }
        )

    if manual_review or partially_evaluated or not_analysable or limitation_incidents:
        reliability_text = (
            f"Manual review is recommended for {manual_review} active submission{'s' if manual_review != 1 else ''}; "
            f"{partially_evaluated} were partially evaluated and {not_analysable} were not analysable."
        )
        if major_limitations:
            reliability_text += f" The main confidence risk is {major_limitations[0].get('label', 'runner limitations').lower()}."
        insights.append(
            {
                "insight_type": "reliability",
                "priority": "high",
                "text": reliability_text,
                "supporting_metric_keys": [
                    "manual_review",
                    "partially_evaluated",
                    "not_analysable",
                    "limitation_incidents",
                    "major_limitations",
                ],
            }
        )
    else:
        insights.append(
            {
                "insight_type": "reliability",
                "priority": "low",
                "text": "Automated evaluation confidence is currently high across the active submissions in scope.",
                "supporting_metric_keys": [
                    "manual_review",
                    "partially_evaluated",
                    "not_analysable",
                    "limitation_incidents",
                ],
            }
        )

    return insights[:4]

def _teaching_insight_context(
    *,
    profile: str,
    overall: Mapping[str, object],
    coverage: Mapping[str, object],
    components: Sequence[Mapping[str, object]],
    requirement_coverage: Sequence[Mapping[str, object]],
    top_failing_rules: Sequence[Mapping[str, object]],
    reliability: Mapping[str, object],
    needs_attention: Sequence[Mapping[str, object]],
    interactive_graphs: Mapping[str, object],
    total_records: int,
    small_cohort: Mapping[str, object],
) -> dict:
    strongest = None
    weakest = None
    if requirement_coverage:
        strongest_row = max(
            requirement_coverage,
            key=lambda row: (float(row.get("met_percent", 0) or 0), int(row.get("students_met", 0) or 0), str(row.get("title") or "")),
        )
        weakest_row = min(
            requirement_coverage,
            key=lambda row: (float(row.get("met_percent", 0) or 0), int(row.get("students_met", 0) or 0), str(row.get("title") or "")),
        )
        strongest = {
            "component": strongest_row.get("component"),
            "title": strongest_row.get("title"),
            "students_met": int(strongest_row.get("students_met", 0) or 0),
            "met_percent": round(float(strongest_row.get("met_percent", 0) or 0), 2),
        }
        weakest = {
            "component": weakest_row.get("component"),
            "title": weakest_row.get("title"),
            "students_met": int(weakest_row.get("students_met", 0) or 0),
            "met_percent": round(float(weakest_row.get("met_percent", 0) or 0), 2),
        }

    top_rule = None
    if top_failing_rules:
        first_rule = dict(top_failing_rules[0])
        top_rule = {
            "rule_id": first_rule.get("rule_id"),
            "label": first_rule.get("label"),
            "component": first_rule.get("component"),
            "severity": first_rule.get("severity"),
            "submissions_affected": int(first_rule.get("submissions_affected", first_rule.get("students_affected", 0)) or 0),
            "percent": round(float(first_rule.get("percent", 0) or 0), 2),
        }

    score_band_distribution = [
        {
            "label": str(label),
            "count": int(count or 0),
            "percent": round((int(count or 0) / total_records) * 100, 2) if total_records else 0.0,
        }
        for label, count in dict(overall.get("buckets", {}) or {}).items()
    ]
    dominant_score_band = None
    non_zero_score_bands = [item for item in score_band_distribution if int(item.get("count", 0) or 0) > 0]
    if non_zero_score_bands:
        max_count = max(int(item.get("count", 0) or 0) for item in non_zero_score_bands)
        leaders = [item for item in non_zero_score_bands if int(item.get("count", 0) or 0) == max_count]
        if len(leaders) == 1:
            dominant_score_band = leaders[0]

    requirement_summary = [
        {
            "component": row.get("component"),
            "title": row.get("title"),
            "rule_count": int(row.get("rule_count", 0) or 0),
            "met_count": int(row.get("students_met", 0) or 0),
            "partial_count": int(row.get("students_partial", 0) or 0),
            "unmet_count": int(row.get("students_unmet", 0) or 0),
            "not_evaluable_count": int(row.get("students_not_evaluable", 0) or 0),
            "fully_met_percent": round(float(row.get("met_percent", 0) or 0), 2),
        }
        for row in list(requirement_coverage or [])
    ]

    component_summary = [
        {
            "component": row.get("component"),
            "title": row.get("title"),
            "average_component_score": round(float(row.get("average", 0) or 0) * 100, 2) if row.get("average") is not None else None,
            "median_component_score": round(float(row.get("median", 0) or 0) * 100, 2) if row.get("median") is not None else None,
            "score_0_count": int(row.get("count_zero", 0) or 0),
            "score_0_5_count": int(row.get("count_half", 0) or 0),
            "score_1_count": int(row.get("count_full", 0) or 0),
            "other_scored_count": int(row.get("count_other", 0) or 0),
            "total_evaluable": int(row.get("total_evaluable", 0) or 0),
        }
        for row in list(components or [])
    ]

    major_rule_categories_map: dict[str, dict[str, object]] = {}
    for rule in list(top_failing_rules or []):
        category = str(rule.get("category") or "other")
        entry = major_rule_categories_map.setdefault(
            category,
            {
                "category": category,
                "rules_affected": 0,
                "students_affected_total": 0,
                "incident_count_total": 0,
                "fail_incidents": 0,
                "warning_incidents": 0,
            },
        )
        entry["rules_affected"] = int(entry["rules_affected"]) + 1
        entry["students_affected_total"] = int(entry["students_affected_total"]) + int(rule.get("students_affected", 0) or 0)
        entry["incident_count_total"] = int(entry["incident_count_total"]) + int(rule.get("incident_count", 0) or 0)
        entry["fail_incidents"] = int(entry["fail_incidents"]) + int(rule.get("fail_incidents", 0) or 0)
        entry["warning_incidents"] = int(entry["warning_incidents"]) + int(rule.get("warning_incidents", 0) or 0)
    major_rule_categories = sorted(
        major_rule_categories_map.values(),
        key=lambda item: (
            -int(item.get("students_affected_total", 0) or 0),
            -int(item.get("incident_count_total", 0) or 0),
            str(item.get("category") or ""),
        ),
    )[:4]

    scatter = dict((interactive_graphs or {}).get("static_functional_scatter_plot", {}) or {})
    mismatch_examples: List[dict[str, object]] = []
    high_static_low_behavioural = 0
    high_behavioural_low_static = 0
    balanced_submissions = 0
    plotted_students = 0
    for point in list(scatter.get("points", []) or []):
        static_score = _coerce_float(point.get("static_score_percent"))
        behavioural_score = _coerce_float(point.get("behavioural_score_percent"))
        if static_score is None or behavioural_score is None:
            continue
        plotted_students += 1
        gap = round(static_score - behavioural_score, 2)
        if gap >= 20:
            high_static_low_behavioural += 1
        elif gap <= -20:
            high_behavioural_low_static += 1
        if abs(gap) <= 10:
            balanced_submissions += 1
        mismatch_examples.append(
            {
                "student_id": point.get("student_id"),
                "overall_mark_percent": point.get("overall_mark_percent"),
                "static_score_percent": static_score,
                "behavioural_score_percent": behavioural_score,
                "gap_percent": gap,
                "manual_review_recommended": bool(point.get("manual_review_recommended")),
                "confidence": point.get("confidence"),
            }
        )
    mismatch_examples.sort(key=lambda item: abs(float(item.get("gap_percent", 0) or 0)), reverse=True)

    flagged_examples = [
        {
            "student_id": item.get("student_id"),
            "severity": item.get("severity"),
            "confidence": item.get("confidence"),
            "reason": item.get("reason"),
            "overall_score": round(float(item.get("overall", 0) or 0) * 100, 2) if isinstance(item.get("overall"), (int, float)) else None,
            "manual_review_recommended": bool(item.get("manual_review_recommended")),
        }
        for item in list(needs_attention or [])[:4]
    ]

    top_rules_summary = [
        {
            "rule_id": item.get("rule_id"),
            "label": item.get("label"),
            "component": item.get("component"),
            "category": item.get("category"),
            "severity": item.get("severity"),
            "submissions_affected": int(item.get("submissions_affected", item.get("students_affected", 0)) or 0),
            "percent": round(float(item.get("percent", 0) or 0), 2),
            "incident_count": int(item.get("incident_count", 0) or 0),
            "confidence_affecting": bool(item.get("confidence_affecting")),
        }
        for item in list(top_failing_rules or [])[:5]
    ]

    return {
        "profile": profile,
        "assigned_students": int(coverage.get("assigned_students", 0) or 0),
        "active_in_scope": int(coverage.get("active_in_scope", total_records) or 0),
        "coverage_percent": int(coverage.get("coverage_percent", 0) or 0),
        "missing_assigned": int(coverage.get("missing_assigned", 0) or 0),
        "average_score": round(float(overall.get("mean", 0) or 0) * 100, 2) if overall.get("mean") is not None else None,
        "median_score": round(float(overall.get("median", 0) or 0) * 100, 2) if overall.get("median") is not None else None,
        "min_score": round(float(overall.get("min", 0) or 0) * 100, 2) if overall.get("min") is not None else None,
        "max_score": round(float(overall.get("max", 0) or 0) * 100, 2) if overall.get("max") is not None else None,
        "score_band_distribution": score_band_distribution,
        "dominant_score_band": dominant_score_band,
        "fully_evaluated": int(reliability.get("fully_evaluated", 0) or 0),
        "partially_evaluated": int(reliability.get("partially_evaluated", 0) or 0),
        "not_analysable": int(reliability.get("not_analysable", 0) or 0),
        "manual_review": int(reliability.get("manual_review", 0) or 0),
        "limitation_incidents": int(reliability.get("limitation_incidents", 0) or 0),
        "limitation_categories": int(reliability.get("limitation_categories", 0) or 0),
        "confidence_mix": {
            "high": {
                "count": int((reliability.get("confidence", {}) or {}).get("high", 0) or 0),
                "percent": round(float((reliability.get("confidence", {}) or {}).get("high_percent", 0) or 0), 2),
            },
            "medium": {
                "count": int((reliability.get("confidence", {}) or {}).get("medium", 0) or 0),
                "percent": round(float((reliability.get("confidence", {}) or {}).get("medium_percent", 0) or 0), 2),
            },
            "low": {
                "count": int((reliability.get("confidence", {}) or {}).get("low", 0) or 0),
                "percent": round(float((reliability.get("confidence", {}) or {}).get("low_percent", 0) or 0), 2),
            },
        },
        "runtime_skip_count": int(reliability.get("runtime_skipped", 0) or 0),
        "browser_skip_count": int(reliability.get("browser_skipped", 0) or 0),
        "runtime_failure_count": int(reliability.get("runtime_issue_submissions", 0) or 0),
        "browser_failure_count": int(reliability.get("browser_issue_submissions", 0) or 0),
        "major_limitations": [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "incident_count": int(item.get("incident_count", 0) or 0),
                "percent": round((int(item.get("incident_count", 0) or 0) / total_records) * 100, 2) if total_records else 0.0,
            }
            for item in list(reliability.get("limitation_breakdown", []) or [])[:4]
        ],
        "strongest_requirement": strongest,
        "weakest_requirement": weakest,
        "requirement_coverage_summary": requirement_summary,
        "component_performance_summary": component_summary,
        "top_failing_rule": top_rule,
        "top_failing_rules": top_rules_summary,
        "major_rule_categories": major_rule_categories,
        "static_vs_behavioural_mismatch": {
            "supported": bool(scatter.get("supported")),
            "unsupported_reason": str(scatter.get("unsupported_reason") or ""),
            "plotted_student_count": plotted_students,
            "behavioural_evaluable_students": int(scatter.get("behavioural_evaluable_students", 0) or 0),
            "high_static_low_behavioural_count": high_static_low_behavioural,
            "high_behavioural_low_static_count": high_behavioural_low_static,
            "balanced_count": balanced_submissions,
            "mean_static_score": scatter.get("reference_lines", {}).get("static_mean_percent"),
            "mean_behavioural_score": scatter.get("reference_lines", {}).get("behavioural_mean_percent"),
            "largest_gap_examples": mismatch_examples[:3],
        },
        "high_priority_flagged_submissions": {
            "count": sum(1 for item in list(needs_attention or []) if str(item.get("severity") or "").lower() == "high"),
            "medium_or_higher_count": sum(
                1 for item in list(needs_attention or []) if str(item.get("severity") or "").lower() in {"high", "medium"}
            ),
            "low_confidence_count": sum(1 for item in list(needs_attention or []) if str(item.get("confidence") or "").lower() == "low"),
            "manual_review_count": sum(1 for item in list(needs_attention or []) if bool(item.get("manual_review_recommended"))),
            "examples": flagged_examples,
        },
        "small_cohort_enabled": bool(small_cohort.get("enabled")),
        "small_cohort_threshold": int(small_cohort.get("threshold", SMALL_COHORT_THRESHOLD) or SMALL_COHORT_THRESHOLD),
        "small_cohort_note": str(small_cohort.get("note") or ""),
    }

def _build_student_assignment_analytics(
    *,
    assignment: Mapping[str, object],
    analytics: Mapping[str, object],
    records: Sequence[Mapping[str, object]],
    student_record: Mapping[str, object],
) -> dict:
    relevant_components = [
        str(component)
        for component in get_relevant_components(str(assignment.get("profile") or "frontend"))
        if str(component).strip()
    ]
    overall = dict(analytics.get("overall", {}) or {})
    reliability = dict(analytics.get("reliability", {}) or {})
    top_rules = list(analytics.get("top_failing_rules", []) or [])
    component_rows = list(analytics.get("components", []) or [])
    requirement_rows = list(analytics.get("requirement_coverage", []) or [])
    graphs = _student_safe_graphs(records, student_record)
    component_comparison = _student_component_comparison(
        student_record=student_record,
        component_rows=component_rows,
        records=records,
        relevant_components=relevant_components,
    )
    top_failing_context = _student_top_failing_context(
        student_record=student_record,
        top_rules=top_rules,
    )
    strengths, improvements = _student_strengths_and_improvements(
        student_record=student_record,
        component_comparison=component_comparison,
        top_failing_context=top_failing_context,
    )
    needs_attention = _student_needs_attention_items(
        student_record=student_record,
        overall=overall,
        component_comparison=component_comparison,
        top_failing_context=top_failing_context,
    )
    detailed_checks = _student_detailed_checks(student_record, top_rules)
    student_summary = _student_result_summary(
        assignment=assignment,
        student_record=student_record,
        overall=overall,
        component_comparison=component_comparison,
    )
    feedback_context = _student_personal_context(
        assignment=assignment,
        student_record=student_record,
        overall=overall,
        reliability=reliability,
        component_comparison=component_comparison,
        top_failing_context=top_failing_context,
        strengths=strengths,
        improvements=improvements,
        attention_items=needs_attention,
    )
    personal_insights = _student_personal_insights(feedback_context)

    requirement_comparison = []
    for row in requirement_rows:
        component = str(row.get("component") or "").strip().lower()
        if component and component not in relevant_components:
            continue
        requirement_state = _student_requirement_state(student_record, component)
        requirement_comparison.append(
            {
                "component": component,
                "title": str(row.get("title") or component.upper()),
                "student_status": requirement_state,
                "student_label": _student_requirement_state_label(requirement_state),
                "cohort_met_percent": round(float(row.get("met_percent", 0) or 0), 2),
                "cohort_partial_percent": round(
                    (int(row.get("students_partial", 0) or 0) / len(records) * 100) if records else 0,
                    2,
                ),
                "cohort_not_evaluable_percent": round(
                    (int(row.get("students_not_evaluable", 0) or 0) / len(records) * 100) if records else 0,
                    2,
                ),
            }
        )

    return {
        "assignment": {
            "id": str(assignment.get("assignmentID") or ""),
            "title": str(assignment.get("title") or assignment.get("assignmentID") or ""),
            "profile": str(assignment.get("profile") or ""),
            "marks_released": bool(assignment.get("marks_released")),
            "due_date": str(assignment.get("due_date") or ""),
        },
        "student": student_summary,
        "cohort": {
            "submission_count": len(records),
            "average_score_percent": round(float(overall.get("mean", 0) or 0) * 100, 2) if overall.get("mean") is not None else None,
            "median_score_percent": round(float(overall.get("median", 0) or 0) * 100, 2) if overall.get("median") is not None else None,
            "min_score_percent": round(float(overall.get("min", 0) or 0) * 100, 2) if overall.get("min") is not None else None,
            "max_score_percent": round(float(overall.get("max", 0) or 0) * 100, 2) if overall.get("max") is not None else None,
            "confidence_mix": {
                "high": int((reliability.get("confidence", {}) or {}).get("high", 0) or 0),
                "medium": int((reliability.get("confidence", {}) or {}).get("medium", 0) or 0),
                "low": int((reliability.get("confidence", {}) or {}).get("low", 0) or 0),
            },
            "small_cohort": dict(analytics.get("small_cohort", {}) or {}),
        },
        "personal_insights": personal_insights,
        "graphs": graphs,
        "strengths": strengths,
        "improvements": improvements,
        "needs_attention": needs_attention,
        "top_failing_context": top_failing_context,
        "component_comparison": component_comparison,
        "requirement_comparison": requirement_comparison,
        "detailed_checks": detailed_checks,
        "feedback_context": feedback_context,
    }

def _student_result_summary(
    *,
    assignment: Mapping[str, object],
    student_record: Mapping[str, object],
    overall: Mapping[str, object],
    component_comparison: Sequence[Mapping[str, object]],
) -> dict:
    overall_percent = (
        round(float(student_record.get("overall", 0) or 0) * 100)
        if isinstance(student_record.get("overall"), (int, float))
        else None
    )
    confidence_level = str(student_record.get("confidence") or "high").strip().lower()
    median_percent = (
        round(float(overall.get("median", 0) or 0) * 100, 2)
        if overall.get("median") is not None
        else None
    )
    summary_line = _student_summary_line(
        student_record=student_record,
        component_comparison=component_comparison,
        median_percent=median_percent,
    )
    return {
        "assignment_title": str(assignment.get("title") or assignment.get("assignmentID") or ""),
        "submitted_at": str(student_record.get("_created_at") or ""),
        "attempt_id": str(student_record.get("attempt_id") or ""),
        "attempt_number": student_record.get("attempt_number"),
        "source_type": str(student_record.get("source_type") or ""),
        "validity_status": str(student_record.get("validity_status") or ""),
        "is_active": bool(student_record.get("is_active")),
        "selection_reason": str(student_record.get("selection_reason") or ""),
        "overall_score": student_record.get("overall"),
        "overall_percent": overall_percent,
        "result_label": _student_result_label(student_record),
        "confidence": confidence_level,
        "confidence_label": confidence_level.capitalize(),
        "confidence_explanation": _student_confidence_explanation(student_record),
        "manual_review_recommended": bool(student_record.get("manual_review_recommended")),
        "manual_review_label": "Recommended" if student_record.get("manual_review_recommended") else "Not required",
        "evaluation_state": str(student_record.get("evaluation_state") or "fully_evaluated"),
        "status": str(student_record.get("status") or "ok"),
        "grade": str(student_record.get("grade") or "unknown"),
        "summary_line": summary_line,
        "cohort_position": _student_cohort_position_label(overall_percent, median_percent),
        "confidence_fairness_note": (
            "Confidence reflects how complete the automated evidence is for this result, not just the quality of your work."
        ),
    }

def _student_safe_graphs(
    records: Sequence[Mapping[str, object]],
    student_record: Mapping[str, object],
) -> dict:
    from ams.analytics.graphs import _build_mark_distribution_histogram, _requirement_axis_score

    student_id = str(student_record.get("student_id") or "").strip()
    histogram = _build_mark_distribution_histogram(records)
    safe_bins = []
    for raw_bin in list(histogram.get("bins", []) or []):
        student_ids = {
            str(current_id).strip()
            for current_id in list(raw_bin.get("student_ids", []) or [])
            if str(current_id).strip()
        }
        safe_bins.append(
            {
                "id": str(raw_bin.get("id") or ""),
                "label": str(raw_bin.get("label") or ""),
                "range_min": int(raw_bin.get("range_min", 0) or 0),
                "range_max": int(raw_bin.get("range_max", 0) or 0),
                "count": int(raw_bin.get("count", 0) or 0),
                "percent": round(float(raw_bin.get("percent", 0) or 0), 2),
                "is_current_student": bool(student_id and student_id in student_ids),
            }
        )

    scatter_points: list[dict] = []
    plotted_static_scores: list[float] = []
    plotted_behavioural_scores: list[float] = []
    behavioural_evaluable_students = 0
    static_requirement_support = 0
    behavioural_requirement_support = 0
    for record in records:
        current_student_id = str(record.get("student_id") or "").strip()
        if not current_student_id:
            continue
        overall_score = record.get("overall")
        static_axis = _requirement_axis_score(record, STATIC_ANALYTICS_STAGES)
        behavioural_axis = _requirement_axis_score(record, FUNCTIONAL_ANALYTICS_STAGES)
        static_score_percent = (
            round(float(static_axis.get("score", 0) or 0) * 100, 2)
            if isinstance(static_axis.get("score"), (int, float))
            else None
        )
        behavioural_score_percent = (
            round(float(behavioural_axis.get("score", 0) or 0) * 100, 2)
            if isinstance(behavioural_axis.get("score"), (int, float))
            else None
        )
        static_requirement_support += int(static_axis.get("requirement_count", 0) or 0)
        behavioural_requirement_support += int(behavioural_axis.get("requirement_count", 0) or 0)
        if int(behavioural_axis.get("evaluable_count", 0) or 0) > 0:
            behavioural_evaluable_students += 1
        if static_score_percent is not None:
            plotted_static_scores.append(static_score_percent)
        if behavioural_score_percent is not None:
            plotted_behavioural_scores.append(behavioural_score_percent)
        scatter_points.append(
            {
                "static_score_percent": static_score_percent,
                "behavioural_score_percent": behavioural_score_percent,
                "x": static_score_percent,
                "y": behavioural_score_percent,
                "overall_mark_percent": round(float(overall_score) * 100, 2) if isinstance(overall_score, (int, float)) else None,
                "overall_percent": round(float(overall_score) * 100, 2) if isinstance(overall_score, (int, float)) else None,
                "confidence": str(record.get("confidence") or "high"),
                "manual_review_recommended": bool(record.get("manual_review_recommended")),
                "functional_evidence_limited": int(behavioural_axis.get("evaluable_count", 0) or 0) == 0,
                "is_current_student": current_student_id == student_id,
            }
        )

    scatter_supported = bool(
        records
        and static_requirement_support > 0
        and behavioural_requirement_support > 0
        and behavioural_evaluable_students >= 2
    )
    scatter_reason = ""
    if not records:
        scatter_reason = "Cohort comparison will appear once assignment submissions are available."
    elif behavioural_requirement_support == 0:
        scatter_reason = "This assignment does not include enough runtime or browser evidence for a static-vs-functional comparison."
    elif behavioural_evaluable_students < 2:
        scatter_reason = "Not enough functional evidence is available across the cohort to plot this comparison yet."
    elif static_requirement_support == 0:
        scatter_reason = "Static and code-quality evidence is not available for this assignment."

    return {
        "histogram": {
            "unscored_submissions": int(histogram.get("unscored_submissions", 0) or 0),
            "bin_width": histogram.get("bin_width"),
            "mean_percent": histogram.get("mean_percent"),
            "median_percent": histogram.get("median_percent"),
            "pass_threshold_percent": 50,
            "x_ticks": [0, 20, 40, 60, 80, 100],
            "primary_reference": {
                "key": "mean_percent",
                "label": "Mean",
                "value": histogram.get("mean_percent"),
                "detail": "Cohort mean mark across the active submissions in scope.",
            },
            "summary_stats": {
                "mean_percent": histogram.get("mean_percent"),
                "median_percent": histogram.get("median_percent"),
                "pass_threshold_percent": 50,
            },
            "bins": safe_bins,
        },
        "scatter": {
            "supported": scatter_supported,
            "unsupported_reason": scatter_reason,
            "reference_lines": {
                "show_balance_diagonal": True,
                "show_mean_lines": True,
                "static_mean_percent": round(statistics.mean(plotted_static_scores), 2) if plotted_static_scores else None,
                "behavioural_mean_percent": round(statistics.mean(plotted_behavioural_scores), 2) if plotted_behavioural_scores else None,
            },
            "points": scatter_points,
        },
    }

def _student_component_comparison(
    *,
    student_record: Mapping[str, object],
    component_rows: Sequence[Mapping[str, object]],
    records: Sequence[Mapping[str, object]],
    relevant_components: Sequence[str],
) -> list[dict]:
    rows: list[dict] = []
    for row in component_rows:
        component = str(row.get("component") or "").strip().lower()
        if component and component not in relevant_components:
            continue
        cohort_scores = [
            round(float((record.get("components", {}) or {}).get(component) or 0) * 100, 2)
            for record in records
            if isinstance((record.get("components", {}) or {}).get(component), (int, float))
        ]
        student_score = (student_record.get("components", {}) or {}).get(component)
        student_percent = round(float(student_score) * 100, 2) if isinstance(student_score, (int, float)) else None
        cohort_mean = round(float(row.get("average", 0) or 0) * 100, 2) if row.get("average") is not None else None
        cohort_median = round(float(row.get("median", 0) or 0) * 100, 2) if row.get("median") is not None else None
        if student_percent is None and not cohort_scores:
            continue
        rows.append(
            {
                "component": component,
                "title": str(row.get("title") or component.upper()),
                "student_percent": student_percent,
                "cohort_mean_percent": cohort_mean,
                "cohort_median_percent": cohort_median,
                "cohort_min_percent": min(cohort_scores) if cohort_scores else None,
                "cohort_max_percent": max(cohort_scores) if cohort_scores else None,
                "total_evaluable": int(row.get("total_evaluable", 0) or 0),
                "interpretation": _student_component_interpretation(student_percent, cohort_median),
                "student_band": _score_band_label(student_percent),
            }
        )

    rows.sort(
        key=lambda item: (
            -(float(item.get("student_percent")) if isinstance(item.get("student_percent"), (int, float)) else -1.0),
            str(item.get("component") or ""),
        )
    )
    return rows

def _student_top_failing_context(
    *,
    student_record: Mapping[str, object],
    top_rules: Sequence[Mapping[str, object]],
) -> list[dict]:
    outcome_index = {
        str(outcome.get("id") or ""): dict(outcome)
        for outcome in list(student_record.get("problem_outcomes", []) or [])
        if str(outcome.get("id") or "").strip()
    }
    rows: list[dict] = []
    for rule in list(top_rules or [])[:6]:
        rule_id = str(rule.get("rule_id") or "").strip()
        if not rule_id:
            continue
        student_outcome = outcome_index.get(rule_id)
        student_status = "MET"
        if student_outcome is not None:
            status = str(student_outcome.get("status") or "").upper()
            if status == "FAIL":
                student_status = "FAIL"
            elif status == "WARN":
                student_status = "PARTIAL"
            elif status == "SKIPPED":
                student_status = "SKIPPED"
        rows.append(
            {
                "rule_id": rule_id,
                "label": str(rule.get("label") or rule_id),
                "component": str(rule.get("component") or "other"),
                "severity": str(rule.get("severity") or "WARN"),
                "cohort_missed_percent": round(float(rule.get("percent", 0) or 0), 2),
                "cohort_attainment_percent": round(max(0.0, 100.0 - float(rule.get("percent", 0) or 0)), 2),
                "incident_count": int(rule.get("incident_count", 0) or 0),
                "student_status": student_status,
                "student_label": _student_requirement_state_label(student_status),
                "common_issue": float(rule.get("percent", 0) or 0) >= 30.0,
                "message": _first_non_empty(
                    list(rule.get("messages", []) or [])
                    + ([str(student_outcome.get("message") or "")] if student_outcome else [])
                ),
            }
        )
    return rows

def _student_strengths_and_improvements(
    *,
    student_record: Mapping[str, object],
    component_comparison: Sequence[Mapping[str, object]],
    top_failing_context: Sequence[Mapping[str, object]],
) -> tuple[list[dict], list[dict]]:
    del student_record
    strengths: list[dict] = []
    improvements: list[dict] = []

    for component in list(component_comparison or [])[:2]:
        if not isinstance(component.get("student_percent"), (int, float)):
            continue
        if float(component.get("student_percent") or 0) < 50:
            continue
        strengths.append(
            {
                "title": str(component.get("title") or ""),
                "detail": (
                    f"You scored {int(round(float(component.get('student_percent') or 0)))}% in this area, "
                    f"{_student_component_interpretation(component.get('student_percent'), component.get('cohort_median_percent')).lower()}."
                ),
            }
        )

    for rule in list(top_failing_context or []):
        if rule.get("student_status") not in {"FAIL", "PARTIAL"}:
            continue
        improvements.append(
            {
                "title": str(rule.get("label") or ""),
                "detail": (
                    f"Your result was {str(rule.get('student_label') or '').lower()} here. "
                    f"Cohort attainment is {int(round(float(rule.get('cohort_attainment_percent') or 0)))}%."
                ),
                "rule_id": str(rule.get("rule_id") or ""),
            }
        )
        if len(improvements) >= 3:
            break

    for component in reversed(list(component_comparison or [])):
        if len(improvements) >= 3:
            break
        if not isinstance(component.get("student_percent"), (int, float)):
            continue
        if float(component.get("student_percent") or 0) >= 50:
            continue
        title = str(component.get("title") or "")
        if any(item.get("title") == title for item in improvements):
            continue
        improvements.append(
            {
                "title": title,
                "detail": (
                    f"You scored {int(round(float(component.get('student_percent') or 0)))}% in this area, "
                    f"{_student_component_interpretation(component.get('student_percent'), component.get('cohort_median_percent')).lower()}."
                ),
                "rule_id": "",
            }
        )

    if not strengths and component_comparison:
        top_component = component_comparison[0]
        strengths.append(
            {
                "title": str(top_component.get("title") or ""),
                "detail": (
                    f"This was your strongest assessed component at "
                    f"{int(round(float(top_component.get('student_percent') or 0)))}%."
                ),
            }
        )

    return strengths[:3], improvements[:4]

def _student_needs_attention_items(
    *,
    student_record: Mapping[str, object],
    overall: Mapping[str, object],
    component_comparison: Sequence[Mapping[str, object]],
    top_failing_context: Sequence[Mapping[str, object]],
) -> list[dict]:
    items: list[dict] = []
    if bool(student_record.get("manual_review_recommended")):
        items.append(
            {
                "title": "Manual review recommended",
                "severity": "high",
                "text": str(student_record.get("review_note") or "Some parts of this result should be checked manually."),
            }
        )

    confidence = str(student_record.get("confidence") or "high").strip().lower()
    if confidence != "high":
        items.append(
            {
                "title": "Confidence reduced",
                "severity": "medium" if confidence == "medium" else "high",
                "text": _student_confidence_explanation(student_record),
            }
        )

    student_percent = (
        round(float(student_record.get("overall", 0) or 0) * 100, 2)
        if isinstance(student_record.get("overall"), (int, float))
        else None
    )
    cohort_median = round(float(overall.get("median", 0) or 0) * 100, 2) if overall.get("median") is not None else None
    if isinstance(student_percent, (int, float)) and isinstance(cohort_median, (int, float)):
        items.append(
            {
                "title": (
                    "Overall score above the cohort median"
                    if student_percent > cohort_median
                    else "Overall score below the cohort median"
                ),
                "severity": "low" if student_percent > cohort_median else "medium",
                "text": (
                    f"Your overall score is {int(round(student_percent))}% compared with a cohort median of "
                    f"{int(round(cohort_median))}%."
                ),
            }
        )

    for component in list(component_comparison or []):
        if not isinstance(component.get("student_percent"), (int, float)):
            continue
        if str(component.get("interpretation") or "").startswith("Below"):
            items.append(
                {
                    "title": f"{component.get('title')} is below the cohort median",
                    "severity": "medium",
                    "text": (
                        f"You scored {int(round(float(component.get('student_percent') or 0)))}% in this area "
                        f"against a cohort median of {int(round(float(component.get('cohort_median_percent') or 0)))}%."
                    ),
                }
            )
            break

    for rule in list(top_failing_context or []):
        if rule.get("student_status") in {"FAIL", "PARTIAL"} and bool(rule.get("common_issue")):
            items.append(
                {
                    "title": "A common cohort issue affected your submission",
                    "severity": "low",
                    "text": (
                        f"{rule.get('label')} was also missed in {int(round(float(rule.get('cohort_missed_percent') or 0)))}% "
                        f"of active submissions."
                    ),
                }
            )
            break

    return items[:4]

def _student_detailed_checks(
    student_record: Mapping[str, object],
    top_rules: Sequence[Mapping[str, object]],
) -> list[dict]:
    requirement_index = {
        str(requirement.get("requirement_id") or "").strip(): dict(requirement)
        for requirement in list((student_record.get("score_evidence", {}) or {}).get("requirements", []) or [])
        if isinstance(requirement, Mapping) and str(requirement.get("requirement_id") or "").strip()
    }
    top_rule_index = {
        str(rule.get("rule_id") or "").strip(): dict(rule)
        for rule in list(top_rules or [])
        if str(rule.get("rule_id") or "").strip()
    }
    rows: list[dict] = []
    for outcome in list(student_record.get("problem_outcomes", []) or []):
        rule_id = str(outcome.get("id") or "").strip()
        if not rule_id:
            continue
        requirement = requirement_index.get(rule_id, {})
        cohort_rule = top_rule_index.get(rule_id, {})
        status = str(outcome.get("status") or "").upper()
        rows.append(
            {
                "id": rule_id,
                "label": str(outcome.get("label") or rule_id),
                "status": status,
                "status_label": _student_requirement_state_label(status),
                "component": str(outcome.get("component") or "other"),
                "stage": str(requirement.get("stage") or ""),
                "message": str(outcome.get("message") or _description_for_identifier(rule_id)),
                "cohort_missed_percent": round(float(cohort_rule.get("percent", 0) or 0), 2) if cohort_rule else None,
                "cohort_context": (
                    f"Also seen in {int(round(float(cohort_rule.get('percent', 0) or 0)))}% of active submissions."
                    if cohort_rule
                    else ""
                ),
            }
        )
    rows.sort(
        key=lambda item: (
            -SEVERITY_PRIORITY.get(str(item.get("status") or "PASS"), 0),
            str(item.get("component") or ""),
            str(item.get("id") or ""),
        )
    )
    return rows

def _student_personal_context(
    *,
    assignment: Mapping[str, object],
    student_record: Mapping[str, object],
    overall: Mapping[str, object],
    reliability: Mapping[str, object],
    component_comparison: Sequence[Mapping[str, object]],
    top_failing_context: Sequence[Mapping[str, object]],
    strengths: Sequence[Mapping[str, object]],
    improvements: Sequence[Mapping[str, object]],
    attention_items: Sequence[Mapping[str, object]],
) -> dict:
    overall_percent = (
        round(float(student_record.get("overall", 0) or 0) * 100, 2)
        if isinstance(student_record.get("overall"), (int, float))
        else None
    )
    return {
        "assignment_title": str(assignment.get("title") or assignment.get("assignmentID") or ""),
        "result_label": _student_result_label(student_record),
        "overall_score_percent": overall_percent,
        "cohort_average_percent": round(float(overall.get("mean", 0) or 0) * 100, 2) if overall.get("mean") is not None else None,
        "cohort_median_percent": round(float(overall.get("median", 0) or 0) * 100, 2) if overall.get("median") is not None else None,
        "confidence": str(student_record.get("confidence") or "high").capitalize(),
        "confidence_explanation": _student_confidence_explanation(student_record),
        "manual_review_recommended": bool(student_record.get("manual_review_recommended")),
        "strengths": [
            str(item.get("detail") or item.get("title") or "")
            for item in list(strengths or [])[:3]
            if str(item.get("detail") or item.get("title") or "").strip()
        ],
        "improvements": [
            str(item.get("detail") or item.get("title") or "")
            for item in list(improvements or [])[:4]
            if str(item.get("detail") or item.get("title") or "").strip()
        ],
        "attention_items": [
            str(item.get("text") or item.get("title") or "")
            for item in list(attention_items or [])[:4]
            if str(item.get("text") or item.get("title") or "").strip()
        ],
        "strongest_component": dict(component_comparison[0]) if component_comparison else {},
        "weakest_component": dict(component_comparison[-1]) if component_comparison else {},
        "common_cohort_issues": [
            {
                "label": str(item.get("label") or ""),
                "cohort_missed_percent": float(item.get("cohort_missed_percent", 0) or 0),
                "student_status": str(item.get("student_status") or ""),
            }
            for item in list(top_failing_context or [])[:4]
        ],
        "confidence_mix": {
            "high": int((reliability.get("confidence", {}) or {}).get("high", 0) or 0),
            "medium": int((reliability.get("confidence", {}) or {}).get("medium", 0) or 0),
            "low": int((reliability.get("confidence", {}) or {}).get("low", 0) or 0),
        },
    }

def _student_personal_insights(context: Mapping[str, object]) -> list[dict]:
    insights: list[dict] = []
    overall_score = _coerce_float(context.get("overall_score_percent"))
    cohort_median = _coerce_float(context.get("cohort_median_percent"))
    strongest = dict(context.get("strongest_component", {}) or {})
    weakest = dict(context.get("weakest_component", {}) or {})
    common_issues = list(context.get("common_cohort_issues", []) or [])
    confidence_explanation = str(context.get("confidence_explanation") or "").strip()

    if overall_score is not None and cohort_median is not None:
        insights.append(
            {
                "title": "Overall performance",
                "text": (
                    f"Your overall score is {int(round(overall_score))}%, which is "
                    f"{_student_cohort_position_label(overall_score, cohort_median).lower()}."
                ),
            }
        )

    if strongest:
        insights.append(
            {
                "title": "Strongest area",
                "text": (
                    f"Your strongest assessed area was {strongest.get('title')}, with a score of "
                    f"{int(round(float(strongest.get('student_percent') or 0)))}%."
                ),
            }
        )

    if weakest and strongest.get("component") != weakest.get("component"):
        insights.append(
            {
                "title": "Main improvement area",
                "text": (
                    f"Your weakest assessed area was {weakest.get('title')}, where you scored "
                    f"{int(round(float(weakest.get('student_percent') or 0)))}%."
                ),
            }
        )

    if confidence_explanation:
        insights.append({"title": "Confidence", "text": confidence_explanation})

    for issue in common_issues:
        if str(issue.get("student_status") or "") not in {"FAIL", "PARTIAL"}:
            continue
        insights.append(
            {
                "title": "Cohort context",
                "text": (
                    f"{issue.get('label')} is a common cohort issue and affected "
                    f"{int(round(float(issue.get('cohort_missed_percent') or 0)))}% of active submissions."
                ),
            }
        )
        break

    return insights[:5]

def _student_result_label(record: Mapping[str, object]) -> str:
    grade = str(record.get("grade") or "unknown").lower()
    return {
        "full marks": "Strong outcome",
        "good": "Good attempt",
        "partial": "Partial attempt",
        "poor": "Developing attempt",
        "failing": "Limited evidence of completion",
        "unknown": "Result unavailable",
    }.get(grade, "Assignment result")

def _student_summary_line(
    *,
    student_record: Mapping[str, object],
    component_comparison: Sequence[Mapping[str, object]],
    median_percent: float | None,
) -> str:
    strongest = component_comparison[0] if component_comparison else {}
    weakest = component_comparison[-1] if component_comparison else {}
    strongest_title = str(strongest.get("title") or "stronger assessed areas")
    weakest_title = str(weakest.get("title") or "weaker assessed areas")
    overall_percent = (
        round(float(student_record.get("overall", 0) or 0) * 100, 2)
        if isinstance(student_record.get("overall"), (int, float))
        else None
    )
    if overall_percent is None:
        return "Your submission could not be fully scored from the available automated evidence."
    if median_percent is not None and overall_percent >= median_percent:
        return (
            f"Your submission shows stronger evidence in {strongest_title.lower()} and sits at or above the current cohort median overall."
        )
    return (
        f"Your submission shows a partial attempt, with stronger evidence in {strongest_title.lower()} than in {weakest_title.lower()}."
    )

def _student_confidence_explanation(record: Mapping[str, object]) -> str:
    reasons = [str(reason).strip() for reason in list(record.get("confidence_reasons", []) or []) if str(reason).strip()]
    confidence = str(record.get("confidence") or "high").strip().lower()
    if confidence == "high":
        return "High confidence because static, runtime, and browser evidence completed without known limitations."
    if reasons:
        joined = "; ".join(reasons[:3])
        if joined:
            return f"{confidence.capitalize()} confidence because {joined[0].lower() + joined[1:] if len(joined) > 1 else joined.lower()}"
    return f"{confidence.capitalize()} confidence because some automated evidence was incomplete or less reliable."

def _student_cohort_position_label(student_percent: float | None, cohort_median: float | None) -> str:
    if not isinstance(student_percent, (int, float)) or not isinstance(cohort_median, (int, float)):
        return "in the current cohort range"
    if abs(float(student_percent) - float(cohort_median)) <= 2.0:
        return "close to the cohort median"
    if float(student_percent) > float(cohort_median):
        return "above the cohort median"
    return "below the cohort median"

def _student_component_interpretation(student_percent: float | None, cohort_median: float | None) -> str:
    if not isinstance(student_percent, (int, float)):
        return "Not enough evidence to compare"
    if not isinstance(cohort_median, (int, float)):
        return "Compared against the available cohort evidence"
    if abs(float(student_percent) - float(cohort_median)) <= 5.0:
        return "Close to cohort median"
    if float(student_percent) > float(cohort_median):
        return "Above cohort median"
    return "Below cohort median"

def _student_requirement_state(student_record: Mapping[str, object], component: str) -> str:
    rules = dict((student_record.get("required_rules", {}) or {}).get(component, {}) or {})
    if not rules:
        return "NOT_EVALUABLE"
    statuses = {str(rule.get("status") or "").upper() for rule in rules.values()}
    if statuses and statuses <= {"PASS"}:
        return "MET"
    if "WARN" in statuses or "PASS" in statuses:
        return "PARTIAL"
    if statuses == {"SKIPPED"} or ("SKIPPED" in statuses and not statuses.intersection({"PASS", "FAIL", "WARN"})):
        return "SKIPPED"
    return "FAIL"

def _student_requirement_state_label(status: str) -> str:
    normalized = str(status or "").strip().upper()
    return {
        "MET": "Met",
        "PASS": "Met",
        "PARTIAL": "Partial",
        "WARN": "Partial",
        "FAIL": "Not met",
        "SKIPPED": "Not evaluable",
        "NOT_EVALUABLE": "Not evaluable",
    }.get(normalized, "Not evaluable")

def _score_band_label(value: float | None) -> str:
    if not isinstance(value, (int, float)):
        return "Not scored"
    if value >= 70:
        return "Strong"
    if value >= 50:
        return "Secure partial"
    if value > 0:
        return "Below secure partial"
    return "No score"

def _label_for_identifier(identifier: str) -> str:
    label, _ = FINDING_LABELS.get(identifier, ("", ""))
    return label or identifier.replace(".", " / ").replace("_", " ").strip().title()

def _description_for_identifier(identifier: str) -> str:
    _, description = FINDING_LABELS.get(identifier, ("", ""))
    return description

def _first_non_empty(values: Sequence[object]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""

def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
