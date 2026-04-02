"""LLM enrichment helpers extracted from the assessment pipeline.

All functions in this module are stateless — they receive the scoring
mode, profile spec, and context as explicit parameters instead of
depending on ``self``.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Mapping, Sequence

from ams.core.config import ScoringMode
from ams.core.models import Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import ProfileSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static helpers (no pipeline state needed)
# ---------------------------------------------------------------------------

def enrich_threat_finding(finding: Finding, llm_evidence: dict) -> None:
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


def record_llm_issue(context: SubmissionContext, message: object) -> None:
    """Record an LLM issue on the submission context."""
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


def build_llm_error_review_finding(
    context: SubmissionContext,
    profile: str,
) -> Finding:
    """Build the review finding shown when LLM processing fails."""
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


def get_rule_metadata(
    rule_id: str,
    profile_spec: ProfileSpec,
    finding: Finding | None = None,
) -> dict:
    """Extract metadata for a rule from the profile spec."""
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


# ---------------------------------------------------------------------------
# Batch preparation / execution / merging
# ---------------------------------------------------------------------------

def prepare_llm_enrichment_batches(
    findings: List[Finding],
    profile_spec: ProfileSpec,
    scoring_mode: ScoringMode,
) -> dict[str, object]:
    """Prepare batched LLM enrichment payloads for findings."""
    from ams.llm.scoring import should_evaluate_partial_credit

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
            if scoring_mode == ScoringMode.STATIC_PLUS_LLM:
                enrich_threat_finding(finding, llm_evidence)
            enriched.append(finding)
            continue

        if finding.severity not in (Severity.FAIL, Severity.WARN):
            enriched.append(finding)
            continue

        rule_id = finding.id
        if isinstance(finding.evidence, dict) and finding.evidence.get("rule_id"):
            rule_id = finding.evidence["rule_id"]
        rule_metadata = get_rule_metadata(rule_id, profile_spec, finding)

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


def run_llm_batch(
    batch: Mapping[str, object],
    context: SubmissionContext,
) -> tuple[str, int, object | None, str | None]:
    """Run a single feedback or partial-credit LLM batch."""
    from ams.llm.generators import BatchFeedbackGenerator
    from ams.llm.scoring import evaluate_partial_credit_batch

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


def merge_llm_results(
    findings: dict[str, object],
    llm_results: Sequence[tuple[str, int, object | None, str | None]],
) -> tuple[List[Finding], dict, list[str]]:
    """Merge completed LLM results back into the finding set."""
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


def enrich_findings_with_llm(
    findings: List[Finding],
    profile_spec: ProfileSpec,
    context: SubmissionContext,
    scoring_mode: ScoringMode,
) -> tuple[List[Finding], dict]:
    """Enrich eligible findings with LLM feedback and scoring data."""
    prepared = prepare_llm_enrichment_batches(findings, profile_spec, scoring_mode)
    chunks = prepared["chunks"]
    if chunks and scoring_mode == ScoringMode.STATIC_PLUS_LLM:
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
                    run_llm_batch,
                    {"task_type": "fb", "idx": idx, "payload": fb_ev},
                    context,
                ): ("fb", idx)
                for idx, fb_ev in enumerate(prepared["chunk_fb_evidence"])
            }
            future_map.update({
                executor.submit(
                    run_llm_batch,
                    {"task_type": "pc", "idx": idx, "payload": pc_it},
                    context,
                ): ("pc", idx)
                for idx, pc_it in enumerate(chunk_pc_items)
                if pc_it
            })
            for future in as_completed(future_map):
                llm_results.append(future.result())

        enriched, llm_evidence, issue_messages = merge_llm_results(prepared, llm_results)
        for message in issue_messages:
            record_llm_issue(context, message)
        logger.info(
            "LLM enrichment complete: %d feedback, %d partial-credit across %d chunks",
            len(llm_evidence["feedback"]),
            len(llm_evidence["partial_credit"]),
            len(chunks),
        )
        return enriched, llm_evidence

    return prepared["enriched"] + [item["finding"] for item in prepared["llm_candidates"]], prepared["llm_evidence"]


# ---------------------------------------------------------------------------
# UX / Vision review helpers
# ---------------------------------------------------------------------------

_LINK_CSS_RE = re.compile(
    r"""<link\b[^>]*rel\s*=\s*["']stylesheet["'][^>]*>""",
    re.IGNORECASE,
)
_STYLE_TAG_RE = re.compile(
    r"<style[\s>]",
    re.IGNORECASE,
)


