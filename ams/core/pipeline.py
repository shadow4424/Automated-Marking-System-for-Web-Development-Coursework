from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

from ams.assessors import Assessor
from ams.assessors.behavioral import DeterministicTestEngine, HTMLBehavioralAssessor
from ams.assessors.playwright_assessor import PlaywrightAssessor
from ams.assessors.consistency_assessor import ConsistencyAssessor
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
from ams.llm.scoring import evaluate_partial_credit, should_evaluate_partial_credit

# Vision / UX Review
from ams.core.config import VISION_ENABLED, VISION_DELAY_BETWEEN_PAGES
from ams.llm.schemas import UXReviewResult
import time

# Phase D: Conflict Resolution
from ams.core.aggregation import resolve_conflicts

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

        # ── Record sandbox status in run metadata ────────────────────
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
        
        # Add submission metadata to context
        if metadata:
            context.metadata["submission_metadata"] = metadata
        
        # Derive run_id from workspace directory name for container naming
        context.metadata["run_id"] = workspace_path.name

        findings: List[Finding] = []

        # =================================================================
        # Threat Scanning — pre-execution inspection of submission files
        # =================================================================
        threat_findings, container_retain = self._run_threat_scan(context)
        if threat_findings:
            findings.extend(threat_findings)
            context.metadata["threat_detected"] = True
            context.metadata["container_retain"] = container_retain
            logger.warning(
                "Threat scanner flagged submission with %d finding(s). "
                "container_retain=%s  — skipping further assessment.",
                len(threat_findings),
                container_retain,
            )
        else:
            context.metadata["threat_detected"] = False
            context.metadata["container_retain"] = False

        # When threats are detected, skip all further assessment stages
        # (assessors, LLM enrichment, UX review) to avoid executing
        # potentially malicious code.  Jump straight to scoring/reporting.
        if not context.metadata.get("threat_detected"):
            profile_spec = get_profile_spec(profile)
            assessors = self.assessors or _default_assessors(
                profile_spec,
                container_retain=context.metadata.get("container_retain", False),
                run_id=context.metadata.get("run_id"),
            )

            for assessor in assessors:
                findings.extend(assessor.run(context))

            # Add CONFIG warnings for required components with no required rules
            findings.extend(self._check_config_warnings(profile_spec, context))

            # =================================================================
            # Artifact Integrity Verification — ensure screenshots exist
            # =================================================================
            try:
                from ams.sandbox.artifact_validator import validate_screenshot
                _validated_shot, artifact_findings = validate_screenshot(workspace_path)
                findings.extend(artifact_findings)
                if artifact_findings:
                    logger.warning("Artifact validation: screenshot missing or corrupt")
            except Exception as exc:
                logger.warning("Artifact validation failed: %s", exc)

            # =================================================================
            # LLM Integration Hook (Phase 1 & 2)
            # =================================================================
            llm_evidence: dict = {}
            if self._should_use_llm():
                findings, llm_evidence = self._enrich_findings_with_llm(
                    findings, profile_spec, context,
                )

            # =================================================================
            # Phase D: Conflict Resolution
            # =================================================================
            # Resolve conflicts between Static and Visual findings before scoring
            findings = resolve_conflicts(findings)
        else:
            profile_spec = get_profile_spec(profile)
            llm_evidence = {}

        # When threats are detected the submission is unsafe — force score to 0.
        if context.metadata.get("threat_detected"):
            scores = {"overall": 0.0, "by_component": {}}
            score_evidence = None
        else:
            scores, score_evidence = self.scoring_engine.score_with_evidence(
                findings,
                profile=profile,
                behavioural_evidence=context.behavioural_evidence,
                browser_evidence=context.browser_evidence,
            )

        # =================================================================
        # UX Review — qualitative, NON-SCORING feedback per page
        # (skipped when threats are detected)
        # =================================================================
        ux_reviews: list = []
        if not context.metadata.get("threat_detected") and self._should_use_llm() and self._vision_enabled:
            ux_findings, ux_reviews = self._run_ux_reviews(
                context, profile, findings,
            )
            # Append UX findings *after* scoring so they are visible in the
            # report but have zero impact on the student's grade.
            findings.extend(ux_findings)
            if "ux_reviews" not in llm_evidence:
                llm_evidence["ux_reviews"] = []
            llm_evidence["ux_reviews"].extend(ux_reviews)
        
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
            
            # Find screenshot if available (pass context for Playwright captures)
            screenshot_path = self._find_screenshot(workspace_path, context)
            
            html_reporter = HTMLReporter()
            html_path = html_reporter.generate(
                report_data=report_data,
                output_path=workspace_path,
                screenshot_path=screenshot_path,
            )
            logger.info(f"Generated HTML report: {html_path}")
        except Exception as e:
            logger.warning(f"Failed to generate HTML report: {e}")

        # ── Cleanup: remove redundant uploaded_extract/ ──
        self._cleanup_uploaded_extract(workspace_path)

        return report_path

    # ------------------------------------------------------------------
    # Post-run cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_uploaded_extract(workspace_path: Path) -> None:
        """Remove ``uploaded_extract/`` once ``submission/`` is confirmed.

        ``uploaded_extract`` is the raw unzip of the student upload and is
        fully superseded by the sanitised ``submission/`` tree.  Deleting it
        after assessment halves per-student disk usage.
        """
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

    # ------------------------------------------------------------------
    # Threat Scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _run_threat_scan(
        context: SubmissionContext,
    ) -> tuple[List[Finding], bool]:
        """Run the threat scanner on submission files.

        Returns ``(threat_findings, container_retain)``.
        *container_retain* is ``True`` when HIGH-severity threats are found.
        """
        from ams.sandbox.threat_scanner import ThreatScanner
        from ams.sandbox.threat_patterns import ThreatCategory
        from ams.core.finding_ids import SANDBOX

        # Category → finding-ID mapping
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

        try:
            scanner = ThreatScanner()
            scan_result = scanner.scan(context.submission_path)

            if not scan_result.threats:
                return findings, container_retain

            container_retain = scan_result.has_high_threats

            for threat in scan_result.threats:
                finding_id = _CATEGORY_TO_ID.get(
                    threat.category,
                    SANDBOX.THREAT.SHELL_EXECUTION,  # fallback
                )

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

    @staticmethod
    def _enrich_threat_finding(finding: Finding, llm_evidence: dict) -> None:
        """Use LLM to generate a security analysis for a THREAT finding."""
        from ams.llm.feedback import ask_llama, scrub_pii
        from ams.llm.prompts import (
            THREAT_ANALYSIS_SYSTEM_PROMPT,
            THREAT_ANALYSIS_USER_PROMPT_TEMPLATE,
        )
        from ams.llm.utils import clean_json_response
        import json as _json

        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        snippet = scrub_pii(evidence.get("snippet", "")[:500])
        prompt = THREAT_ANALYSIS_USER_PROMPT_TEMPLATE.format(
            category=evidence.get("category", "unknown"),
            pattern_name=evidence.get("pattern_name", "unknown"),
            file_path=evidence.get("file", "unknown"),
            snippet=snippet,
        )

        try:
            raw = ask_llama(prompt, system_prompt=THREAT_ANALYSIS_SYSTEM_PROMPT)
            cleaned = clean_json_response(raw)
            analysis = _json.loads(cleaned)
            evidence["llm_threat_analysis"] = analysis
            llm_evidence.setdefault("threat_analysis", []).append({
                "finding_id": finding.id,
                "analysis": analysis,
            })
        except Exception as exc:
            logger.warning("LLM threat analysis failed for %s: %s", finding.id, exc)
            evidence["llm_threat_analysis"] = {
                "risk_level": evidence.get("threat_severity", "UNKNOWN"),
                "explanation": finding.message,
                "recommendation": "Review manually — LLM analysis unavailable.",
                "error": True,
            }

    def _should_use_llm(self) -> bool:
        """Check if LLM should be used based on scoring mode."""
        return self.scoring_mode == ScoringMode.STATIC_PLUS_LLM

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
        llm_evidence: dict = {"feedback": [], "partial_credit": []}
        
        # Locate screenshot for vision analysis — prefer real Playwright capture
        screenshot_path = self._find_screenshot(context.workspace_path, context)
        
        for finding in findings:
            # Handle SKIPPED findings for required components (provide deterministic feedback)
            if finding.severity == Severity.SKIPPED:
                if finding.required:
                    skip_reason = finding.evidence.get("skip_reason", "component was skipped")
                    fallback_feedback = {
                        "summary": f"This check was not executed because {skip_reason}.",
                        "items": [],
                        "meta": {"fallback": True, "reason": "skipped_required"},
                    }
                    if isinstance(finding.evidence, dict):
                        finding.evidence["llm_feedback"] = fallback_feedback
                enriched.append(finding)
                continue

            # --- THREAT findings: LLM security analysis ---
            if finding.severity == Severity.THREAT:
                if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                    self._enrich_threat_finding(finding, llm_evidence)
                enriched.append(finding)
                continue
            
            # Only process actual failures (FAIL or WARN) for LLM processing
            if finding.severity not in (Severity.FAIL, Severity.WARN):
                enriched.append(finding)
                continue
            
            # Extract rule ID from finding - may be in evidence["rule_id"] for required checks
            rule_id = finding.id
            if isinstance(finding.evidence, dict) and finding.evidence.get("rule_id"):
                rule_id = finding.evidence["rule_id"]
            
            # Extract rule metadata for LLM enrichment (used by Phase 2 and 3)
            # Pass finding for fallback category if rule not found in required rules
            rule_metadata = self._get_rule_metadata(rule_id, profile_spec, finding)
            
            # Extract code snippet from evidence if available
            code_snippet = ""
            if isinstance(finding.evidence, dict):
                code_snippet = finding.evidence.get("snippet", "")
                if not code_snippet:
                    code_snippet = finding.evidence.get("content", "")[:500]
            
            # Log warning for findings with no code evidence ONLY if from required assessors where code is expected
            # Skip warning for findings from Quality, Consistency, Behaviour, Browser assessors which may not have code
            is_required_assessor = any(
                finding.id.upper().startswith(prefix) 
                for prefix in ["HTML.REQ", "CSS.REQ", "JS.REQ", "PHP.REQ", "SQL.REQ"]
            )
            if not code_snippet.strip() and is_required_assessor:
                logger.warning(f"Finding {finding.id} has no code evidence for LLM enrichment (MISSING_FILES or read error)")
            
            # Skip LLM enrichment for non-required findings without code evidence
            # These findings (Quality, Consistency, Browser, etc.) don't require code context
            if not code_snippet.strip() and not is_required_assessor:
                enriched.append(finding)
                continue
            
            # Handle empty code (MISSING_FILES) with deterministic message for required assessors
            if not code_snippet.strip() and is_required_assessor:
                fallback_feedback = {
                    "summary": f"No code was found for this check. Ensure you include the required files and format.",
                    "items": [],
                    "meta": {"fallback": True, "reason": "no_code"},
                }
                if isinstance(finding.evidence, dict):
                    finding.evidence["llm_feedback"] = fallback_feedback
                enriched.append(finding)
                continue
            
            # Phase 1: Generate feedback for failed rules
            if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
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
                    # Attach fallback feedback on error
                    fallback_feedback = {
                        "summary": f"This check failed: {finding.message}",
                        "items": [],
                        "meta": {"fallback": True, "reason": "llm_error"},
                    }
                    if isinstance(finding.evidence, dict):
                        finding.evidence["llm_feedback"] = fallback_feedback
            
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
                        # Attach fallback hybrid_score on error
                        fallback_score = {
                            "static_score": 0.0,
                            "llm_score": None,
                            "final_score": 0.0,
                            "reasoning": f"Partial credit evaluation failed: {str(e)[:100]}",
                            "intent_detected": False,
                            "error": True,
                        }
                        if isinstance(finding.evidence, dict):
                            finding.evidence["hybrid_score"] = fallback_score
            
            # Phase 3 + C: Legacy Vision Analysis removed.
            # Visual evaluation is now handled by the UX Review step that
            # runs AFTER scoring and has zero impact on the grade.
            
            enriched.append(finding)
        
        return enriched, llm_evidence

    # -----------------------------------------------------------------
    # UX Review — multi-page qualitative feedback (zero scoring impact)
    # -----------------------------------------------------------------

    def _run_ux_reviews(
        self,
        context: SubmissionContext,
        profile: str,
        static_findings: List[Finding] | None = None,
    ) -> tuple[List[Finding], list]:
        """Capture screenshots of every HTML page and run a UX review.

        Args:
            context: The current submission context.
            profile: Active marking profile name.
            static_findings: Findings already produced by the static and
                required assessors.  Used to build a *context note* for
                the vision model so it has deterministic grounding (e.g.
                "no CSS files found") **before** it looks at the image.

        Returns:
            (ux_findings, ux_evidence_list)

        The findings use ``category='ux_review'`` which is **not** in
        ``ScoringEngine.COMPONENTS``, so they are ignored by scoring.
        """
        from ams.assessors.playwright_assessor import PlaywrightAssessor as _PA

        ux_findings: List[Finding] = []
        ux_evidence: list = []

        # Use the PlaywrightAssessor already in the pipeline, or create one
        pa = None
        if self.assessors:
            for a in self.assessors:
                if isinstance(a, _PA):
                    pa = a
                    break
        if pa is None:
            pa = _PA()

        page_shots = pa.capture_all_pages(context)
        if not page_shots:
            logger.info("UX Review: no HTML pages found — skipping.")
            return ux_findings, ux_evidence

        analyst = self.vision_analyst
        if analyst is None:
            logger.warning("UX Review: VisionAnalyst not available — skipping.")
            return ux_findings, ux_evidence

        # Build a filename → Path lookup from the discovered HTML files so
        # we can read each page's source to produce a per-page context note.
        html_lookup: dict[str, Path] = {}
        for hp in context.discovered_files.get("html", []):
            html_lookup[hp.name] = hp

        for entry in page_shots:
            page_name: str = entry["page"]
            shot_path = entry.get("screenshot")  # may be None

            # ── Derive identifiers ───────────────────────────────────────────
            safe_id = page_name.upper().replace(".", "_")
            finding_id = f"UX_REVIEW.{safe_id}"

            # ── Handle missing screenshot ────────────────────────────────────
            if shot_path is None or not Path(str(shot_path)).exists():
                logger.warning(
                    "UX: failed %s error=no screenshot available", page_name,
                )
                ux_findings.append(
                    Finding(
                        id=finding_id,
                        category="ux_review",
                        message=f"UX review could not be completed for {page_name}: screenshot capture failed.",
                        severity=Severity.INFO,
                        evidence={
                            "ux_review": {
                                "page": page_name,
                                "status": "NOT_EVALUATED",
                                "feedback": "Screenshot capture failed — unable to perform visual review.",
                            },
                            "screenshot": None,
                            "page": page_name,
                        },
                        source="VisionAnalyst.ux_review",
                        finding_category=FindingCategory.VISUAL,
                        profile=profile,
                        required=False,
                    )
                )
                ux_evidence.append({
                    "page": page_name,
                    "screenshot": None,
                    "review": {
                        "page": page_name,
                        "status": "NOT_EVALUATED",
                        "feedback": "Screenshot capture failed.",
                    },
                })
                continue

            shot_path = Path(shot_path)

            # ── Per-page context note ────────────────────────────────────────
            context_note = self._build_per_page_context(
                page_name, html_lookup.get(page_name)
            )

            logger.debug(
                "UX: evaluating %s screenshot=%s size=%d",
                page_name, shot_path, shot_path.stat().st_size,
            )

            try:
                review = analyst.review_ux(
                    str(shot_path), page_name, context_note=context_note,
                )
            except Exception as exc:
                logger.warning(
                    "UX: failed %s error=%s", page_name, exc,
                )
                review = UXReviewResult(
                    page=page_name,
                    status="NOT_EVALUATED",
                    feedback=f"UX review failed: {exc}",
                    screenshot=str(shot_path),
                    model="unknown",
                )

            # Build a workspace-relative screenshot path for the UI
            try:
                rel_screenshot = shot_path.relative_to(context.workspace_path)
            except ValueError:
                rel_screenshot = Path(shot_path.name)

            review_dict = review.model_dump()

            # Build the finding message: feedback + improvement recommendation
            message_parts = [review.feedback or "No feedback generated."]
            if review.improvement_recommendation:
                message_parts.append(
                    f"Recommendation: {review.improvement_recommendation}"
                )
            finding_message = " ".join(message_parts)

            ux_findings.append(
                Finding(
                    id=finding_id,
                    category="ux_review",  # NOT a scoring component
                    message=finding_message,
                    severity=Severity.INFO,  # Always INFO — advisory only
                    evidence={
                        "ux_review": review_dict,
                        "screenshot": str(rel_screenshot),
                        "page": page_name,
                    },
                    source="VisionAnalyst.ux_review",
                    finding_category=FindingCategory.VISUAL,
                    profile=profile,
                    required=False,
                )
            )

            ux_evidence.append({
                "page": page_name,
                "screenshot": str(rel_screenshot),
                "review": review_dict,
            })

            logger.info(
                "UX: success %s status=%s", page_name, review.status,
            )

            # Optional cooldown between pages (default 0 = disabled)
            if (
                VISION_DELAY_BETWEEN_PAGES > 0
                and entry is not page_shots[-1]
            ):
                logger.debug(
                    "UX: sleeping %.1fs between pages",
                    VISION_DELAY_BETWEEN_PAGES,
                )
                time.sleep(VISION_DELAY_BETWEEN_PAGES)

        return ux_findings, ux_evidence

    # -----------------------------------------------------------------
    # Phase E: Static-grounding helper for UX reviews
    # -----------------------------------------------------------------

    @staticmethod
    def _build_ux_context_note(findings: List[Finding]) -> str | None:
        """Condense static-analysis findings into a short context note.

        The note is injected into the vision model's system prompt so that
        the LLM is *grounded* in verifiable facts before it interprets the
        screenshot.  This prevents hallucinated praise for blank or unstyled
        pages.

        Returns ``None`` when there is nothing noteworthy to report (i.e.
        both HTML and CSS are present and well-formed).
        """
        # Collect finding IDs for quick look-up
        ids = {f.id for f in findings}

        warnings: list[str] = []

        # --- HTML ---
        if "HTML.MISSING_FILES" in ids or "HTML.REQ.MISSING_FILES" in ids:
            warnings.append(
                "- No HTML files were found in the submission."
            )

        # --- CSS ---
        if "CSS.MISSING_FILES" in ids or "CSS.REQ.MISSING_FILES" in ids:
            warnings.append(
                "- No CSS files were found.  The page has NO external stylesheet."
            )
        elif "CSS.NO_RULES" in ids:
            warnings.append(
                "- A CSS file exists but contains zero valid rules. "
                "The page is effectively unstyled."
            )

        # --- JS ---
        if "JS.MISSING_FILES" in ids or "JS.REQ.MISSING_FILES" in ids:
            warnings.append(
                "- No JavaScript files were found in the submission."
            )

        if not warnings:
            return None

        return "\n".join(warnings)

    # -----------------------------------------------------------------
    # Phase I: Per-page context builder for UX reviews
    # -----------------------------------------------------------------

    _LINK_CSS_RE = re.compile(
        r"""<link\b[^>]*rel\s*=\s*["']stylesheet["'][^>]*>""",
        re.IGNORECASE,
    )
    _STYLE_TAG_RE = re.compile(
        r"<style[\s>]",
        re.IGNORECASE,
    )

    @staticmethod
    def _build_per_page_context(page_name: str, html_path: Path | None) -> str:
        """Read the HTML source for *one* page and produce a short context note.

        Unlike the submission-level ``_build_ux_context_note`` this method
        inspects the **actual HTML file** being evaluated so the context is
        accurate per page.  For example, ``about.html`` may lack a
        ``<link rel="stylesheet">`` while ``index.html`` includes one —
        each page gets its own truthful context note.

        Returns a short string suitable for injection into the system
        prompt's ``{context_note}`` placeholder.
        """
        if html_path is None or not html_path.exists():
            return (
                f"WARNING: The HTML source for {page_name} could not be read. "
                "Rely on visual evidence only."
            )

        try:
            source = html_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return (
                f"WARNING: The HTML source for {page_name} could not be read. "
                "Rely on visual evidence only."
            )

        has_link = bool(AssessmentPipeline._LINK_CSS_RE.search(source))
        has_style = bool(AssessmentPipeline._STYLE_TAG_RE.search(source))

        if not has_link and not has_style:
            return (
                f"WARNING: Static analysis of {page_name} found NO "
                "<link rel=\"stylesheet\"> and NO <style> block.  "
                "This page has no CSS and is unstyled."
            )

        parts: list[str] = []
        if has_link:
            parts.append("an external <link rel=\"stylesheet\">")
        if has_style:
            parts.append("an inline <style> block")

        return (
            f"This page ({page_name}) includes {' and '.join(parts)}.  "
            "Evaluate the visual quality of the applied styles."
        )

    def _get_rule_metadata(self, rule_id: str, profile_spec: ProfileSpec, finding: Finding | None = None) -> dict:
        """Extract metadata for a rule from the profile spec.
        
        Args:
            rule_id: The rule identifier to look up
            profile_spec: The profile specification
            finding: Optional Finding object to use for fallback metadata
            
        Returns:
            Dictionary with rule metadata, or minimal metadata using finding.category as fallback
        """
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
        
        # Fallback: construct minimal metadata from finding's category if available
        if finding:
            return {
                "category": getattr(finding, "category", "unknown") or "unknown",
                "partial_allowed": False,
                "partial_range": (0.0, 0.5),
                "severity": "medium",
                "llm_guidance": "",
                "visual_check": False,
            }
        
        return {}

    def _prepare_context(self, submission_path: Path, workspace_path: Path, profile: str) -> SubmissionContext:
        return SubmissionProcessor().prepare(submission_path, workspace_path, profile=profile)
    
    def _find_screenshot(
        self,
        workspace_path: Path,
        context: Optional[SubmissionContext] = None,
    ) -> Optional[Path]:
        """Find the best screenshot for vision analysis.

        Priority order:
        1. Real Playwright screenshots from ``context.browser_evidence``
           (stored under ``artifacts/browser/``).  The *newest* capture
           is preferred so we evaluate what the browser actually rendered.
        2. Common filenames in the workspace / submission directory
           (``screenshot.*``, ``page.*``, …).
        3. Any image file found via glob.

        Args:
            workspace_path: Path to the assessment workspace.
            context: The current :class:`SubmissionContext` (optional).
                     When provided the method inspects ``browser_evidence``
                     for real Playwright captures.

        Returns:
            Absolute path to the screenshot, or *None* if nothing usable
            was found.
        """
        # ---------------------------------------------------------
        # Pass 0 — real Playwright captures (highest priority)
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # Pass 1 — preferred filenames in workspace / submission
        # ---------------------------------------------------------
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

        # ---------------------------------------------------------
        # Pass 2 — any image file via glob (last resort)
        # ---------------------------------------------------------
        for directory in search_dirs:
            for ext in image_extensions:
                matches = sorted(directory.glob(f"*{ext}"))
                if matches:
                    logger.debug(f"Found screenshot (glob fallback): {matches[0]}")
                    return matches[0]

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


def _default_assessors(
    profile_spec: ProfileSpec,
    *,
    container_retain: bool = False,
    run_id: str | None = None,
) -> List[Assessor]:
    """Return the default ordered assessor pipeline for a profile."""
    from ams.sandbox.factory import get_command_runner, get_browser_runner

    cmd_runner = get_command_runner(
        container_retain=container_retain, run_id=run_id,
    )
    browser_runner = get_browser_runner(
        container_retain=container_retain, run_id=run_id,
    )

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
        DeterministicTestEngine(runner=cmd_runner),
        PlaywrightAssessor(runner=browser_runner),
    ]


__all__ = ["AssessmentPipeline"]

