from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from ams.assessors.base import Assessor
from ams.assessors.behavioral import DeterministicTestEngine, HTMLBehavioralAssessor
from ams.assessors.browser import PlaywrightAssessor
from ams.assessors.consistency import ConsistencyAssessor
from ams.assessors.required import (
    CSSRequiredRulesAssessor,
    HTMLRequiredElementsAssessor,
    JSRequiredFeaturesAssessor,
    PHPRequiredFeaturesAssessor,
    SQLRequiredFeaturesAssessor,
)
from ams.assessors.static import (
    CSSStaticAssessor,
    HTMLStaticAssessor,
    JSStaticAssessor,
    PHPStaticAssessor,
    SQLStaticAssessor,
)
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec, get_profile_spec
from ams.core.scoring import ScoringEngine
from ams.core.config import SCORING_MODE, ScoringMode
from ams.io.reporting import ReportWriter
from ams.io.submission import SubmissionProcessor

# LLM Integration (Phase 1 & 2)
from ams.llm.feedback import generate_feedback
from ams.llm.scoring import evaluate_partial_credit, should_evaluate_partial_credit, HybridScore

# Vision Integration (Phase 3 & C)
from ams.core.config import VISION_ENABLED
from ams.llm.vision_schemas import VisionResult

# Phase D: Conflict Resolution
from ams.core.arbitration import resolve_conflicts

logger = logging.getLogger(__name__)