def build_ux_context_note(findings: List[Finding]) -> str | None:
    """Condense static-analysis findings into a short context note."""
    ids = {f.id for f in findings}
    warnings: list[str] = []

    if "HTML.MISSING_FILES" in ids or "HTML.REQ.MISSING_FILES" in ids:
        warnings.append("- No HTML files were found in the submission.")
    if "CSS.MISSING_FILES" in ids or "CSS.REQ.MISSING_FILES" in ids:
        warnings.append("- No CSS files were found.  The page has NO external stylesheet.")
    elif "CSS.NO_RULES" in ids:
        warnings.append(
            "- A CSS file exists but contains zero valid rules. "
            "The page is effectively unstyled."
        )
    if "JS.MISSING_FILES" in ids or "JS.REQ.MISSING_FILES" in ids:
        warnings.append("- No JavaScript files were found in the submission.")

    if not warnings:
        return None
    return "\n".join(warnings)


def build_per_page_context(page_name: str, html_path: Path | None) -> str:
    """Read the HTML source for *one* page and produce a short context note."""
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

    has_link = bool(_LINK_CSS_RE.search(source))
    has_style = bool(_STYLE_TAG_RE.search(source))

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


def capture_ux_screenshots(
    context: SubmissionContext,
    profile: str,
    assessors: list | None,
) -> dict[str, object]:
    """Capture screenshots for UX review."""
    from ams.assessors.playwright_assessor import PlaywrightAssessor as _PA

    pa = None
    if assessors:
        for assessor in assessors:
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


def evaluate_ux_screenshots(
    screenshots: Mapping[str, object],
    static_findings: List[Finding] | None,
    vision_analyst,
    vision_delay: float,
) -> tuple[List[Finding], list]:
    """Evaluate captured screenshots with the vision reviewer."""
    del static_findings
    import time

    from ams.llm.schemas import UXReviewResult

    context = screenshots["context"]
    profile = str(screenshots["profile"])
    page_shots = screenshots["page_shots"]
    html_lookup = screenshots["html_lookup"]
    ux_findings: List[Finding] = []
    ux_evidence: list = []

    if vision_analyst is None:
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
        context_note = build_per_page_context(
            page_name, html_lookup.get(page_name)
        )
        logger.debug(
            "UX: evaluating %s screenshot=%s size=%d",
            page_name, shot_path, shot_path.stat().st_size,
        )
        try:
            review = vision_analyst.review_ux(
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
            record_llm_issue(context, f"{page_name}: {review_feedback}")

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
        if vision_delay > 0 and entry is not page_shots[-1]:
            logger.debug("UX: sleeping %.1fs between pages", vision_delay)
            time.sleep(vision_delay)

    return ux_findings, ux_evidence


def run_ux_reviews(
    context: SubmissionContext,
    profile: str,
    assessors: list | None,
    vision_analyst,
    vision_delay: float,
    static_findings: List[Finding] | None = None,
) -> tuple[List[Finding], list]:
    """Run the UX review flow for a submission."""
    screenshots = capture_ux_screenshots(context, profile, assessors)
    page_shots = screenshots["page_shots"]
    if not page_shots:
        return [], []
    return evaluate_ux_screenshots(screenshots, static_findings, vision_analyst, vision_delay)
