"""HTML Report Generator for Assessment Results.

This module generates beautiful, user-friendly HTML dashboards from
assessment JSON data, including:
- Circular score indicator
- Feedback cards for failed rules
- AI feedback badges
- Statistics and check summaries

Usage:
    from ams.io.html_reporter import HTMLReporter

    reporter = HTMLReporter()
    html_path = reporter.generate(report_data, output_path)
"""
from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


HTML_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Assessment Report</title>
    <style>
        :root {{
            --primary: #667eea;
            --primary-dark: #5a6fd6;
            --success: #4CAF50;
            --warning: #FF9800;
            --danger: #f44336;
            --bg: #f5f7fa;
            --card-bg: #ffffff;
            --text: #333;
            --text-light: #666;
            --border: #e0e0e0;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: var(--bg);
            colour: var(--text);
            line-height: 1.6;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }}

        header {{
            background: linear-gradient(135deg, var(--primary) 0%, #764ba2 100%);
            colour: white;
            padding: 2rem;
            text-align: center;
            margin-bottom: 2rem;
            border-radius: 12px;
        }}

        header h1 {{
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }}

        .metadata {{
            display: flex;
            justify-content: center;
            gap: 2rem;
            flex-wrap: wrap;
            font-size: 0.9rem;
            opacity: 0.9;
        }}

        /* Score Circle */
        .score-section {{
            display: flex;
            justify-content: center;
            margin-bottom: 2rem;
        }}

        .score-circle {{
            width: 200px;
            height: 200px;
            border-radius: 50%;
            background: conic-gradient(
                {grade_color} calc({percentage} * 1%),
                #e0e0e0 0
            );
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }}

        .score-inner {{
            width: 160px;
            height: 160px;
            border-radius: 50%;
            background: var(--card-bg);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}

        .score-value {{
            font-size: 3rem;
            font-weight: bold;
            colour: {grade_color};
        }}

        .score-label {{
            font-size: 0.9rem;
            colour: var(--text-light);
        }}

        /* Stats Grid */
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }}

        .stat-card {{
            background: var(--card-bg);
            padding: 1.5rem;
            border-radius: 8px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}

        .stat-card.success {{ border-left: 4px solid var(--success); }}
        .stat-card.danger {{ border-left: 4px solid var(--danger); }}
        .stat-card.warning {{ border-left: 4px solid var(--warning); }}
        .stat-card.ai {{ border-left: 4px solid var(--primary); }}

        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
            display: block;
        }}

        .stat-label {{
            font-size: 0.85rem;
            colour: var(--text-light);
        }}

        /* Section Headers */
        .section-header {{
            font-size: 1.5rem;
            margin: 2rem 0 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid var(--primary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        /* Finding Cards */
        .finding-card {{
            background: var(--card-bg);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
            border-left: 4px solid var(--warning);
        }}

        .finding-card.severe {{
            border-left-colour: var(--danger);
        }}

        .finding-header {{
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: 0.75rem;
            flex-wrap: wrap;
        }}

        .finding-id {{
            font-weight: bold;
            colour: var(--primary);
        }}

        .finding-category {{
            font-size: 0.8rem;
            padding: 0.2rem 0.6rem;
            background: #e8eaf6;
            border-radius: 4px;
            colour: var(--primary-dark);
        }}

        .severity-badge {{
            font-size: 0.75rem;
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-weight: bold;
            margin-left: auto;
        }}

        .severity-badge.severe {{
            background: #ffebee;
            colour: var(--danger);
        }}

        .severity-badge.warning {{
            background: #fff3e0;
            colour: var(--warning);
        }}

        .finding-message {{
            colour: var(--text);
            margin-bottom: 1rem;
        }}

        .ai-feedback {{
            background: #f3e5f5;
            padding: 1rem;
            border-radius: 6px;
            margin-top: 0.5rem;
        }}

        .ai-badge {{
            display: inline-block;
            background: #9c27b0;
            colour: white;
            font-size: 0.75rem;
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            margin-bottom: 0.5rem;
        }}

        .ai-feedback p {{
            font-size: 0.9rem;
            colour: #4a148c;
        }}

        /* Vision Section - Removed (legacy) */

        .no-issues {{
            text-align: center;
            padding: 2rem;
            background: #e8f5e9;
            border-radius: 8px;
            colour: var(--success);
            font-size: 1.1rem;
        }}

        footer {{
            text-align: center;
            padding: 2rem;
            margin-top: 2rem;
            colour: var(--text-light);
            font-size: 0.9rem;
        }}

        @media (max-width: 768px) {{
            .container {{
                padding: 1rem;
            }}

            .score-circle {{
                width: 150px;
                height: 150px;
            }}

            .score-inner {{
                width: 120px;
                height: 120px;
            }}

            .score-value {{
                font-size: 2rem;
            }}

            .finding-header {{
                flex-direction: column;
                align-items: flex-start;
            }}

            .severity-badge {{
                margin-left: 0;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Assessment Report</h1>
            <div class="metadata">
                <span>Profile: {profile}</span>
                <span>Mode: {scoring_mode}</span>
                <span>{timestamp}</span>
            </div>
        </header>

        <section class="score-section">
            <div class="score-circle">
                <div class="score-inner">
                    <span class="score-value">{percentage}%</span>
                    <span class="score-label">{final_score} / {max_score}</span>
                </div>
            </div>
        </section>

        <section>
            <h2 class="section-header">Statistics</h2>
            {stats_section}
        </section>

        <section>
            <h2 class="section-header">Issues & Feedback</h2>
            {findings_section}
        </section>

        <footer>
            <p>Generated by the Hybrid Assessment System</p>
            <p>Powered by Static Analysis + LLM Feedback + Vision AI</p>
        </footer>
    </div>
</body>
</html>
"""


class HTMLReporter:
    """Generates HTML assessment reports from JSON data."""

    def __init__(self) -> None:
        self.template = self._get_template()

    def _extract_report_context(
        self,
        report_data: Dict[str, Any],
    ) -> tuple[Dict[str, Any], List[Dict], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        metadata = report_data.get("metadata", {}) or {}
        if not isinstance(metadata, dict):
            metadata = {}
        findings = report_data.get("findings", []) or []
        if not isinstance(findings, list):
            findings = []
        score_evidence = report_data.get("score_evidence", {}) or {}
        if not isinstance(score_evidence, dict):
            score_evidence = {}
        llm_analysis = score_evidence.get("llm_analysis", {}) or {}
        if not isinstance(llm_analysis, dict):
            llm_analysis = {}
        scores = report_data.get("scores", {}) or {}
        if not isinstance(scores, dict):
            scores = {}
        return metadata, findings, score_evidence, llm_analysis, scores

    def _calculate_score_display(
        self,
        score_evidence: Dict[str, Any],
        scores: Dict[str, Any],
    ) -> tuple[Any, Any, float]:
        overall_summary = score_evidence.get("overall", {})
        final_score = score_evidence.get(
            "final_score",
            overall_summary.get("final", scores.get("overall", 0)),
        )
        max_score = score_evidence.get("max_score", 1.0)
        percentage = (final_score / max_score * 100) if max_score > 0 else 0
        return final_score, max_score, percentage

    def _resolve_grade_style(self, percentage: float) -> tuple[str, str]:
        if percentage >= 70:
            return "#4CAF50", "excellent"
        if percentage >= 50:
            return "#FF9800", "good"
        return "#f44336", "needs-work"

    def _resolve_output_path(self, output_path: Path) -> Path:
        output_path = Path(output_path)
        if output_path.is_dir():
            return output_path / "report.html"
        return output_path

    def _build_template_context(
        self,
        metadata: Dict[str, Any],
        final_score: Any,
        max_score: Any,
        percentage: float,
        grade_color: str,
        grade_class: str,
        findings_html: str,
        stats_html: str,
    ) -> Dict[str, str]:
        timestamp = metadata.get("timestamp", datetime.now().isoformat())
        return {
            "profile": html.escape(str(metadata.get("profile", "N/A"))),
            "scoring_mode": html.escape(str(metadata.get("scoring_mode", "N/A"))),
            "timestamp": html.escape(str(timestamp)),
            "final_score": f"{final_score:.1f}",
            "max_score": f"{max_score:.1f}",
            "percentage": f"{percentage:.0f}",
            "grade_color": grade_color,
            "grade_class": grade_class,
            "findings_section": findings_html,
            "stats_section": stats_html,
        }

    def generate(
        self,
        report_data: Dict[str, Any],
        output_path: Path,
        screenshot_path: Optional[Path] = None,
    ) -> Path:
        """Generate an HTML report from assessment data.

        Args:
            report_data: The assessment report dictionary (from JSON).
            output_path: Directory to write the HTML report.
            screenshot_path: Optional path to screenshot for vision display.

        Returns:
            Path to the generated HTML file.
        """
        metadata, findings, score_evidence, llm_analysis, scores = self._extract_report_context(report_data)
        final_score, max_score, percentage = self._calculate_score_display(score_evidence, scores)
        grade_color, grade_class = self._resolve_grade_style(percentage)
        findings_html = self._render_findings_section(findings, llm_analysis)
        stats_html = self._render_stats_section(findings, llm_analysis)
        html_content = self.template.format(
            **self._build_template_context(
                metadata,
                final_score,
                max_score,
                percentage,
                grade_color,
                grade_class,
                findings_html,
                stats_html,
            )
        )
        html_path = self._resolve_output_path(output_path)
        html_path.write_text(html_content, encoding="utf-8")
        logger.info(f"Generated HTML report: {html_path}")
        return html_path

    def _select_renderable_findings(self, findings: List[Dict]) -> List[Dict]:
        return [
            finding
            for finding in findings
            if finding.get("severity") in ("FAIL", "WARN", "SKIPPED", "fail", "warn", "skipped")
        ]

    def _render_ai_feedback_section(self, finding: Dict[str, Any]) -> str:
        evidence = finding.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}

        llm_feedback = evidence.get("llm_feedback", {})
        if not isinstance(llm_feedback, dict) or not llm_feedback:
            return ""

        feedback_text = (
            llm_feedback.get("summary")
            or llm_feedback.get("evidence")
            or llm_feedback.get("reason")
            or str(llm_feedback)
        )
        if not feedback_text:
            return ""
        return f"""
                    <div class="ai-feedback">
                        <span class="ai-badge">🤖 AI Feedback</span>
                        <p>{html.escape(str(feedback_text)[:500])}</p>
                    </div>
                    """

    def _render_finding_card(self, finding: Dict[str, Any]) -> str:
        finding_id = finding.get("id", "Unknown")
        category = finding.get("category", "Unknown")
        message = finding.get("message", "No details")
        severity = finding.get("severity", "WARN")
        severity_class = (
            "severe"
            if severity.upper() == "FAIL"
            else ("skipped" if severity.upper() == "SKIPPED" else "warning")
        )
        feedback_html = self._render_ai_feedback_section(finding)
        return f"""
            <div class="finding-card {severity_class}">
                <div class="finding-header">
                    <span class="finding-id">{html.escape(finding_id)}</span>
                    <span class="finding-category">{html.escape(category.upper())}</span>
                    <span class="severity-badge {severity_class}">{html.escape(severity.upper())}</span>
                </div>
                <p class="finding-message">{html.escape(message)}</p>
                {feedback_html}
            </div>
            """

    def _render_findings_section(
        self,
        findings: List[Dict],
        llm_analysis: Dict,
    ) -> str:
        """Build the findings/feedback cards section."""
        shown_findings = self._select_renderable_findings(findings)
        if not shown_findings:
            return '<p class="no-issues">No issues found! Great work!</p>'
        return "\n".join(self._render_finding_card(finding) for finding in shown_findings)

    def _render_stats_section(
        self,
        findings: List[Dict],
        llm_analysis: Dict,
    ) -> str:
        """Build the statistics section using aggregated checks."""
        from ams.core.aggregation import aggregate_findings_to_checks, compute_check_stats

        checks, diagnostics = aggregate_findings_to_checks(findings)
        stats = compute_check_stats(checks)

        total = stats["total"]
        passed = stats["passed"]
        failed = stats["failed"]
        warnings = stats["warnings"]
        feedback_count = len(llm_analysis.get("feedback", []))
        partial_count = len(llm_analysis.get("partial_credit", []))

        return f"""
        <div class="stat-grid">
            <div class="stat-card">
                <span class="stat-value">{total}</span>
                <span class="stat-label">Total Checks</span>
            </div>
            <div class="stat-card success">
                <span class="stat-value">{passed}</span>
                <span class="stat-label">Passed</span>
            </div>
            <div class="stat-card danger">
                <span class="stat-value">{failed}</span>
                <span class="stat-label">Failed</span>
            </div>
            <div class="stat-card warning">
                <span class="stat-value">{warnings}</span>
                <span class="stat-label">Warnings</span>
            </div>
        </div>
        <div class="stat-grid ai-stats">
            <div class="stat-card ai">
                <span class="stat-value">{feedback_count}</span>
                <span class="stat-label">🤖 AI Feedback</span>
            </div>
            <div class="stat-card ai">
                <span class="stat-value">{partial_count}</span>
                <span class="stat-label">💡 Partial Credits</span>
            </div>
        </div>
        """

    def _get_template(self) -> str:
        """Return the HTML template."""
        return HTML_REPORT_TEMPLATE


__all__ = ["HTMLReporter"]
