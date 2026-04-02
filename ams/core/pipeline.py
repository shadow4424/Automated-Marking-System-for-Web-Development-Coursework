from __future__ import annotations

import logging
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from ams.assessors import Assessor
from ams.assessors.behavioral import DeterministicTestEngine, HTMLBehavioralAssessor
from ams.assessors.playwright_assessor import PlaywrightAssessor
from ams.assessors.consistency_assessor import ConsistencyAssessor
from ams.assessors.static import (
    APIStaticAssessor,
    CSSStaticAssessor,
    HTMLStaticAssessor,
    JSStaticAssessor,
    PHPStaticAssessor,
    SQLStaticAssessor,
)
from ams.core.assignment_config import ResolvedAssignmentConfig, resolve_assignment_config
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec
from ams.core.requirements import RequirementEvaluationEngine
from ams.core.submission_evidence import build_submission_evidence
from ams.core.scoring import ScoringEngine
from ams.core.config import SCORING_MODE, ScoringMode
from ams.io.reporting import ReportWriter
from ams.io.submission import SubmissionProcessor

# LLM Integration
from ams.core import llm_enrichment as _llm

# Vision / UX Review
from ams.core.config import VISION_ENABLED, VISION_DELAY_BETWEEN_PAGES

# Conflict Resolution
from ams.core.aggregation import resolve_conflicts

logger = logging.getLogger(__name__)

