"""Chart and graph helpers for assignment analytics."""
from __future__ import annotations

import statistics
from typing import List, Mapping, Sequence

from ams.analytics.assignment_analytics import FUNCTIONAL_ANALYTICS_STAGES, STATIC_ANALYTICS_STAGES
from ams.analytics.insights import _coerce_float, _first_non_empty

# Imported function to be used in analytics computations, but not directly exposed by this module
def _interactive_graphs(
    *,
    records: List[Mapping[str, object]],
    relevant_components: Sequence[str],
    coverage: Mapping[str, object],
    components: Sequence[Mapping[str, object]],
    requirement_coverage: Sequence[Mapping[str, object]],
    top_failing_rules: Sequence[Mapping[str, object]],
    reliability: Mapping[str, object],
) -> dict:
    student_index = {
        str(record.get("student_id") or ""): _student_graph_snapshot(record)
        for record in records
        if str(record.get("student_id") or "").strip()
    }
    # Build histogram data for overall mark distribution
    histogram = _build_mark_distribution_histogram(records)

    # Build component performance distribution data
    component_rows: List[dict] = []
    for component_summary in components:
        component = str(component_summary.get("component") or "")
        if not component:
            continue
        state_students = {
            "zero": [],
            "half": [],
            "full": [],
            "other": [],
            "not_scored": [],
        }

        # Track related rule IDs that contributed to this component's performance for potential drill-downs or insights
        related_rule_ids: set[str] = set()
        for record in records:
            student_id = str(record.get("student_id") or "").strip()
            if not student_id:
                continue
            score = (record.get("components", {}) or {}).get(component)
            if score == 0:
                state_students["zero"].append(student_id)
            elif score == 0.5:
                state_students["half"].append(student_id)
            elif score == 1:
                state_students["full"].append(student_id)
            elif isinstance(score, (int, float)):
                state_students["other"].append(student_id)
            else:
                state_students["not_scored"].append(student_id)

            # Finds rules that had negative outcomes related to this component for the student, which may indicate common issues or areas for improvement
            for outcome in record.get("problem_outcomes", []) or []:
                if str(outcome.get("component") or "").lower() == component and str(outcome.get("status") or "") in {"FAIL", "WARN"}:
                    related_rule_ids.add(str(outcome.get("id") or ""))
        
        #Component performance summary
        component_rows.append(
            {
                "component": component,
                "title": str(component_summary.get("title") or component.upper()),
                "average_percent": round(float(component_summary.get("average") or 0) * 100, 2)
                if component_summary.get("average") is not None
                else None,
                "total_evaluable": int(component_summary.get("total_evaluable", 0) or 0),
                "segments": [
                    # This segment shows students who scored 0 on this component 
                    _graph_segment(
                        segment_id=f"{component}_zero",
                        label="Score 0",
                        count=len(state_students["zero"]),
                        total=len(records),
                        student_ids=state_students["zero"],
                    ),
                    # This segment shows students who scored 0.5 on this component
                    _graph_segment(
                        segment_id=f"{component}_half",
                        label="Score 0.5",
                        count=len(state_students["half"]),
                        total=len(records),
                        student_ids=state_students["half"],
                    ),
                    # This segment shows students who scored 1 on this component
                    _graph_segment(
                        segment_id=f"{component}_full",
                        label="Score 1",
                        count=len(state_students["full"]),
                        total=len(records),
                        student_ids=state_students["full"],
                    ),
                    # This segment captures any students who had partal credit
                    _graph_segment(
                        segment_id=f"{component}_other",
                        label="Other scored states",
                        count=len(state_students["other"]),
                        total=len(records),
                        student_ids=state_students["other"],
                    ),
                    # This segment captures students who were not scored on this component
                    _graph_segment(
                        segment_id=f"{component}_not_scored",
                        label="Not scored",
                        count=len(state_students["not_scored"]),
                        total=len(records),
                        student_ids=state_students["not_scored"],
                    ),
                ],
                "related_rule_ids": sorted(rule_id for rule_id in related_rule_ids if rule_id),
            }
        )

    # Build requirement coverage matrix data
    requirement_rows: List[dict] = []
    for row in requirement_coverage:
        component = str(row.get("component") or "")
        if not component:
            continue
        requirement_rows.append(
            {
                "component": component,
                "title": str(row.get("title") or component.upper()),
                "rule_count": int(row.get("rule_count", 0) or 0),
                "cells": [
                    # This segment shows students who fully met the requirement
                    _graph_segment(
                        segment_id=f"{component}_met",
                        label="Met",
                        count=int(row.get("students_met", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("met_students", []) or []),
                    ),
                    # This segment shows students who partially met the requirement
                    _graph_segment(
                        segment_id=f"{component}_partial",
                        label="Partial",
                        count=int(row.get("students_partial", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("partial_students", []) or []),
                    ),
                    # This segment shows students who did not meet the requirement at all
                    _graph_segment(
                        segment_id=f"{component}_unmet",
                        label="Unmet",
                        count=int(row.get("students_unmet", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("unmet_students", []) or []),
                    ),
                    # This segment captures students whose requirement status was not evaluated
                    _graph_segment(
                        segment_id=f"{component}_not_evaluable",
                        label="Not evaluable",
                        count=int(row.get("students_not_evaluable", 0) or 0),
                        total=len(records),
                        student_ids=list(row.get("not_evaluable_students", []) or []),
                    ),
                ],
            }
        )

    # Holds breakdowns of evaluation states and confidence levels
    reliability_groups = [
        {
            "id": "evaluation_state",
            "label": "Evaluation state",
            "segments": [
                # This segment shows students whose submissions were fully evaluated without major issues
                _graph_segment(
                    segment_id="fully_evaluated",
                    label="Fully evaluated",
                    count=int(reliability.get("fully_evaluated", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "fully_evaluated" and str(record.get("student_id") or "").strip()
                    ],
                ),
                # This segment shows students whose submissions were only partially evaluated
                _graph_segment(
                    segment_id="partially_evaluated",
                    label="Partially evaluated",
                    count=int(reliability.get("partially_evaluated", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "partially_evaluated" and str(record.get("student_id") or "").strip()
                    ],
                ),
                # This segment captures students whose submissions could not be analysed at all
                _graph_segment(
                    segment_id="not_analysable",
                    label="Not analysable",
                    count=int(reliability.get("not_analysable", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("evaluation_state") == "not_analysable" and str(record.get("student_id") or "").strip()
                    ],
                ),
            ],
        },
        # This group breaks down the confidence levels assigned to each submission
        {
            "id": "confidence",
            "label": "Confidence level",
            "segments": [
                # This segment shows students whose submissions were evaluated with high confidence
                _graph_segment(
                    segment_id="confidence_high",
                    label="High confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("high", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "high" and str(record.get("student_id") or "").strip()
                    ],
                ),
                # This segment shows students whose submissions were evaluated with medium confidence
                _graph_segment(
                    segment_id="confidence_medium",
                    label="Medium confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("medium", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "medium" and str(record.get("student_id") or "").strip()
                    ],
                ),
                # This segment shows students whose submissions were evaluated with low confidence
                _graph_segment(
                    segment_id="confidence_low",
                    label="Low confidence",
                    count=int((reliability.get("confidence", {}) or {}).get("low", 0) or 0),
                    total=len(records),
                    student_ids=[
                        str(record.get("student_id") or "")
                        for record in records
                        if record.get("confidence") == "low" and str(record.get("student_id") or "").strip()
                    ],
                ),
            ],
        },
    ]

    # This group captures specific limitations or issues that impacted the evaluation reliability
    limitation_rows = [
        # This segment shows students whose submissions were flagged for manual review
        _graph_segment(
            segment_id="manual_review",
            label="Manual review recommended",
            count=int(reliability.get("manual_review", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("manual_review_recommended") and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment shows students whose submissions had runtime checks skipped or unavailable
        _graph_segment(
            segment_id="runtime_skipped",
            label="Runtime checks skipped or unavailable",
            count=int(reliability.get("runtime_skipped", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("runtime_skipped") and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment shows students whose submissions had browser checks skipped or unavailable
        _graph_segment(
            segment_id="browser_skipped",
            label="Browser checks skipped or unavailable",
            count=int(reliability.get("browser_skipped", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("browser_skipped") and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment captures students whose submissions had runtime issues that may have impacted evaluation reliability
        _graph_segment(
            segment_id="runtime_issue",
            label="Runtime failures or timeouts",
            count=int(reliability.get("runtime_issue_submissions", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("runtime_issue") and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment captures students whose submissions had browser issues that may have impacted evaluation reliability
        _graph_segment(
            segment_id="browser_issue",
            label="Browser failures, timeouts, or console errors",
            count=int(reliability.get("browser_issue_submissions", 0) or 0),
            total=len(records),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if (record.get("runtime_flags", {}) or {}).get("browser_issue") and str(record.get("student_id") or "").strip()
            ],
        ),
    ]

    # Plotting data points for static vs behavioural scores scatter plot
    scatter_points: List[dict] = []
    plotted_static_scores: List[float] = []
    plotted_functional_scores: List[float] = []
    static_requirement_support = 0
    functional_requirement_support = 0
    behavioural_evaluable_students = 0
    
    # Iterate through each student record to extract scores and requirement
    for record in records:
        student_id = str(record.get("student_id") or "").strip()
        if not student_id:
            continue
        overall = record.get("overall")
        score_percent = round(float(overall) * 100, 2) if isinstance(overall, (int, float)) else None
        static_axis = _requirement_axis_score(record, STATIC_ANALYTICS_STAGES)
        functional_axis = _requirement_axis_score(record, FUNCTIONAL_ANALYTICS_STAGES)
        # Convert the axis scores to percentage format for plotting
        static_score_percent = (
            round(float(static_axis["score"]) * 100, 2)
            if isinstance(static_axis.get("score"), (int, float))
            else None
        )
        # Convert the axis scores to percentage format for plotting
        behavioural_score_percent = (
            round(float(functional_axis["score"]) * 100, 2)
            if isinstance(functional_axis.get("score"), (int, float))
            else None
        )
        static_requirement_support += int(static_axis.get("requirement_count", 0) or 0)
        functional_requirement_support += int(functional_axis.get("requirement_count", 0) or 0)
        if int(functional_axis.get("evaluable_count", 0) or 0) > 0:
            behavioural_evaluable_students += 1
        if static_score_percent is not None:
            plotted_static_scores.append(static_score_percent)
        if behavioural_score_percent is not None:
            plotted_functional_scores.append(behavioural_score_percent)
        
        # Each point on the scatter plot represents a student's performance, with additional metadata for filtering and insights
        scatter_points.append(
            {
                "id": student_id,
                "student_id": student_id,
                "student_name": str(record.get("student_name") or ""),
                "submission_id": record.get("submission_id"),
                "overall_mark_percent": score_percent,
                "static_score_percent": static_score_percent,
                "behavioural_score_percent": behavioural_score_percent,
                "static_requirement_count": int(static_axis.get("requirement_count", 0) or 0),
                "behavioural_requirement_count": int(functional_axis.get("requirement_count", 0) or 0),
                "behavioural_evaluable_count": int(functional_axis.get("evaluable_count", 0) or 0),
                "functional_evidence_limited": int(functional_axis.get("evaluable_count", 0) or 0) == 0,
                "manual_review_recommended": bool(record.get("manual_review_recommended")),
                "confidence": str(record.get("confidence") or "high"),
                "severity": str(record.get("severity") or "low"),
                "matched_rule_count": len(list(record.get("matched_rule_ids", []) or [])),
                "primary_issue": _first_non_empty(
                    [
                        record.get("reason_detail"),
                        record.get("review_note"),
                        record.get("reason"),
                    ]
                ),
                "report_available": bool(record.get("run_id")),
                "student_ids": [student_id],
            }
        )

    # The scatter plot is only supported if there are enough records and evidence
    scatter_supported = bool(
        records
        and static_requirement_support > 0
        and functional_requirement_support > 0
        and behavioural_evaluable_students >= 2
    )
    scatter_reason = ""
    if not records:
        scatter_reason = "Scatter plot data will appear once assignment submissions are available."
    elif functional_requirement_support == 0:
        scatter_reason = "This chart is hidden because the selected assignment profile does not include enough runtime or browser evidence."
    elif behavioural_evaluable_students < 2:
        scatter_reason = "Not enough behavioural evidence to plot this view for the current assignment."
    elif static_requirement_support == 0:
        scatter_reason = "Static and code-quality evidence is not available for the current assignment."

    # Build coverage breakdown data for missing/incomplete submissions
    coverage_rows = [
        # This segment captures the total number of students assigned to the assignment
        _graph_segment(
            segment_id="assigned_students",
            label="Assigned students",
            count=int(coverage.get("assigned_students", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("assigned_student_ids", []) or []),
        ),
        # This segment shows students who actively submitted work that was within the scope of evaluation
        _graph_segment(
            segment_id="active_in_scope",
            label="Active submissions in scope",
            count=int(coverage.get("active_in_scope", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("active_student_ids", []) or []),
        ),
        # This segment captures students who were assigned but did not submit any work or whose submissions were not captured for evaluation
        _graph_segment(
            segment_id="missing_assigned",
            label="Assigned but not submitted",
            count=int(coverage.get("missing_assigned", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("missing_students", []) or []),
        ),
        # This segment captures students who submitted work but it could not be analysed due to issues
        _graph_segment(
            segment_id="submitted_not_analysable",
            label="Submitted but not analysable",
            count=int(coverage.get("not_analysable", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "not_analysable" and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment captures students whose submissions were excluded from evaluation or superseded by later submissions
        _graph_segment(
            segment_id="excluded_or_superseded",
            label="Excluded or superseded",
            count=int(coverage.get("inactive_or_superseded", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=list(coverage.get("inactive_or_superseded_students", []) or []),
        ),
        # This segment shows students whose submissions were fully evaluated and contributed to the final marks
        _graph_segment(
            segment_id="fully_evaluated_coverage",
            label="Fully evaluated",
            count=int(coverage.get("fully_evaluated", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "fully_evaluated" and str(record.get("student_id") or "").strip()
            ],
        ),
        # This segment shows students whose submissions were only partially evaluated
        _graph_segment(
            segment_id="partially_evaluated_coverage",
            label="Partially evaluated",
            count=int(coverage.get("partially_evaluated", 0) or 0),
            total=int(coverage.get("assigned_students", 0) or 0),
            student_ids=[
                str(record.get("student_id") or "")
                for record in records
                if record.get("evaluation_state") == "partially_evaluated" and str(record.get("student_id") or "").strip()
            ],
        ),
    ]

    # Compile all the graph data into a single structure to be consumed by the frontend for rendering interactive analytics dashboards
    return {
        "student_index": student_index,
        "mark_distribution_histogram": {
            "total_students": len(records),
            "scored_students": histogram["scored_students"],
            "unscored_submissions": histogram["unscored_submissions"],
            "bin_width": histogram["bin_width"],
            "x_ticks": [0, 20, 40, 60, 80, 100],
            "primary_reference": {
                "key": "mean_percent",
                "label": "Mean",
                "value": histogram["mean_percent"],
                "detail": "Cohort mean mark across the active submissions in scope.",
            },
            "summary_stats": {
                "mean_percent": histogram["mean_percent"],
                "median_percent": histogram["median_percent"],
                "pass_threshold_percent": 50,
            },
            "reference_lines": {
                "mean_percent": histogram["mean_percent"],
                "median_percent": histogram["median_percent"],
                "pass_threshold_percent": 50,
            },
            "bins": histogram["bins"],
        },
        "component_performance_distribution": {
            "components": component_rows,
            "relevant_components": list(relevant_components),
        },
        "requirement_coverage_matrix": {
            "states": ["Met", "Partial", "Unmet", "Not evaluable"],
            "rows": requirement_rows,
        },
        "top_failing_rules_chart": {
            "rules": list(top_failing_rules[: min(len(top_failing_rules), 10)]),
        },
        "confidence_reliability_breakdown": {
            "groups": reliability_groups,
            "limitation_rows": limitation_rows,
        },
        "static_functional_scatter_plot": {
            "x_label": "Static / Code Quality Score",
            "y_label": "Behavioural / Functional Score",
            "supported": scatter_supported,
            "unsupported_reason": scatter_reason,
            "cohort_count": len(records),
            "behavioural_evaluable_students": behavioural_evaluable_students,
            "reference_lines": {
                "static_mean_percent": round(statistics.mean(plotted_static_scores), 2) if scatter_supported and plotted_static_scores else None,
                "behavioural_mean_percent": round(statistics.mean(plotted_functional_scores), 2) if scatter_supported and plotted_functional_scores else None,
                "show_mean_lines": scatter_supported and len(records) >= 4,
                "show_balance_diagonal": scatter_supported,
            },
            "points": scatter_points,
        },
        "missing_incomplete_submission_coverage_chart": {
            "stages": coverage_rows,
        },
    }
# Function to build a histogram of mark distribution based on the overall scores of student records
def _build_mark_distribution_histogram(records: Sequence[Mapping[str, object]]) -> dict:
    scored_records: List[dict[str, object]] = []
    for record in records:
        overall = record.get("overall")
        if not isinstance(overall, (int, float)):
            continue
        percent = max(0.0, min(100.0, float(overall) * 100))
        student_id = str(record.get("student_id") or "").strip()
        scored_records.append({"student_id": student_id, "percent": percent})

    # Sets width of histogram bins based on the number of scored records
    scored_count = len(scored_records)
    bin_width = 5 if scored_count >= 20 else 10
    bins: List[dict] = []

    # Create width for the histogram and count how many students fall into each percentage range
    for start in range(0, 100, bin_width):
        end = min(start + bin_width, 100)
        student_ids = [
            str(item.get("student_id") or "")
            for item in scored_records
            if (
                start <= float(item.get("percent") or 0) <= 100
                if end >= 100
                else start <= float(item.get("percent") or 0) < end
            )
            and str(item.get("student_id") or "").strip()
        ]
        bins.append(
            {
                "id": f"band_{start}_{end}",
                "label": f"{start}-{end}%",
                "range_min": start,
                "range_max": end,
                "count": len(student_ids),
                "percent": (len(student_ids) / len(records) * 100) if records else 0,
                "student_ids": student_ids,
            }
        )

    # Extract the percentage marks for scored records to calculate mean and median for reference lines on the histogram
    scored_marks = [float(item["percent"]) for item in scored_records]
    return {
        "scored_students": scored_count,
        "unscored_submissions": sum(1 for record in records if not isinstance(record.get("overall"), (int, float))),
        "bin_width": bin_width,
        "mean_percent": round(statistics.mean(scored_marks), 2) if scored_marks else None,
        "median_percent": round(statistics.median(scored_marks), 2) if scored_marks else None,
        "bins": bins,
    }

# Creates a snapshot of a student's performance and evaluation details for use in interactive graphs and drill-downs, extracting key metrics and metadata from the record
def _student_graph_snapshot(record: Mapping[str, object]) -> dict:
    overall = record.get("overall")
    static_axis = _requirement_axis_score(record, STATIC_ANALYTICS_STAGES)
    functional_axis = _requirement_axis_score(record, FUNCTIONAL_ANALYTICS_STAGES)
    return {
        "student_id": str(record.get("student_id") or ""),
        "student_name": str(record.get("student_name") or ""),
        "submission_id": str(record.get("submission_id") or ""),
        "score_percent": round(float(overall) * 100, 2) if isinstance(overall, (int, float)) else None,
        "static_score_percent": (
            round(float(static_axis["score"]) * 100, 2)
            if isinstance(static_axis.get("score"), (int, float))
            else None
        ),
        "behavioural_score_percent": (
            round(float(functional_axis["score"]) * 100, 2)
            if isinstance(functional_axis.get("score"), (int, float))
            else None
        ),
        "grade": str(record.get("grade") or "unknown"),
        "confidence": str(record.get("confidence") or "high"),
        "evaluation_state": str(record.get("evaluation_state") or "fully_evaluated"),
        "severity": str(record.get("severity") or "low"),
        "manual_review_recommended": bool(record.get("manual_review_recommended")),
        "primary_issue": _first_non_empty(
            [
                record.get("reason_detail"),
                record.get("review_note"),
                record.get("reason"),
            ]
        ),
        "reason": str(record.get("reason") or ""),
        "reason_detail": str(record.get("reason_detail") or ""),
        "flags": list(record.get("flags", []) or []),
        "matched_rule_ids": list(record.get("matched_rule_ids", []) or []),
        "matched_rule_labels": list(record.get("matched_rule_labels", []) or []),
        "run_id": str(record.get("run_id") or ""),
        "source_mode": str(record.get("source_mode") or ""),
    }

# Defining segments for the coverage breakdown chart
def _graph_segment(
    *,
    segment_id: str,
    label: str,
    count: int,
    total: int,
    student_ids: Sequence[str],
) -> dict:
    clean_students = sorted({str(student_id).strip() for student_id in student_ids if str(student_id).strip()})
    return {
        "id": segment_id,
        "label": label,
        "count": int(count),
        "percent": (int(count) / total * 100) if total else 0,
        "student_ids": clean_students,
    }

# Calculates a score for the requirement axis
def _requirement_axis_score(
    record: Mapping[str, object],
    stages: Sequence[str],
) -> dict:
    stage_set = {str(stage or "").strip().lower() for stage in stages if str(stage or "").strip()}
    requirements = list((record.get("score_evidence", {}) or {}).get("requirements", []) or [])
    total_weight = 0.0
    weighted_score = 0.0
    evaluable_count = 0
    requirement_count = 0

    # Iterate through each requirement in the student's score evidence to calculate a weighted score
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        if requirement.get("required") is False:
            continue
        stage = str(requirement.get("stage") or "").strip().lower()
        if stage not in stage_set:
            continue
        requirement_count += 1
        score = _requirement_numeric_score(requirement)
        weight = _coerce_float(requirement.get("weight"))
        if weight is None or weight <= 0:
            weight = 1.0
        if score is None:
            score = 0.0
        else:
            evaluable_count += 1
        total_weight += weight
        weighted_score += score * weight

    return {
        "score": (weighted_score / total_weight) if total_weight > 0 else None,
        "requirement_count": requirement_count,
        "evaluable_count": evaluable_count,
    }

# Converts various representations of requirement scores into a standardised numeric format
def _requirement_numeric_score(requirement: Mapping[str, object]) -> float | None:
    raw_score = _coerce_float(requirement.get("score"))
    if raw_score is not None:
        return max(0.0, min(1.0, raw_score))

    status = str(requirement.get("status") or "").strip().upper()
    if status == "PASS":
        return 1.0
    if status == "PARTIAL":
        return 0.5
    if status == "FAIL":
        return 0.0
    if status == "SKIPPED":
        return None
    return None

# Categorises rules into broader buckets based on their identifiers
def _rule_category(rule_id: str) -> str:
    identifier = str(rule_id or "").upper()
    if identifier.startswith("BROWSER."):
        return "browser/runtime"
    if identifier.startswith("BEHAVIOUR."):
        return "behavioural/runtime"
    if identifier.startswith("CONSISTENCY."):
        return "consistency"
    if ".QUALITY." in identifier:
        return "quality"
    if ".SECURITY." in identifier:
        return "security"
    if ".REQ." in identifier or ".MISSING_FILES" in identifier:
        return "structure"
    if identifier == "SUBMISSION.NOT_ANALYSABLE":
        return "confidence/runner limitation"
    return "other"

# This function breaks down the composition of a student's score into different sources of evidence and issues
def _score_composition(records: List[Mapping[str, object]], total: int) -> List[dict]:
    sources = [
        # Source for static analysis evidence
        {
            "id": "static_analysis",
            "label": "Static analysis",
            "description": "Required rules, structural checks, and static rubric findings that contribute baseline evidence.",
            "predicate": lambda record: bool(record.get("required_rules")) or int((record.get("check_stats") or {}).get("total", 0)) > 0,
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if not str(outcome.get("id") or "").startswith(("BEHAVIOUR.", "BROWSER.", "CONSISTENCY."))
                and ".QUALITY." not in str(outcome.get("id") or "")
                and ".SECURITY." not in str(outcome.get("id") or "")
                and str(outcome.get("id") or "") != "submission.not_analysable"
            ],
            "skipped_incidents": lambda record: 0,
            "confidence_reduced": lambda record: record.get("status") != "ok" and (
                bool(record.get("required_rules")) or int((record.get("check_stats") or {}).get("total", 0)) > 0
            ),
        },
        # Source for behavioural and runtime checks
        {
            "id": "runtime_checks",
            "label": "Behavioural and runtime checks",
            "description": "Runtime execution checks that validate backend behaviour or deterministic execution paths.",
            "predicate": lambda record: bool(record.get("behavioural_evidence")) or any(
                str(outcome.get("id") or "").startswith("BEHAVIOUR.") for outcome in record.get("problem_outcomes", []) or []
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("id") or "").startswith("BEHAVIOUR.")
            ],
            "skipped_incidents": lambda record: 1 if record.get("runtime_flags", {}).get("runtime_skipped") else 0,
            "confidence_reduced": lambda record: bool(
                record.get("runtime_flags", {}).get("runtime_skipped")
                or record.get("runtime_flags", {}).get("runtime_issue")
            ),
        },
        # Source for browser-based checks and client-side evidence
        {
            "id": "browser_checks",
            "label": "Browser interaction checks",
            "description": "Browser automation and client-side checks that validate page loading and front-end behaviour.",
            "predicate": lambda record: bool(record.get("browser_evidence")) or any(
                str(outcome.get("id") or "").startswith("BROWSER.") for outcome in record.get("problem_outcomes", []) or []
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("id") or "").startswith("BROWSER.")
            ],
            "skipped_incidents": lambda record: 1 if record.get("runtime_flags", {}).get("browser_skipped") else 0,
            "confidence_reduced": lambda record: bool(
                record.get("runtime_flags", {}).get("browser_skipped")
                or record.get("runtime_flags", {}).get("browser_issue")
            ),
        },
        # Source for penalties and quality checks that may impact the score due to issues or inconsistencies
        {
            "id": "penalties",
            "label": "Penalties and quality checks",
            "description": "Consistency, quality, or security findings that can drag performance down or trigger moderation review.",
            "predicate": lambda record: any(
                token in str(outcome.get("id") or "")
                for outcome in record.get("problem_outcomes", []) or []
                for token in ("CONSISTENCY.", ".QUALITY.", ".SECURITY.")
            ),
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if any(
                    token in str(outcome.get("id") or "")
                    for token in ("CONSISTENCY.", ".QUALITY.", ".SECURITY.")
                )
            ],
            "skipped_incidents": lambda record: 0,
            "confidence_reduced": lambda record: bool(record.get("runtime_flags", {}).get("consistency_issue")),
        },
        # Source for issues related to skipped checks or unavailable evidence that may have reduced confidence in the evaluation
        {
            "id": "skipped_logic",
            "label": "Skipped or unavailable checks",
            "description": "Confidence-reducing gaps where runtime, browser, or full pipeline evaluation was unavailable.",
            "predicate": lambda record: record.get("evaluation_state") != "fully_evaluated",
            "outcomes": lambda record: [
                outcome
                for outcome in record.get("problem_outcomes", []) or []
                if str(outcome.get("status") or "") == "SKIPPED" or str(outcome.get("id") or "") == "submission.not_analysable"
            ],
            "skipped_incidents": lambda record: int(bool(record.get("runtime_flags", {}).get("runtime_skipped")))
            + int(bool(record.get("runtime_flags", {}).get("browser_skipped"))),
            "confidence_reduced": lambda record: record.get("confidence") != "high",
        },
    ]

    # Iterate through each source of evidence and calculate how many students were affected by issues in that category
    rows: List[dict] = []
    for source in sources:
        students: set[str] = set()
        fail_incidents = 0
        warning_incidents = 0
        skipped_incidents = 0
        confidence_reduced_students: set[str] = set()
        for record in records:
            if not source["predicate"](record):
                continue
            student_id = str(record.get("student_id") or "")
            if student_id:
                students.add(student_id)
            outcomes = list(source["outcomes"](record))
            fail_incidents += sum(1 for outcome in outcomes if str(outcome.get("status") or "") == "FAIL")
            warning_incidents += sum(1 for outcome in outcomes if str(outcome.get("status") or "") == "WARN")
            skipped_incidents += int(source["skipped_incidents"](record))
            if source["confidence_reduced"](record) and student_id:
                confidence_reduced_students.add(student_id)

        counted = len(students)
        rows.append(
            {
                "id": source["id"],
                "label": source["label"],
                "description": source["description"],
                "students_affected": counted,
                "submissions_affected": counted,
                "percent": (counted / total * 100) if total else 0,
                "fail_incidents": fail_incidents,
                "warning_incidents": warning_incidents,
                "skipped_incidents": skipped_incidents,
                "confidence_reduced_submissions": len(confidence_reduced_students),
                "examples": sorted(students)[:3],
            }
        )
    return rows
