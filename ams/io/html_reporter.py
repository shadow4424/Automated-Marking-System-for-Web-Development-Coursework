"""HTML Report Generator for Assessment Results.

This module generates beautiful, user-friendly HTML dashboards from
assessment JSON data, including:
- Circular score indicator
- Feedback cards for failed rules
- AI feedback badges
- Vision analysis with screenshot display

Usage:
    from ams.io.html_reporter import HTMLReporter
    
    reporter = HTMLReporter()
    html_path = reporter.generate(report_data, output_path)
"""
from __future__ import annotations

import html
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HTMLReporter:
    """Generates HTML assessment reports from JSON data."""
    
    def __init__(self) -> None:
        self.template = self._get_template()
    
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
        # Extract data
        metadata = report_data.get("metadata", {})
        findings = report_data.get("findings", [])
        score_evidence = report_data.get("score_evidence", {})
        llm_analysis = score_evidence.get("llm_analysis", {})
        
        # Calculate score
        final_score = score_evidence.get("final_score", 0)
        max_score = score_evidence.get("max_score", 100)
        percentage = (final_score / max_score * 100) if max_score > 0 else 0
        
        # Determine grade color
        if percentage >= 70:
            grade_color = "#4CAF50"  # Green
            grade_class = "excellent"
        elif percentage >= 50:
            grade_color = "#FF9800"  # Orange
            grade_class = "good"
        else:
            grade_color = "#f44336"  # Red
            grade_class = "needs-work"
        
        # Build component sections
        findings_html = self._build_findings_section(findings, llm_analysis)
        vision_html = self._build_vision_section(llm_analysis, screenshot_path, output_path)
        stats_html = self._build_stats_section(findings, llm_analysis)
        
        # Format timestamp
        timestamp = metadata.get("timestamp", datetime.now().isoformat())
        
        # Fill template
        html_content = self.template.format(
            profile=html.escape(str(metadata.get("profile", "N/A"))),
            scoring_mode=html.escape(str(metadata.get("scoring_mode", "N/A"))),
            timestamp=html.escape(str(timestamp)),
            final_score=f"{final_score:.1f}",
            max_score=f"{max_score:.1f}",
            percentage=f"{percentage:.0f}",
            grade_color=grade_color,
            grade_class=grade_class,
            findings_section=findings_html,
            vision_section=vision_html,
            stats_section=stats_html,
        )
        
        # Write file
        output_path = Path(output_path)
        if output_path.is_dir():
            html_path = output_path / "report.html"
        else:
            html_path = output_path
        
        html_path.write_text(html_content, encoding="utf-8")
        logger.info(f"Generated HTML report: {html_path}")
        
        return html_path
    
    def _build_findings_section(
        self,
        findings: List[Dict],
        llm_analysis: Dict,
    ) -> str:
        """Build the findings/feedback cards section."""
        # Filter to failed findings (include SKIPPED for completeness)
        shown_findings = [
            f for f in findings 
            if f.get("severity") in ("FAIL", "WARN", "SKIPPED", "fail", "warn", "skipped")
        ]
        
        if not shown_findings:
            return '<p class="no-issues">No issues found! Great work!</p>'
        
        cards_html = []
        for finding in shown_findings:  # Show all findings, no 10-finding cap
            finding_id = finding.get("id", "Unknown")
            category = finding.get("category", "Unknown")
            message = finding.get("message", "No details")
            severity = finding.get("severity", "WARN")
            evidence = finding.get("evidence", {})
            
            # Read LLM feedback from inline evidence (new approach)
            llm_feedback = evidence.get("llm_feedback", {})
            
            # Format feedback from inline evidence
            feedback_html = ""
            if isinstance(llm_feedback, dict) and llm_feedback:
                feedback_text = (
                    llm_feedback.get("summary") or 
                    llm_feedback.get("evidence") or 
                    llm_feedback.get("reason") or 
                    str(llm_feedback)
                )
                if feedback_text:
                    feedback_html = f'''
                    <div class="ai-feedback">
                        <span class="ai-badge">🤖 AI Feedback</span>
                        <p>{html.escape(str(feedback_text)[:500])}</p>
                    </div>
                    '''
            
            severity_class = "severe" if severity.upper() == "FAIL" else ("skipped" if severity.upper() == "SKIPPED" else "warning")
            
            card = f'''
            <div class="finding-card {severity_class}">
                <div class="finding-header">
                    <span class="finding-id">{html.escape(finding_id)}</span>
                    <span class="finding-category">{html.escape(category.upper())}</span>
                    <span class="severity-badge {severity_class}">{html.escape(severity.upper())}</span>
                </div>
                <p class="finding-message">{html.escape(message)}</p>
                {feedback_html}
            </div>
            '''
            cards_html.append(card)
        
        return "\n".join(cards_html)
    
    def _build_vision_section(
        self,
        llm_analysis: Dict,
        screenshot_path: Optional[Path],
        output_path: Optional[Path] = None,
    ) -> str:
        """Build the vision analysis section."""
        vision_items = llm_analysis.get("vision_analysis", [])
        
        if not vision_items:
            return '<p class="no-vision">No vision analysis performed.</p>'
        
        sections = []
        for item in vision_items:
            finding_id = item.get("finding_id", "Unknown")
            result = item.get("result", {})
            status = result.get("status", "UNKNOWN")
            reason = result.get("reason", "No details provided")
            issues = result.get("issues", [])
            screenshot_name = item.get("screenshot", "screenshot.png")
            
            status_class = "pass" if status == "PASS" else "fail" if status == "FAIL" else "unknown"
            
            # Screenshot display — embed as base64 when available
            img_html = ""
            resolved_screenshot = None
            if screenshot_path and screenshot_path.exists():
                resolved_screenshot = screenshot_path
            elif screenshot_name and output_path:
                # Try finding screenshot relative to the output directory
                candidate = Path(output_path) / screenshot_name
                if not candidate.exists():
                    candidate = Path(output_path) / "submission" / screenshot_name
                if candidate.exists():
                    resolved_screenshot = candidate

            if resolved_screenshot and resolved_screenshot.exists():
                import base64
                try:
                    raw = resolved_screenshot.read_bytes()
                    b64 = base64.b64encode(raw).decode("ascii")
                    suffix = resolved_screenshot.suffix.lower()
                    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
                    img_html = f'<img src="data:{mime};base64,{b64}" alt="Page Screenshot" class="vision-screenshot">'
                except Exception:
                    img_html = f'<img src="{screenshot_name}" alt="Page Screenshot" class="vision-screenshot" onerror="this.style.display=\'none\'">'
            elif screenshot_name:
                img_html = f'<img src="{screenshot_name}" alt="Page Screenshot" class="vision-screenshot" onerror="this.style.display=\'none\'">'
            
            # Build issues list
            issues_html = ""
            if issues:
                items = []
                for issue in issues:
                    desc = html.escape(issue.get("description", ""))
                    sev = html.escape(issue.get("severity", "WARN"))
                    items.append(f'<li><span class="severity-badge {"severe" if sev == "FAIL" else "warning"}">{sev}</span> {desc}</li>')
                issues_html = '<ul class="vision-issues">' + "\n".join(items) + '</ul>'

            section = f'''
            <div class="vision-item">
                <div class="vision-result {status_class}">
                    <h4>Vision Check: {html.escape(finding_id)}</h4>
                    <span class="vision-status {status_class}">{html.escape(status)}</span>
                </div>
                <p class="vision-reason">{html.escape(reason)}</p>
                {issues_html}
                {img_html}
            </div>
            '''
            sections.append(section)
        
        return "\n".join(sections)
    
    def _build_stats_section(
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
        vision_count = len(llm_analysis.get("vision_analysis", []))
        partial_count = len(llm_analysis.get("partial_credit", []))
        
        return f'''
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
                <span class="stat-value">{vision_count}</span>
                <span class="stat-label">👁️ Vision Checks</span>
            </div>
            <div class="stat-card ai">
                <span class="stat-value">{partial_count}</span>
                <span class="stat-label">💡 Partial Credits</span>
            </div>
        </div>
        '''
    
    def _get_template(self) -> str:
        """Return the HTML template."""
        return '''<!DOCTYPE html>
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
            color: var(--text);
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 2rem;
        }}
        
        header {{
            background: linear-gradient(135deg, var(--primary) 0%, #764ba2 100%);
            color: white;
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
            color: {grade_color};
        }}
        
        .score-label {{
            font-size: 0.9rem;
            color: var(--text-light);
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
            color: var(--text-light);
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
            border-left-color: var(--danger);
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
            color: var(--primary);
        }}
        
        .finding-category {{
            font-size: 0.8rem;
            padding: 0.2rem 0.6rem;
            background: #e8eaf6;
            border-radius: 4px;
            color: var(--primary-dark);
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
            color: var(--danger);
        }}
        
        .severity-badge.warning {{
            background: #fff3e0;
            color: var(--warning);
        }}
        
        .finding-message {{
            color: var(--text);
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
            color: white;
            font-size: 0.75rem;
            padding: 0.2rem 0.6rem;
            border-radius: 4px;
            margin-bottom: 0.5rem;
        }}
        
        .ai-feedback p {{
            font-size: 0.9rem;
            color: #4a148c;
        }}
        
        /* Vision Section */
        .vision-item {{
            background: var(--card-bg);
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        
        .vision-result {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }}
        
        .vision-status {{
            font-size: 1rem;
            font-weight: bold;
            padding: 0.3rem 1rem;
            border-radius: 20px;
        }}
        
        .vision-status.pass {{
            background: #e8f5e9;
            color: var(--success);
        }}
        
        .vision-status.fail {{
            background: #ffebee;
            color: var(--danger);
        }}
        
        .vision-reason {{
            color: var(--text-light);
            margin-bottom: 1rem;
        }}
        
        .vision-screenshot {{
            max-width: 100%;
            max-height: 300px;
            border-radius: 8px;
            border: 1px solid var(--border);
            margin-top: 1rem;
        }}
        
        .no-issues, .no-vision {{
            text-align: center;
            padding: 2rem;
            background: #e8f5e9;
            border-radius: 8px;
            color: var(--success);
            font-size: 1.1rem;
        }}
        
        .no-vision {{
            background: #f5f5f5;
            color: var(--text-light);
        }}
        
        footer {{
            text-align: center;
            padding: 2rem;
            margin-top: 2rem;
            color: var(--text-light);
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
        
        <section>
            <h2 class="section-header">👁️ Vision Analysis</h2>
            {vision_section}
        </section>
        
        <footer>
            <p>Generated by the Hybrid Assessment System</p>
            <p>Powered by Static Analysis + LLM Feedback + Vision AI</p>
        </footer>
    </div>
</body>
</html>
'''


__all__ = ["HTMLReporter"]