# Default assessor factory based on profile specification.
class AssessmentPipeline:
    """Orchestrates assessors, scoring, and reporting."""

    # Initialisation with optional custom components.
    def __init__(
        self,
        assessors: Optional[Iterable[Assessor]] = None,
        scoring_engine: Optional[ScoringEngine] = None,
        scoring_mode: ScoringMode = SCORING_MODE,
    ) -> None:
        """Initialise the assessment pipeline."""
        self.assessors: Optional[List[Assessor]] = list(assessors) if assessors is not None else None
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.scoring_mode = scoring_mode
        self.requirement_engine = RequirementEvaluationEngine()

        # Vision Integration
        self._vision_analyst = None
        self._vision_enabled = VISION_ENABLED

    # Vision / UX Review
    @property
    def vision_analyst(self):
        """Load VisionAnalyst to avoid import overhead if not used."""
        if self._vision_analyst is None and self._vision_enabled:
            try:
                from ams.llm.vision import VisionAnalyst
                self._vision_analyst = VisionAnalyst()
                logger.info("VisionAnalyst loaded for visual grading.")
            except ImportError as e:
                logger.warning(f"VisionAnalyst not available: {e}")
                self._vision_enabled = False
        return self._vision_analyst

    # Default assessor factory based on profile specification.
    def _setup_run(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str,
        metadata: Mapping[str, object] | None,
    ) -> tuple[SubmissionContext, ResolvedAssignmentConfig]:
        """Prepare the submission context and resolved config for a run."""
        resolved_config = resolve_assignment_config(profile, metadata=metadata)
        context = self._prepare_context(
            submission_path,
            workspace_path,
            profile,
            resolved_config,
        )
        # Build submission evidence for use in assessors and scoring.
        context.resolved_config = resolved_config
        context.metadata["profile"] = profile
        context.metadata["requested_profile"] = profile
        context.metadata["resolved_profile"] = resolved_config.profile_name
        context.metadata["resolved_assignment_config"] = resolved_config.to_dict()
        context.metadata["scoring_mode"] = self.scoring_mode.value
        context.metadata["llm_error_detected"] = False
        context.metadata["llm_error_messages"] = []
        
        # Build initial submission evidence bundle for assessors and scoring engine.
        try:
            from ams.sandbox.config import get_sandbox_status

            sandbox_status = get_sandbox_status()
            context.metadata["sandbox"] = sandbox_status
            if sandbox_status.get("enforced"):
                logger.info("Pipeline running with Docker sandbox ACTIVE.")
            else:
                logger.warning(
                    "Pipeline running WITHOUT full sandbox: %s",
                    sandbox_status.get("message", ""),
                )
        except Exception as exc:
            logger.warning("Unable to determine sandbox status: %s", exc)
            context.metadata["sandbox"] = {"enforced": False, "message": str(exc)}
        if metadata:
            context.metadata["submission_metadata"] = metadata
        context.metadata["run_id"] = workspace_path.name
        return context, resolved_config

    # Context preparation
    def _run_analysis(
        self,
        context: SubmissionContext,
        config: ResolvedAssignmentConfig,
        skip_threat_scan: bool,
    ) -> dict[str, object]:
        """Run the assessment analysis for a prepared context."""
        findings: List[Finding] = []

        # Threat scanning and initial metadata setup
        if skip_threat_scan:
            logger.warning(
                "Threat scan BYPASSED for run %s - instructor override active.",
                context.workspace_path.name,
            )
            context.metadata["threat_detected"] = False
            context.metadata["threat_override"] = True
            context.metadata["container_retain"] = False
        else:
            threat_findings, container_retain = self._run_threat_scan(context)
            if threat_findings:
                findings.extend(threat_findings)
                context.metadata["threat_detected"] = True
                context.metadata["container_retain"] = container_retain
                logger.warning(
                    "Threat scanner flagged submission with %d finding(s). "
                    "container_retain=%s  - skipping further assessment.",
                    len(threat_findings),
                    container_retain,
                )
            else:
                context.metadata["threat_detected"] = False
                context.metadata["container_retain"] = False

        # Main assessment logic (skipped if threat detected)
        profile_spec = config.profile
        llm_evidence: dict = {} # LLM evidence to be collected and returned with findings
        if not context.metadata.get("threat_detected"):
            # Assessors can be pre-initialised and passed in.
            assessors = self.assessors or _default_assessors(
                profile_spec,
                container_retain=context.metadata.get("container_retain", False),
                run_id=context.metadata.get("run_id"),
            )
            # Run each assessor and collect findings.
            for assessor in assessors:
                findings.extend(assessor.run(context))
            _requirement_results, requirement_findings = self.requirement_engine.evaluate(
                context,
                findings,
            )
            findings.extend(requirement_findings)
            findings.extend(self._check_config_warnings(profile_spec, context))
            # Vision / UX review findings will be added to the report but do not affect scoring.
            if profile_spec.enabled_browser_checks:
                # Delay to allow any asynchronous browser processes to complete before taking screenshots for LLM review.
                try:
                    from ams.sandbox.artifact_validator import validate_screenshot

                    _validated_shot, artifact_findings = validate_screenshot(context.workspace_path)
                    findings.extend(artifact_findings)
                    if artifact_findings:
                        logger.warning("Artifact validation: screenshot missing or corrupt")
                except Exception as exc:
                    logger.warning("Artifact validation failed: %s", exc)
            # LLM enrichment for static findings (if enabled in scoring mode).
            if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                findings, llm_evidence = _llm.enrich_findings_with_llm(
                    findings, profile_spec, context, self.scoring_mode,
                )
            findings = resolve_conflicts(findings)

        return {
            "findings": findings,
            "llm_evidence": llm_evidence,
            "profile_name": str(context.metadata.get("profile") or profile_spec.name),
            "metadata": context.metadata.get("submission_metadata"),
        }

    # Report generation and post-run cleanup
    def _generate_report(
        self,
        findings: dict[str, object],
        context: SubmissionContext,
        config: ResolvedAssignmentConfig,
    ) -> Path:
        """Write the report artefacts for an assessment run."""
        profile = str(findings["profile_name"])
        llm_evidence = findings["llm_evidence"]
        finding_items = list(findings["findings"])
        metadata = findings.get("metadata")

        if context.metadata.get("threat_detected"):
            scores = {"overall": 0.0, "by_component": {}}
            score_evidence = None
        else:
            scores, score_evidence = self.scoring_engine.score_with_evidence(
                finding_items,
                context=context,
                resolved_config=config,
                behavioural_evidence=context.behavioural_evidence,
                browser_evidence=context.browser_evidence,
            )

        # Vision / UX review is run post-scoring to avoid impacting scores.
        ux_reviews: list = []
        if (
            not context.metadata.get("threat_detected")
            and self.scoring_mode == ScoringMode.STATIC_PLUS_LLM
            and self._vision_enabled
        ):
            ux_findings, ux_reviews = _llm.run_ux_reviews(
                context, profile, self.assessors,
                self.vision_analyst, VISION_DELAY_BETWEEN_PAGES,
                finding_items,
            )
            finding_items.extend(ux_findings)
            if "ux_reviews" not in llm_evidence:
                llm_evidence["ux_reviews"] = []
            llm_evidence["ux_reviews"].extend(ux_reviews)

        # If LLM errors were detected, add a review finding to the report to flag the issue.
        if context.metadata.get("llm_error_detected"):
            finding_items.append(_llm.build_llm_error_review_finding(context, profile))

        # Write the report artefacts.
        report_path = context.workspace_path / "report.json"
        ReportWriter(report_path).write(
            context,
            finding_items,
            scores,
            score_evidence=score_evidence,
            metadata=metadata,
            llm_evidence=llm_evidence if llm_evidence else None,
        )
        # Attempt to generate HTML report, but do not fail the run if this step encounters issues.
        try:
            from ams.io.html_reporter import HTMLReporter
            import json

            with open(report_path, "r", encoding="utf-8") as f:
                report_data = json.load(f)

            screenshot_path = self._find_screenshot(context.workspace_path, context)
            html_reporter = HTMLReporter()
            html_path = html_reporter.generate(
                report_data=report_data,
                output_path=context.workspace_path,
                screenshot_path=screenshot_path,
            )
            logger.info(f"Generated HTML report: {html_path}")
        except Exception as e:
            logger.warning(f"Failed to generate HTML report: {e}")

        self._cleanup_uploaded_extract(context.workspace_path)
        return report_path

    # Main pipeline method
    def run(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str = "frontend",
        metadata: Mapping[str, object] | None = None,
        skip_threat_scan: bool = False,
    ) -> Path:
        """Run the pipeline."""
        context, config = self._setup_run(
            submission_path,
            workspace_path,
            profile,
            metadata,
        )
        findings = self._run_analysis(context, config, skip_threat_scan)
        return self._generate_report(findings, context, config)


    # Post-run cleanup - save space.
    @staticmethod
    def _cleanup_uploaded_extract(workspace_path: Path) -> None:
        """Remove uploaded_extract/ once submission/ is confirmed."""
        extracted = workspace_path / "uploaded_extract"
        submission = workspace_path / "submission"

        if not extracted.is_dir():
            return

        if not submission.is_dir():
            logger.warning(
                "Skipping uploaded_extract cleanup — submission/ not found "
                "in %s",
                workspace_path,
            )
            return

        try:
            shutil.rmtree(extracted)
            logger.info("Removed redundant uploaded_extract/ from %s", workspace_path)
        except Exception as exc:
            logger.warning("Failed to remove uploaded_extract/: %s", exc)


    # Threat Scanning - returns findings and whether the container should be retained for manual review.
    @staticmethod
    def _run_threat_scan(
        context: SubmissionContext,
    ) -> tuple[List[Finding], bool]:
        """Run the threat scanner on submission files."""
        from ams.sandbox.threat_scanner import ThreatScanner
        from ams.sandbox.threat_patterns import ThreatCategory
        from ams.core.finding_ids import SANDBOX

        # Mapping from internal threat categories to standardised finding IDs for reporting.
        _CATEGORY_TO_ID = {
            ThreatCategory.SHELL_EXECUTION: SANDBOX.THREAT.SHELL_EXECUTION,
            ThreatCategory.PROCESS_CONTROL: SANDBOX.THREAT.PROCESS_CONTROL,
            ThreatCategory.FILESYSTEM_ESCAPE: SANDBOX.THREAT.FILESYSTEM_ESCAPE,
            ThreatCategory.NETWORK_ACCESS: SANDBOX.THREAT.NETWORK_ACCESS,
            ThreatCategory.CODE_INJECTION: SANDBOX.THREAT.CODE_INJECTION,
            ThreatCategory.OBFUSCATION: SANDBOX.THREAT.OBFUSCATION,
            ThreatCategory.DANGEROUS_JS: SANDBOX.THREAT.DANGEROUS_JS,
            ThreatCategory.BINARY_INJECTION: SANDBOX.THREAT.BINARY_INJECTION,
            ThreatCategory.SYMLINK_ATTACK: SANDBOX.THREAT.SYMLINK_ATTACK,
        }

        findings: List[Finding] = []
        container_retain = False

        # Run the threat scanner and convert results into findings.
        # Any exceptions during scanning are caught and logged.
        try:
            scanner = ThreatScanner()
            scan_result = scanner.scan(context.submission_path)

            if not scan_result.threats:
                return findings, container_retain

            container_retain = scan_result.has_high_threats

            # Convert each detected threat into a Finding with appropriate evidence for reporting.
            for threat in scan_result.threats:
                finding_id = _CATEGORY_TO_ID.get(
                    threat.category,
                    SANDBOX.THREAT.SHELL_EXECUTION,  # Fallback
                )

                # For high severity threats, we flag the container for retention to allow manual review.
                findings.append(Finding(
                    id=finding_id,
                    category="security",
                    message=f"Threat detected: {threat.description} in {threat.file}"
                            + (f" (line {threat.line})" if threat.line else ""),
                    severity=Severity.THREAT,
                    evidence={
                        "file": threat.file,
                        "line": threat.line,
                        "pattern_name": threat.pattern_name,
                        "category": threat.category.value,
                        "threat_severity": threat.severity.value,
                        "snippet": threat.snippet,
                    },
                    source="ThreatScanner",
                    finding_category=FindingCategory.SECURITY,
                ))

            # Store scan summary in context metadata
            context.metadata["threat_scan"] = {
                "total_threats": scan_result.threat_count,
                "high": scan_result.high_count,
                "medium": scan_result.medium_count,
                "low": scan_result.low_count,
                "files_scanned": scan_result.files_scanned,
                "container_retain": container_retain,
            }

        except Exception as exc:
            logger.error("Threat scanner failed: %s", exc)

        return findings, container_retain

    # LLM feedback for findings
    def _prepare_context(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str,
        resolved_config: ResolvedAssignmentConfig,
    ) -> SubmissionContext:
        """Prepare the submission context for assessment."""
        context = SubmissionProcessor().prepare(
            submission_path,
            workspace_path,
            profile=profile,
            resolved_config=resolved_config,
        )
        context.resolved_config = resolved_config
        build_submission_evidence(context)
        return context

    def _find_screenshot(
        self,
        workspace_path: Path,
        context: Optional[SubmissionContext] = None,
    ) -> Optional[Path]:
        """Find the best screenshot for vision analysis."""

        # Pass 0 — real Playwright captures (highest priority)

        if context is not None:
            playwright_shots: list[Path] = []
            for be in context.browser_evidence:
                for sp in (be.screenshot_paths or []):
                    p = Path(sp)
                    if p.exists() and p.stat().st_size > 500:
                        playwright_shots.append(p)
            if playwright_shots:
                # Pick the newest capture
                best = max(playwright_shots, key=lambda p: p.stat().st_mtime)
                logger.info(f"Using Playwright screenshot for vision: {best}")
                return best


        # Pass 1 — preferred filenames in workspace / submission

        search_dirs = [workspace_path]
        submission_dir = workspace_path / "submission"
        if submission_dir.exists():
            search_dirs.append(submission_dir)

        preferred_stems = [
            "screenshot",
            "browser_screenshot",
            "page",
            "capture",
            "preview",
        ]
        image_extensions = (".png", ".jpg", ".jpeg", ".webp")

        for directory in search_dirs:
            for stem in preferred_stems:
                for ext in image_extensions:
                    path = directory / f"{stem}{ext}"
                    if path.exists():
                        logger.debug(f"Found screenshot: {path}")
                        return path


        # Pass 2 — any image file via glob (last resort)

        for directory in search_dirs:
            for ext in image_extensions:
                matches = sorted(directory.glob(f"*{ext}"))
                if matches:
                    logger.debug(f"Found screenshot (glob fallback): {matches[0]}")
                    return matches[0]

        return None

    def _run_ux_reviews(self, context, profile, static_findings=None):
        """Delegate to llm_enrichment module (kept for backward compat)."""
        return _llm.run_ux_reviews(
            context, profile, self.assessors, self.vision_analyst,
            VISION_DELAY_BETWEEN_PAGES, static_findings,
        )

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


def _default_assessors(
    profile_spec: ProfileSpec,
    *,
    container_retain: bool = False,
    run_id: str | None = None,
) -> List[Assessor]:
    """Return default ordered assessor pipeline for a profile."""
    from ams.sandbox.factory import get_command_runner, get_browser_runner

    cmd_runner = get_command_runner(
        container_retain=container_retain, run_id=run_id,
    )
    browser_runner = get_browser_runner(
        container_retain=container_retain, run_id=run_id,
    )

    assessors: List[Assessor] = [
        HTMLStaticAssessor(),
        CSSStaticAssessor(),
        JSStaticAssessor(),
        APIStaticAssessor(),
        PHPStaticAssessor(),
        SQLStaticAssessor(),
        ConsistencyAssessor(),  # Cross-file consistency checks after static/required
    ]
    if profile_spec.enabled_behavioural_checks:
        assessors.append(HTMLBehavioralAssessor())
        assessors.append(DeterministicTestEngine(runner=cmd_runner))
    if profile_spec.enabled_browser_checks:
        assessors.append(PlaywrightAssessor(runner=browser_runner))
    return assessors


__all__ = ["AssessmentPipeline"]