class AssessmentPipeline:
    """Orchestrates assessors, scoring, and reporting.
    
    LLM Integration:
    - If SCORING_MODE includes LLM, failed findings are enriched with LLM feedback
    - Partial credit is evaluated for rules that allow it
    """

    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
        scoring_mode: ScoringMode = SCORING_MODE,
    ) -> None:
        self.assessors: Optional[List[Assessor]] = list(assessors) if assessors is not None else None
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.scoring_mode = scoring_mode
        
        # Vision Integration: Lazy-load VisionAnalyst only if enabled
        self._vision_analyst = None
        self._vision_enabled = VISION_ENABLED
    
    @property
    def vision_analyst(self):
        """Lazy-load VisionAnalyst to avoid import overhead if not used."""
        if self._vision_analyst is None and self._vision_enabled:
            try:
                from ams.llm.vision import VisionAnalyst
                self._vision_analyst = VisionAnalyst()
                logger.info("VisionAnalyst loaded for visual grading.")
            except ImportError as e:
                logger.warning(f"VisionAnalyst not available: {e}")
                self._vision_enabled = False
        return self._vision_analyst

    def run(
        self, 
        submission_path: Path, 
        workspace_path: Path, 
        profile: str = "frontend",
        metadata: Mapping[str, object] | None = None
    ) -> Path:
        context = self._prepare_context(submission_path, workspace_path, profile)
        context.metadata["profile"] = profile
        context.metadata["scoring_mode"] = self.scoring_mode.value
        
        # Add submission metadata to context
        if metadata:
            context.metadata["submission_metadata"] = metadata
        
        profile_spec = get_profile_spec(profile)
        assessors = self.assessors or _default_assessors(profile_spec)

        findings: List[Finding] = []
        for assessor in assessors:
            findings.extend(assessor.run(context))

        # Add CONFIG warnings for required components with no required rules
        findings.extend(self._check_config_warnings(profile_spec, context))

        # =================================================================
        # LLM Integration Hook (Phase 1 & 2)
        # =================================================================
        llm_evidence: dict = {}
        if self._should_use_llm():
            findings, llm_evidence = self._enrich_findings_with_llm(
                findings, profile_spec, context
            )

        # =================================================================
        # Phase D: Conflict Resolution
        # =================================================================
        # Resolve conflicts between Static and Visual findings before scoring
        findings = resolve_conflicts(findings)

        scores, score_evidence = self.scoring_engine.score_with_evidence(
            findings,
            profile=profile,
            behavioural_evidence=context.behavioural_evidence,
            browser_evidence=context.browser_evidence,
        )
        
        report_path = workspace_path / "report.json"
        ReportWriter(report_path).write(
            context, findings, scores, 
            score_evidence=score_evidence, 
            metadata=metadata,
            llm_evidence=llm_evidence if llm_evidence else None,
        )
        
        # Generate HTML report
        try:
            from ams.io.html_reporter import HTMLReporter
            import json
            
            # Load the JSON report for HTML generation
            with open(report_path, "r", encoding="utf-8") as f:
                report_data = json.load(f)
            
            # Find screenshot if available
            screenshot_path = self._find_screenshot(workspace_path)
            
            html_reporter = HTMLReporter()
            html_path = html_reporter.generate(
                report_data=report_data,
                output_path=workspace_path,
                screenshot_path=screenshot_path,
            )
            logger.info(f"Generated HTML report: {html_path}")
        except Exception as e:
            logger.warning(f"Failed to generate HTML report: {e}")
        
        return report_path

    def _should_use_llm(self) -> bool:
        """Check if LLM should be used based on scoring mode."""
        return self.scoring_mode in (
            ScoringMode.STATIC_PLUS_LLM,
            ScoringMode.LLM_FEEDBACK_ONLY,
            ScoringMode.LLM_OVERRIDE,
        )

    def _enrich_findings_with_llm(
        self,
        findings: List[Finding],
        profile_spec: ProfileSpec,
        context: SubmissionContext,
    ) -> tuple[List[Finding], dict]:
        """Enrich findings with LLM feedback, partial credit, and vision analysis.
        
        Phase 1: Generate feedback for failed findings
        Phase 2: Evaluate partial credit for rules that allow it
        Phase 3: Run vision analysis for rules with visual_check=True
        
        Returns:
            Tuple of (enriched_findings, llm_evidence_dict)
        """
        enriched: List[Finding] = []
        llm_evidence: dict = {"feedback": [], "partial_credit": [], "vision_analysis": []}
        
        # Locate screenshot for vision analysis
        screenshot_path = self._find_screenshot(context.workspace_path)
        
        for finding in findings:
            # Only process failed findings (severity FAIL or WARN)
            if finding.severity not in (Severity.FAIL, Severity.WARN):
                enriched.append(finding)
                continue
            
            # Extract rule ID from finding - may be in evidence["rule_id"] for required checks
            rule_id = finding.id
            if isinstance(finding.evidence, dict) and finding.evidence.get("rule_id"):
                rule_id = finding.evidence["rule_id"]
            
            # Extract rule metadata for LLM enrichment (used by Phase 2 and 3)
            rule_metadata = self._get_rule_metadata(rule_id, profile_spec)
            
            # Extract code snippet from evidence if available
            code_snippet = ""
            if isinstance(finding.evidence, dict):
                code_snippet = finding.evidence.get("snippet", "")
                if not code_snippet:
                    code_snippet = finding.evidence.get("content", "")[:500]
            
            # Phase 1: Generate feedback for failed rules
            if self.scoring_mode in (ScoringMode.LLM_FEEDBACK_ONLY, ScoringMode.STATIC_PLUS_LLM):
                try:
                    feedback = generate_feedback(
                        rule_name=finding.id,
                        student_code=code_snippet,
                        error_context=finding.message,
                        category=finding.category,
                    )
                    
                    # Attach feedback to finding evidence
                    if isinstance(finding.evidence, dict):
                        finding.evidence["llm_feedback"] = feedback
                    
                    llm_evidence["feedback"].append({
                        "finding_id": finding.id,
                        "feedback": feedback,
                    })
                    
                except Exception as e:
                    logger.warning(f"Failed to generate LLM feedback for {finding.id}: {e}")
            
            # Phase 2: Evaluate partial credit
            if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                partial_allowed = rule_metadata.get("partial_allowed", False)
                partial_range = rule_metadata.get("partial_range", (0.0, 0.5))
                
                if should_evaluate_partial_credit(0.0, partial_allowed):
                    try:
                        hybrid_score = evaluate_partial_credit(
                            rule_name=finding.id,
                            student_code=code_snippet,
                            error_context=finding.message,
                            category=rule_metadata.get("category", "unknown"),
                            partial_range=partial_range,
                        )
                        
                        # Attach hybrid score to finding evidence
                        if isinstance(finding.evidence, dict):
                            finding.evidence["hybrid_score"] = hybrid_score.to_dict()
                        
                        llm_evidence["partial_credit"].append({
                            "finding_id": finding.id,
                            "hybrid_score": hybrid_score.to_dict(),
                        })
                        
                    except Exception as e:
                        logger.warning(f"Failed to evaluate partial credit for {finding.id}: {e}")
            
            # Phase 3 + C: Vision Analysis for visual_check rules
            if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                visual_check = rule_metadata.get("visual_check", False)
                
                if visual_check and screenshot_path and self.vision_analyst:
                    try:
                        # Use description-first approach for reliability with small models
                        requirement = f"Check if this design meets: {finding.message}"
                        
                        vision_result: VisionResult = self.vision_analyst.detect_layout_issues(
                            screenshot_path=str(screenshot_path),
                            requirement_context=requirement,
                        )
                        
                        # Attach vision result to finding evidence (as dict)
                        vision_dict = vision_result.model_dump()
                        if isinstance(finding.evidence, dict):
                            finding.evidence["vision_analysis"] = vision_dict
                        
                        llm_evidence["vision_analysis"].append({
                            "finding_id": finding.id,
                            "screenshot": str(screenshot_path.name),
                            "result": vision_dict,
                        })
                        
                        # Phase C: Create VISUAL finding if status is FAIL
                        if vision_result.status == "FAIL":
                            for issue in vision_result.issues:
                                visual_finding = Finding(
                                    id=f"VISUAL.{finding.id}",
                                    category="visual",
                                    message=issue.description,
                                    severity=Severity.FAIL if issue.severity == "FAIL" else Severity.WARN,
                                    evidence={
                                        "screenshot": str(screenshot_path),
                                        "original_rule": finding.id,
                                        "confidence": vision_result.confidence,
                                    },
                                    source="VisionAnalyst",
                                    finding_category=FindingCategory.VISUAL,
                                )
                                enriched.append(visual_finding)
                                logger.info(f"Created VISUAL finding: {visual_finding.id}")
                        
                        logger.info(f"Vision analysis for {finding.id}: {vision_result.status}")
                        
                    except Exception as e:
                        logger.warning(f"Failed vision analysis for {finding.id}: {e}")
            
            enriched.append(finding)
        
        return enriched, llm_evidence

    def _get_rule_metadata(self, rule_id: str, profile_spec: ProfileSpec) -> dict:
        """Extract metadata for a rule from the profile spec."""
        # Search through all rule types
        all_rules = (
            list(profile_spec.required_html) +
            list(profile_spec.required_css) +
            list(profile_spec.required_js) +
            list(profile_spec.required_php) +
            list(profile_spec.required_sql)
        )
        
        for rule in all_rules:
            if rule.id == rule_id:
                return {
                    "category": getattr(rule, "category", ""),
                    "partial_allowed": getattr(rule, "partial_allowed", False),
                    "partial_range": getattr(rule, "partial_range", (0.0, 0.5)),
                    "severity": getattr(rule, "severity", "medium"),
                    "llm_guidance": getattr(rule, "llm_guidance", ""),
                    "visual_check": getattr(rule, "visual_check", False),
                }
        
        return {}

    def _prepare_context(self, submission_path: Path, workspace_path: Path, profile: str) -> SubmissionContext:
        return SubmissionProcessor().prepare(submission_path, workspace_path, profile=profile)
    
    def _find_screenshot(self, workspace_path: Path) -> Optional[Path]:
        """Find a screenshot file for vision analysis.
        
        Looks for common screenshot filenames in the workspace.
        
        Args:
            workspace_path: Path to the assessment workspace.
            
        Returns:
            Path to screenshot if found, None otherwise.
        """
        screenshot_names = [
            "screenshot.png",
            "screenshot.jpg",
            "page.png",
            "page.jpg",
            "capture.png",
            "browser_screenshot.png",
        ]
        
        for name in screenshot_names:
            path = workspace_path / name
            if path.exists():
                logger.debug(f"Found screenshot: {path}")
                return path
        
        # Also check in submission subdirectory
        submission_dir = workspace_path / "submission"
        if submission_dir.exists():
            for name in screenshot_names:
                path = submission_dir / name
                if path.exists():
                    logger.debug(f"Found screenshot in submission: {path}")
                    return path
        
        return None

    def _check_config_warnings(self, profile_spec: ProfileSpec, context: SubmissionContext) -> List[Finding]:
        """Check for configuration issues: required components with no required rules."""
        warnings: List[Finding] = []
        components = ["html", "css", "js", "php", "sql"]
        
        for component in components:
            is_required = profile_spec.is_component_required(component)
            has_required_rules = profile_spec.has_required_rules(component)
            
            if is_required and not has_required_rules:
                # Component is required but has no required rules configured
                warnings.append(
                    Finding(
                        id=f"CONFIG.MISSING_REQUIRED_RULES.{component.upper()}",
                        category="config",
                        message=f"Component '{component}' is required for profile '{profile_spec.name}' but has no required rules configured. This may indicate a marker configuration issue.",
                        severity=Severity.WARN,
                        evidence={
                            "component": component,
                            "profile": profile_spec.name,
                            "required": True,
                            "has_required_rules": False,
                        },
                        source="pipeline",
                        finding_category=FindingCategory.CONFIG,
                        profile=profile_spec.name,
                        required=True,
                    )
                )
        
        return warnings


def _default_assessors(profile_spec: ProfileSpec) -> List[Assessor]:
    """Return the default ordered assessor pipeline for a profile."""
    return [
        HTMLStaticAssessor(),
        CSSStaticAssessor(),
        JSStaticAssessor(),
        PHPStaticAssessor(),
        SQLStaticAssessor(),
        HTMLRequiredElementsAssessor(profile=profile_spec),
        CSSRequiredRulesAssessor(profile=profile_spec),
        JSRequiredFeaturesAssessor(profile=profile_spec),
        PHPRequiredFeaturesAssessor(profile=profile_spec),
        SQLRequiredFeaturesAssessor(profile=profile_spec),
        ConsistencyAssessor(),  # Cross-file consistency checks after static/required
        HTMLBehavioralAssessor(),
        DeterministicTestEngine(),
        PlaywrightAssessor(),
    ]


__all__ = ["AssessmentPipeline"]

