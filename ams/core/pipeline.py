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

# LLM Integration (Phase 1 & 2)
from ams.llm.scoring import evaluate_partial_credit_batch, should_evaluate_partial_credit
from ams.llm.generators import BatchFeedbackGenerator

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
        self.requirement_engine = RequirementEvaluationEngine()
        
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

    def _setup_run(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str,
        metadata: Mapping[str, object] | None,
    ) -> tuple[SubmissionContext, ResolvedAssignmentConfig]:
        resolved_config = resolve_assignment_config(profile, metadata=metadata)
        context = self._prepare_context(
            submission_path,
            workspace_path,
            profile,
            resolved_config,
        )
        context.resolved_config = resolved_config
        context.metadata["profile"] = profile
        context.metadata["requested_profile"] = profile
        context.metadata["resolved_profile"] = resolved_config.profile_name
        context.metadata["resolved_assignment_config"] = resolved_config.to_dict()
        context.metadata["scoring_mode"] = self.scoring_mode.value
        context.metadata["llm_error_detected"] = False
        context.metadata["llm_error_messages"] = []
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

    def _run_analysis(
        self,
        context: SubmissionContext,
        config: ResolvedAssignmentConfig,
        skip_threat_scan: bool,
    ) -> dict[str, object]:
        findings: List[Finding] = []
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

        profile_spec = config.profile
        llm_evidence: dict = {}
        if not context.metadata.get("threat_detected"):
            assessors = self.assessors or _default_assessors(
                profile_spec,
                container_retain=context.metadata.get("container_retain", False),
                run_id=context.metadata.get("run_id"),
            )
            for assessor in assessors:
                findings.extend(assessor.run(context))
            _requirement_results, requirement_findings = self.requirement_engine.evaluate(
                context,
                findings,
            )
            findings.extend(requirement_findings)
            findings.extend(self._check_config_warnings(profile_spec, context))
            if profile_spec.enabled_browser_checks:
                try:
                    from ams.sandbox.artifact_validator import validate_screenshot

                    _validated_shot, artifact_findings = validate_screenshot(context.workspace_path)
                    findings.extend(artifact_findings)
                    if artifact_findings:
                        logger.warning("Artifact validation: screenshot missing or corrupt")
                except Exception as exc:
                    logger.warning("Artifact validation failed: %s", exc)
            if self._should_use_llm():
                findings, llm_evidence = self._enrich_findings_with_llm(
                    findings, profile_spec, context,
                )
            findings = resolve_conflicts(findings)

        return {
            "findings": findings,
            "llm_evidence": llm_evidence,
            "profile_name": str(context.metadata.get("profile") or profile_spec.name),
            "metadata": context.metadata.get("submission_metadata"),
        }

    def _generate_report(
        self,
        findings: dict[str, object],
        context: SubmissionContext,
        config: ResolvedAssignmentConfig,
    ) -> Path:
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

        ux_reviews: list = []
        if (
            not context.metadata.get("threat_detected")
            and self._should_use_llm()
            and self._vision_enabled
        ):
            ux_findings, ux_reviews = self._run_ux_reviews(
                context, profile, finding_items,
            )
            finding_items.extend(ux_findings)
            if "ux_reviews" not in llm_evidence:
                llm_evidence["ux_reviews"] = []
            llm_evidence["ux_reviews"].extend(ux_reviews)

        if context.metadata.get("llm_error_detected"):
            finding_items.append(self._build_llm_error_review_finding(context, profile))

        report_path = context.workspace_path / "report.json"
        ReportWriter(report_path).write(
            context,
            finding_items,
            scores,
            score_evidence=score_evidence,
            metadata=metadata,
            llm_evidence=llm_evidence if llm_evidence else None,
        )
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

    def run(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str = "frontend",
        metadata: Mapping[str, object] | None = None,
        skip_threat_scan: bool = False,
    ) -> Path:
        context, config = self._setup_run(
            submission_path,
            workspace_path,
            profile,
            metadata,
        )
        findings = self._run_analysis(context, config, skip_threat_scan)
        return self._generate_report(findings, context, config)

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

    @staticmethod
    def _record_llm_issue(context: SubmissionContext, message: object) -> None:
        text = str(message or "").strip()
        if not text:
            text = "LLM-assisted marking failed and requires review."
        messages = context.metadata.setdefault("llm_error_messages", [])
        if isinstance(messages, list) and text not in messages:
            messages.append(text)
        context.metadata["llm_error_detected"] = True
        context.metadata["llm_error_message"] = (
            messages[0] if isinstance(messages, list) and messages else text
        )

    @staticmethod
    def _build_llm_error_review_finding(
        context: SubmissionContext,
        profile: str,
    ) -> Finding:
        messages = [
            str(item).strip()
            for item in list(context.metadata.get("llm_error_messages", []) or [])
            if str(item).strip()
        ]
        summary = messages[0] if messages else "LLM-assisted marking failed and requires review."
        return Finding(
            id="LLM.ERROR.REQUIRES_REVIEW",
            category="llm",
            message=f"LLM Error - Requires Review. {summary}",
            severity=Severity.WARN,
            evidence={
                "llm_error_count": len(messages),
                "llm_error_message": summary,
                "llm_error_messages": messages,
            },
            source="AssessmentPipeline.llm",
            finding_category=FindingCategory.CONFIG,
            profile=profile,
            required=False,
            tags=["llm_error", "requires_review"],
        )

    def _should_use_llm(self) -> bool:
        """Check if LLM should be used based on scoring mode."""
        return self.scoring_mode == ScoringMode.STATIC_PLUS_LLM

    def _prepare_llm_enrichment_batches(
        self,
        findings: List[Finding],
        profile_spec: ProfileSpec,
    ) -> dict[str, object]:
        batch_size = 5
        enriched: List[Finding] = []
        llm_evidence: dict = {"feedback": [], "partial_credit": []}
        llm_candidates: list[dict] = []

        for finding in findings:
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

            if finding.severity == Severity.THREAT:
                if self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                    self._enrich_threat_finding(finding, llm_evidence)
                enriched.append(finding)
                continue

            if finding.severity not in (Severity.FAIL, Severity.WARN):
                enriched.append(finding)
                continue

            rule_id = finding.id
            if isinstance(finding.evidence, dict) and finding.evidence.get("rule_id"):
                rule_id = finding.evidence["rule_id"]
            rule_metadata = self._get_rule_metadata(rule_id, profile_spec, finding)

            code_snippet = ""
            if isinstance(finding.evidence, dict):
                code_snippet = finding.evidence.get("snippet", "")
                if not code_snippet:
                    code_snippet = finding.evidence.get("content", "")[:500]

            is_required_assessor = any(
                finding.id.upper().startswith(prefix)
                for prefix in ["HTML.REQ", "CSS.REQ", "JS.REQ", "PHP.REQ", "SQL.REQ"]
            )
            if not code_snippet.strip() and is_required_assessor:
                logger.warning(
                    "Finding %s has no code evidence for LLM enrichment (MISSING_FILES or read error)",
                    finding.id,
                )

            if not code_snippet.strip() and not is_required_assessor:
                enriched.append(finding)
                continue

            if not code_snippet.strip() and is_required_assessor:
                fallback_feedback = {
                    "summary": "No code was found for this check. Ensure you include the required files and format.",
                    "items": [],
                    "meta": {"fallback": True, "reason": "no_code"},
                }
                if isinstance(finding.evidence, dict):
                    finding.evidence["llm_feedback"] = fallback_feedback
                enriched.append(finding)
                continue

            llm_candidates.append({
                "finding": finding,
                "rule_id": rule_id,
                "rule_metadata": rule_metadata,
                "code_snippet": code_snippet,
            })

        chunks = [
            llm_candidates[i:i + batch_size]
            for i in range(0, len(llm_candidates), batch_size)
        ]
        chunk_fb_evidence: list[list[dict]] = []
        chunk_pc_items: list[list[dict]] = []
        chunk_pc_finding_maps: list[dict[str, dict]] = []

        for chunk in chunks:
            fb_evidence = [
                {
                    "rule_id": item["finding"].id,
                    "category": item["finding"].category,
                    "code_snippet": item["code_snippet"],
                    "error_context": item["finding"].message,
                }
                for item in chunk
            ]
            chunk_fb_evidence.append(fb_evidence)

            pc_items: list[dict] = []
            pc_finding_map: dict[str, dict] = {}
            for item in chunk:
                rm = item["rule_metadata"]
                if should_evaluate_partial_credit(0.0, rm.get("partial_allowed", False)):
                    pc_key = item["rule_id"]
                    pc_items.append({
                        "rule_name": pc_key,
                        "student_code": item["code_snippet"],
                        "error_context": item["finding"].message,
                        "category": rm.get("category", "unknown"),
                        "partial_range": rm.get("partial_range", (0.0, 0.5)),
                    })
                    pc_finding_map[pc_key] = item
            chunk_pc_items.append(pc_items)
            chunk_pc_finding_maps.append(pc_finding_map)

        return {
            "enriched": enriched,
            "llm_evidence": llm_evidence,
            "llm_candidates": llm_candidates,
            "chunks": chunks,
            "chunk_fb_evidence": chunk_fb_evidence,
            "chunk_pc_items": chunk_pc_items,
            "chunk_pc_finding_maps": chunk_pc_finding_maps,
        }

    def _run_llm_batch(
        self,
        batch: Mapping[str, object],
        context: SubmissionContext,
    ) -> tuple[str, int, object | None, str | None]:
        task_type = str(batch["task_type"])
        idx = int(batch["idx"])
        payload = batch["payload"]
        try:
            if task_type == "fb":
                result = BatchFeedbackGenerator().generate_batch(payload)
            else:
                result = evaluate_partial_credit_batch(payload)
            return task_type, idx, result, None
        except Exception as exc:
            logger.error("LLM %s chunk %d failed: %s", task_type, idx, exc)
            return task_type, idx, None, str(exc)

    def _merge_llm_results(
        self,
        findings: dict[str, object],
        llm_results: Sequence[tuple[str, int, object | None, str | None]],
    ) -> tuple[List[Finding], dict, list[str]]:
        enriched = list(findings["enriched"])
        llm_evidence = findings["llm_evidence"]
        chunks = findings["chunks"]
        chunk_pc_finding_maps = findings["chunk_pc_finding_maps"]
        issue_messages: list[str] = []
        fb_results: dict[int, dict] = {}
        pc_results: dict[int, dict] = {}

        for task_type, idx, result, error in llm_results:
            if error:
                label = "Feedback" if task_type == "fb" else "Partial-credit"
                issue_messages.append(f"{label} LLM task failed: {error}")
                continue
            if task_type == "fb":
                fb_results[idx] = result
            else:
                pc_results[idx] = result

        for idx, chunk in enumerate(chunks):
            fb_map = fb_results.get(idx, {})
            for item in chunk:
                fid = item["finding"].id
                fb = fb_map.get(fid)
                if fb is not None:
                    fb_dict = fb.model_dump() if hasattr(fb, "model_dump") else fb
                    if isinstance(item["finding"].evidence, dict):
                        item["finding"].evidence["llm_feedback"] = fb_dict
                    fb_meta = fb_dict.get("meta", {}) if isinstance(fb_dict, dict) else {}
                    if isinstance(fb_meta, dict) and fb_meta.get("fallback"):
                        reason = str(fb_meta.get("reason") or "").strip().lower()
                        error = str(fb_meta.get("error") or "").strip()
                        if reason == "llm_error" or error:
                            issue_messages.append(
                                f"{fid}: {error or 'LLM feedback generation failed.'}"
                            )
                    llm_evidence["feedback"].append({
                        "finding_id": fid,
                        "feedback": fb_dict,
                    })
                else:
                    if isinstance(item["finding"].evidence, dict):
                        item["finding"].evidence["llm_feedback"] = {
                            "summary": f"This check failed: {item['finding'].message}",
                            "items": [],
                            "meta": {"fallback": True, "reason": "llm_error"},
                        }
                    issue_messages.append(f"{fid}: LLM feedback generation failed.")

            score_map = pc_results.get(idx, {})
            pc_finding_map = chunk_pc_finding_maps[idx]
            for rule_name, hybrid_score in score_map.items():
                pi = pc_finding_map.get(rule_name)
                if pi is None:
                    continue
                if isinstance(pi["finding"].evidence, dict):
                    pi["finding"].evidence["hybrid_score"] = hybrid_score.to_dict()
                reasoning = str(hybrid_score.reasoning or "").strip()
                raw_error = ""
                if isinstance(hybrid_score.raw_response, dict):
                    raw_error = str(hybrid_score.raw_response.get("error") or "").strip()
                if raw_error or "llm error" in reasoning.lower() or "llm parse error" in reasoning.lower():
                    issue_messages.append(
                        f"{rule_name}: {raw_error or reasoning or 'LLM partial-credit evaluation failed.'}"
                    )
                llm_evidence["partial_credit"].append({
                    "finding_id": pi["finding"].id,
                    "rule_id": rule_name,
                    "hybrid_score": hybrid_score.to_dict(),
                })

        for item in findings["llm_candidates"]:
            enriched.append(item["finding"])
        return enriched, llm_evidence, issue_messages

    def _enrich_findings_with_llm(
        self,
        findings: List[Finding],
        profile_spec: ProfileSpec,
        context: SubmissionContext,
    ) -> tuple[List[Finding], dict]:
        prepared = self._prepare_llm_enrichment_batches(findings, profile_spec)
        chunks = prepared["chunks"]
        if chunks and self.scoring_mode == ScoringMode.STATIC_PLUS_LLM:
            chunk_pc_items = prepared["chunk_pc_items"]
            total_tasks = len(chunks) + sum(1 for pc in chunk_pc_items if pc)
            llm_workers = min(4, total_tasks)
            logger.info(
                "Parallel LLM enrichment: %d chunks -> %d tasks across %d workers",
                len(chunks), total_tasks, llm_workers,
            )
            llm_results: list[tuple[str, int, object | None, str | None]] = []
            with ThreadPoolExecutor(max_workers=llm_workers) as executor:
                future_map = {
                    executor.submit(
                        self._run_llm_batch,
                        {"task_type": "fb", "idx": idx, "payload": fb_ev},
                        context,
                    ): ("fb", idx)
                    for idx, fb_ev in enumerate(prepared["chunk_fb_evidence"])
                }
                future_map.update({
                    executor.submit(
                        self._run_llm_batch,
                        {"task_type": "pc", "idx": idx, "payload": pc_it},
                        context,
                    ): ("pc", idx)
                    for idx, pc_it in enumerate(chunk_pc_items)
                    if pc_it
                })
                for future in as_completed(future_map):
                    llm_results.append(future.result())

            enriched, llm_evidence, issue_messages = self._merge_llm_results(prepared, llm_results)
            for message in issue_messages:
                self._record_llm_issue(context, message)
            logger.info(
                "LLM enrichment complete: %d feedback, %d partial-credit across %d chunks",
                len(llm_evidence["feedback"]),
                len(llm_evidence["partial_credit"]),
                len(chunks),
            )
            return enriched, llm_evidence

        return prepared["enriched"] + [item["finding"] for item in prepared["llm_candidates"]], prepared["llm_evidence"]

    # -----------------------------------------------------------------
    # UX Review — multi-page qualitative feedback (zero scoring impact)
    # -----------------------------------------------------------------

    def _capture_ux_screenshots(
        self,
        context: SubmissionContext,
        profile: str,
    ) -> dict[str, object]:
        from ams.assessors.playwright_assessor import PlaywrightAssessor as _PA

        pa = None
        if self.assessors:
            for assessor in self.assessors:
                if isinstance(assessor, _PA):
                    pa = assessor
                    break
        if pa is None:
            pa = _PA()

        page_shots = pa.capture_all_pages(context)
        if not page_shots:
            logger.info("UX Review: no HTML pages found - skipping.")
            return {
                "context": context,
                "profile": profile,
                "page_shots": [],
                "html_lookup": {},
            }

        html_lookup: dict[str, Path] = {}
        for html_path in context.discovered_files.get("html", []):
            html_lookup[html_path.name] = html_path
        return {
            "context": context,
            "profile": profile,
            "page_shots": page_shots,
            "html_lookup": html_lookup,
        }

    def _evaluate_ux_screenshots(
        self,
        screenshots: Mapping[str, object],
        static_findings: List[Finding] | None,
    ) -> tuple[List[Finding], list]:
        del static_findings
        context = screenshots["context"]
        profile = str(screenshots["profile"])
        page_shots = screenshots["page_shots"]
        html_lookup = screenshots["html_lookup"]
        ux_findings: List[Finding] = []
        ux_evidence: list = []

        analyst = self.vision_analyst
        if analyst is None:
            logger.warning("UX Review: VisionAnalyst not available - skipping.")
            return ux_findings, ux_evidence

        for entry in page_shots:
            page_name: str = entry["page"]
            shot_path = entry.get("screenshot")
            safe_id = page_name.upper().replace(".", "_")
            finding_id = f"UX_REVIEW.{safe_id}"

            if shot_path is None or not Path(str(shot_path)).exists():
                logger.warning("UX: failed %s error=no screenshot available", page_name)
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
                                "feedback": "Screenshot capture failed - unable to perform visual review.",
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
                logger.warning("UX: failed %s error=%s", page_name, exc)
                review = UXReviewResult(
                    page=page_name,
                    status="NOT_EVALUATED",
                    feedback=f"UX review failed: {exc}",
                    screenshot=str(shot_path),
                    model="unknown",
                )

            try:
                rel_screenshot = shot_path.relative_to(context.workspace_path)
            except ValueError:
                rel_screenshot = Path(shot_path.name)

            review_dict = review.model_dump()
            review_feedback = str(review.feedback or "").strip()
            if review.status == "NOT_EVALUATED" and (
                review_feedback.lower().startswith("llm error:")
                or review_feedback.lower() == "could not parse model response."
            ):
                self._record_llm_issue(context, f"{page_name}: {review_feedback}")

            message_parts = [review.feedback or "No feedback generated."]
            if review.improvement_recommendation:
                message_parts.append(
                    f"Recommendation: {review.improvement_recommendation}"
                )
            finding_message = " ".join(message_parts)
            ux_findings.append(
                Finding(
                    id=finding_id,
                    category="ux_review",
                    message=finding_message,
                    severity=Severity.INFO,
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
            logger.info("UX: success %s status=%s", page_name, review.status)
            if VISION_DELAY_BETWEEN_PAGES > 0 and entry is not page_shots[-1]:
                logger.debug(
                    "UX: sleeping %.1fs between pages",
                    VISION_DELAY_BETWEEN_PAGES,
                )
                time.sleep(VISION_DELAY_BETWEEN_PAGES)

        return ux_findings, ux_evidence

    def _run_ux_reviews(
        self,
        context: SubmissionContext,
        profile: str,
        static_findings: List[Finding] | None = None,
    ) -> tuple[List[Finding], list]:
        screenshots = self._capture_ux_screenshots(context, profile)
        page_shots = screenshots["page_shots"]
        if not page_shots:
            return [], []
        return self._evaluate_ux_screenshots(screenshots, static_findings)

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

    def _prepare_context(
        self,
        submission_path: Path,
        workspace_path: Path,
        profile: str,
        resolved_config: ResolvedAssignmentConfig,
    ) -> SubmissionContext:
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



