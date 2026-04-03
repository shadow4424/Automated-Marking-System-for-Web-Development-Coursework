from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from ams.core.finding_ids import API as AID, CSS as CID, HTML as HID, JS as JID, PHP as PID, SQL as SID
from ams.core.models import Finding, FindingCategory, RequirementEvaluationResult, Severity, SubmissionContext
from ams.core.profiles import AggregationMode, BehavioralRule, ProfileSpec, RequirementDefinition, RequiredRule
from ams.core.rule_evaluators import evaluate_rule as _evaluate_rule

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class _RuleFileResult:
    path: str
    count: int
    passed: bool
    snippet: str


def build_requirement_result(
    definition: RequirementDefinition | None = None,
    *,
    score: float | str,
    status: str,
    evidence: Mapping[str, object],
    component: str | None = None,
    required: bool | None = None,
    contributing_paths: Sequence[str] | None = None,
    skipped_reason: str | None = None,
    confidence_flags: Sequence[str] | None = None,
    # Legacy kwargs accepted for backward-compat (used when definition is None)
    requirement_id: str | None = None,
    description: str | None = None,
    stage: str | None = None,
    aggregation_mode: str | None = None,
    weight: float | None = None,
) -> RequirementEvaluationResult:
    """Build a requirement result using values from the definition by default."""
    if definition is None:
        definition = RequirementDefinition(
            id=requirement_id,
            component=component or "",
            description=description or "",
            stage=stage or "",
            aggregation_mode=aggregation_mode or "",
            weight=weight if weight is not None else 1.0,
            required=required if required is not None else True,
        )
    return RequirementEvaluationResult(
        requirement_id=definition.id,
        component=component or definition.component,
        description=definition.description,
        stage=definition.stage,
        aggregation_mode=definition.aggregation_mode,
        score=score,
        status=status,
        weight=weight if weight is not None else definition.weight,
        required=definition.required if required is None else required,
        evidence=evidence,
        contributing_paths=list(contributing_paths or []),
        skipped_reason=skipped_reason,
        confidence_flags=list(confidence_flags or []),
    )


def build_skipped_requirement_result(
    definition: RequirementDefinition,
    *,
    reason: str,
    component: str | None = None,
    required: bool | None = None,
    confidence_flags: Sequence[str] | None = None,
    evidence: Mapping[str, object] | None = None,
) -> RequirementEvaluationResult:
    """Build a standard skipped result."""
    skipped_evidence = dict(evidence or {})
    skipped_evidence.setdefault("reason", reason)
    return build_requirement_result(
        definition,
        component=component,
        score="SKIPPED",
        status="SKIPPED",
        required=required,
        evidence=skipped_evidence,
        skipped_reason=reason,
        confidence_flags=confidence_flags,
    )


class RequirementEvaluationEngine:

    _EVALUATOR_MAP = {
        "required_rule":           "_evaluate_static_rule",
        "behavioral_rule":         "_evaluate_behavioral_rule",
        "browser_page_load":       "_evaluate_browser_page_load",
        "browser_interaction":     "_evaluate_browser_interaction",
        "layout_responsive":       "_evaluate_layout_requirement",
        "quality_penalty":         "_evaluate_quality_penalty",
        "api_usage_presence":      "_evaluate_api_usage",
        "browser_console_clean":   "_evaluate_browser_console_clean",
        "browser_network_assets":  "_evaluate_browser_network_assets",
        "browser_dom_structure":   "_evaluate_browser_dom_structure",
        "browser_accessibility":   "_evaluate_browser_accessibility",
    }

    _CROSS_FILE_MAP = {
        "cross_file_php_form":      "php_form_alignment",
        "cross_file_sql_alignment": "sql_alignment",
        "cross_file_api_alignment": "api_alignment",
    }

    _COMPONENT_IDS = {
        "html": (HID.REQ_PASS, HID.REQ_FAIL, HID.REQ_SKIPPED, HID.REQ_MISSING_FILES),
        "css":  (CID.REQ_PASS, CID.REQ_FAIL, CID.REQ_SKIPPED, CID.REQ_MISSING_FILES),
        "js":   (JID.REQ_PASS, JID.REQ_FAIL, JID.REQ_SKIPPED, JID.REQ_MISSING_FILES),
        "php":  (PID.REQ_PASS, PID.REQ_FAIL, PID.REQ_SKIPPED, PID.REQ_MISSING_FILES),
        "sql":  (SID.REQ_PASS, SID.REQ_FAIL, SID.REQ_SKIPPED, SID.REQ_MISSING_FILES),
        "api":  (AID.REQ_PASS, AID.REQ_FAIL, AID.REQ_SKIPPED, AID.REQ_MISSING_FILES),
    }

    def evaluate(
        self,
        context: SubmissionContext,
        findings: Iterable[Finding],
    ) -> tuple[List[RequirementEvaluationResult], List[Finding]]:
        """Return evaluate."""
        resolved = context.resolved_config
        if resolved is None:
            raise ValueError("SubmissionContext.resolved_config must be populated before evaluating requirements")

        profile = resolved.profile
        profile_name = str(context.metadata.get("profile") or profile.name)
        findings_list = list(findings)

        results = [
            self._safe_evaluate_definition(defn, context, findings_list, profile)
            for defn in resolved.requirement_definitions
        ]
        generated_findings = [
            self._finding_from_requirement(r, profile_name=profile_name)
            for r, defn in zip(results, resolved.requirement_definitions)
            if defn.stage == "static" and defn.rule is not None
        ]

        for component in ("html", "css", "js", "php", "sql", "api"):
            if profile.is_component_required(component):
                continue
            generated_findings.append(
                self._finding_from_requirement(
                    RequirementEvaluationResult(
                        requirement_id=f"{component}.component_skipped",
                        component=component,
                        description=f"{component.upper()} requirements skipped for this profile.",
                        stage="static",
                        aggregation_mode=AggregationMode.ANY.value,
                        score="SKIPPED",
                        status="SKIPPED",
                        weight=0.0,
                        required=False,
                        evidence={
                            "rule_id": f"{component}.component_skipped",
                            "profile": profile_name,
                            "required": False,
                        },
                        skipped_reason="component_not_required",
                    ),
                    profile_name=profile_name,
                )
            )

        ordered_results = sorted(
            results,
            key=lambda item: (item.component, item.stage, item.requirement_id),
        )
        ordered_findings = sorted(
            generated_findings,
            key=lambda item: (item.category, item.id, str(item.evidence.get("rule_id", ""))),
        )
        context.requirement_results = ordered_results
        return ordered_results, ordered_findings

    def _safe_evaluate_definition(self, definition, context, findings_list, profile):
        """Wrap _evaluate_definition with error handling."""
        try:
            return self._evaluate_definition(
                definition=definition, context=context,
                findings=findings_list, profile=profile,
            )
        except Exception as exc:
            logger.exception("Failed to evaluate requirement %s: %s", definition.id, exc)
            return build_skipped_requirement_result(definition, reason="evaluation_error")

    def _evaluate_definition(
        self,
        *,
        definition: RequirementDefinition,
        context: SubmissionContext,
        findings: Sequence[Finding],
        profile: ProfileSpec,
    ) -> RequirementEvaluationResult:
        """Evaluate a single requirement definition via registry dispatch."""
        evaluator = definition.evaluator

        # Cross-file evaluators with an extra result_key argument
        cross_key = self._CROSS_FILE_MAP.get(evaluator)
        if cross_key is not None:
            return self._evaluate_cross_file_result(definition, context, cross_key)

        handler_name = self._EVALUATOR_MAP.get(evaluator)
        if handler_name is None:
            raise ValueError(f"Unsupported requirement evaluator: {evaluator}")

        handler = getattr(self, handler_name)

        # Type-guarded evaluators
        if evaluator == "required_rule":
            if not isinstance(definition.rule, RequiredRule):
                raise ValueError(f"Unsupported requirement evaluator: {evaluator}")
            return handler(definition, context, profile)
        if evaluator == "behavioral_rule":
            if not isinstance(definition.rule, BehavioralRule):
                raise ValueError(f"Unsupported requirement evaluator: {evaluator}")
            return handler(definition, context)
        # Evaluators needing findings
        if evaluator in {"layout_responsive", "api_usage_presence"}:
            return handler(definition, context, findings)
        if evaluator == "quality_penalty":
            return handler(definition, findings)
        # Default: (definition, context) signature
        return handler(definition, context)

    def _load_files_for_rule(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> tuple[List[Path], int, int]:
        """Load the files for rule."""
        component = definition.component
        files = context.files_for(component, relevant_only=True)
        discovered_count = len(context.discovered_files.get(component, []))
        relevant_count = len(files)
        return files, discovered_count, relevant_count

    def _analyse_static_rule(
        self,
        definition: RequirementDefinition,
        files: Sequence[Path],
        discovered_count: int,
        relevant_count: int,
    ) -> RequirementEvaluationResult:
        """Analyse the static rule."""
        component = definition.component
        if (
            component == "html"
            and isinstance(definition.rule, RequiredRule)
            and definition.rule.id == "html.has_alt_attributes"
            and not self._html_files_have_images(files)
        ):
            return build_skipped_requirement_result(
                definition,
                component=component,
                required=True,
                reason="not_applicable",
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "not_applicable",
                    "discovered_count": discovered_count,
                    "relevant_count": relevant_count,
                },
            )

        file_results = [
            self._evaluate_rule_on_file(component, definition.rule, path)
            for path in sorted(files)
        ]
        score, status = _aggregate_file_results(definition.aggregation_mode, file_results)
        count = max((item.count for item in file_results), default=0)
        snippets = [item.snippet for item in file_results if item.snippet]
        contributing_paths = [item.path for item in file_results]
        return build_requirement_result(
            definition,
            component=component,
            score=score,
            status=status,
            required=True,
            evidence={
                "rule_id": definition.id,
                "count": count,
                "matched_paths": [item.path for item in file_results if item.count > 0],
                "discovered_count": discovered_count,
                "relevant_count": relevant_count,
                "snippets": snippets[:3],
            },
            contributing_paths=contributing_paths,
        )

    def _evaluate_static_rule(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
        profile: ProfileSpec,
    ) -> RequirementEvaluationResult:
        """Evaluate the static rule."""
        component = definition.component
        required = profile.is_component_required(component)
        files, discovered_count, relevant_count = self._load_files_for_rule(definition, context)
        if not required:
            return build_skipped_requirement_result(
                definition,
                component=component,
                required=False,
                reason="component_not_required",
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "component_not_required",
                    "discovered_count": discovered_count,
                    "relevant_count": relevant_count,
                },
            )
        if not files:
            return build_requirement_result(
                definition,
                component=component,
                score=0.0,
                status="FAIL",
                required=True,
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "no_relevant_files",
                    "discovered_count": discovered_count,
                    "relevant_count": 0,
                },
                skipped_reason="no_relevant_files",
            )
        return self._analyse_static_rule(definition, files, discovered_count, relevant_count)

    def _collect_behavioral_evidence(
        self,
        rule: BehavioralRule,
        context: SubmissionContext,
    ) -> tuple[List[str], List[Mapping[str, object]]]:
        """Prepare the behavioural context."""
        statuses = []
        evidence_rows: List[Mapping[str, object]] = []
        prefix_map: dict[str, tuple[str, ...]] = {
            "form_submit": ("PHP.FORM", "PHP.SMOKE"),
            "db_persist": ("SQL.SQLITE_EXEC",),
            "api_exec": ("API.EXEC",),
            "hover_check": ("BEHAVIOUR.HOVER",),
            "viewport_resize": ("BEHAVIOUR.VIEWPORT",),
        }
        if rule.test_type in {"calculator_sequence", "calculator_display", "calculator_operator"}:
            prefixes = ("BEHAVIOUR.CALCULATOR",)
        else:
            prefixes = prefix_map.get(rule.test_type, ())
        for item in context.behavioural_evidence:
            test_id = item.test_id.upper()
            if prefixes and test_id.startswith(prefixes):
                statuses.append(item.status.lower())
                evidence_rows.append(item.to_dict())
        return statuses, evidence_rows

    def _run_behavioral_check(
        self,
        definition: RequirementDefinition,
        statuses: Sequence[str],
        evidence_rows: Sequence[Mapping[str, object]],
    ) -> RequirementEvaluationResult:
        """Run the behavioural check."""
        if not statuses:
            return build_skipped_requirement_result(
                definition,
                reason="no_runtime_evidence",
                confidence_flags=["runtime_skipped"],
            )

        if "pass" in statuses:
            score = 1.0 if statuses.count("pass") == len(statuses) else 0.5
            status = "PASS" if score == 1.0 else "PARTIAL"
        elif any(state in {"fail", "timeout", "error"} for state in statuses):
            score = 0.5
            status = "PARTIAL"
        elif all(state == "skipped" for state in statuses):
            score = "SKIPPED"
            status = "SKIPPED"
        else:
            score = 0.0
            status = "FAIL"

        confidence_flags = []
        if any(state == "skipped" for state in statuses):
            confidence_flags.append("runtime_skipped")
        if any(state in {"fail", "timeout", "error"} for state in statuses):
            confidence_flags.append("runtime_failure")
        return build_requirement_result(
            definition,
            score=score,
            status=status,
            evidence={"statuses": statuses, "evidence": evidence_rows[:3]},
            confidence_flags=confidence_flags,
            skipped_reason="runtime_skipped" if score == "SKIPPED" else None,
        )

    def _evaluate_behavioral_rule(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate the behavioural rule."""
        rule = definition.rule
        assert isinstance(rule, BehavioralRule)
        if rule.test_type == "page_load":
            return self._evaluate_browser_page_load(definition, context)
        if rule.test_type == "js_interaction":
            return self._evaluate_browser_interaction(definition, context)
        statuses, evidence_rows = self._collect_behavioral_evidence(rule, context)
        return self._run_behavioral_check(definition, statuses, evidence_rows)

    def _skip_without_browser_evidence(
        self,
        definition: RequirementDefinition,
    ) -> RequirementEvaluationResult:
        """Return the standard skipped result for missing browser evidence."""
        return build_skipped_requirement_result(
            definition,
            reason="no_browser_evidence",
            confidence_flags=["browser_skipped"],
        )

    def _evaluate_browser_page_load(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate the browser page load."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)

        browser = context.browser_evidence[0]
        status = (browser.status or "").lower()
        html_shell_complete = self._has_complete_html_shell(context)
        if status == "pass":
            score = 1.0 if html_shell_complete else 0.5
            result_status = "PASS" if score == 1.0 else "PARTIAL"
        elif status in {"fail", "timeout", "error"}:
            score = 0.5 if context.files_for("html", relevant_only=True) else 0.0
            result_status = "PARTIAL" if score == 0.5 else "FAIL"
        else:
            score = "SKIPPED"
            result_status = "SKIPPED"
        confidence_flags = []
        if status in {"fail", "timeout", "error"}:
            confidence_flags.append("browser_failure")
        if status == "skipped":
            confidence_flags.append("browser_skipped")
        return build_requirement_result(
            definition,
            score=score,
            status=result_status,
            evidence=browser.to_dict(),
            contributing_paths=[
                str(path) for path in context.files_for("html", relevant_only=True)[:2]
            ],
            skipped_reason="browser_skipped" if score == "SKIPPED" else None,
            confidence_flags=confidence_flags,
        )

    def _evaluate_browser_interaction(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate the browser interaction."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)

        if not context.files_for("js", relevant_only=True):
            return build_requirement_result(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=definition.required,
                evidence={"reason": "no_js_files"},
                skipped_reason="no_js_files",
                confidence_flags=["js_files_missing"],
            )

        browser = context.browser_evidence[0]
        actions = list(browser.actions or [])
        interacted = any(item.get("type") in {"form_submit", "click"} for item in actions)
        if browser.status == "pass" and interacted and not browser.console_errors:
            score = 1.0
            result_status = "PASS"
        elif browser.status == "pass" and (interacted or browser.console_errors):
            score = 0.5
            result_status = "PARTIAL"
        elif browser.status in {"fail", "timeout", "error"}:
            score = 0.5 if context.files_for("js", relevant_only=True) else 0.0
            result_status = "PARTIAL" if score == 0.5 else "FAIL"
        else:
            score = "SKIPPED"
            result_status = "SKIPPED"
        confidence_flags = []
        if browser.console_errors:
            confidence_flags.append("browser_console_errors")
        if browser.status in {"fail", "timeout", "error"}:
            confidence_flags.append("browser_failure")
        if browser.status == "skipped":
            confidence_flags.append("browser_skipped")
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=result_status,
            weight=definition.weight,
            required=definition.required,
            evidence=browser.to_dict(),
            contributing_paths=[
                str(path) for path in context.files_for("js", relevant_only=True)[:2]
            ],
            skipped_reason="browser_skipped" if score == "SKIPPED" else None,
            confidence_flags=confidence_flags,
        )

    def _evaluate_layout_requirement(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
        findings: Sequence[Finding],
    ) -> RequirementEvaluationResult:
        """Evaluate the layout requirement."""
        relevant_files = context.files_for(definition.component, relevant_only=True)
        if definition.required and not relevant_files:
            return build_requirement_result(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score=0.0,
                status="FAIL",
                weight=definition.weight,
                required=True,
                evidence={"reason": "no_relevant_files"},
                skipped_reason="no_relevant_files",
            )
        media_query_present = any(
            finding.category == "css"
            and (
                finding.evidence.get("media_queries", 0) if isinstance(finding.evidence, Mapping) else 0
            ) > 0
            for finding in findings
            if finding.id in {CID.EVIDENCE, CID.REQ_PASS}
        )
        browser_pass = bool(context.browser_evidence and context.browser_evidence[0].status == "pass")
        if browser_pass and media_query_present:
            score = 1.0
            status = "PASS"
        elif browser_pass or media_query_present:
            score = 0.5
            status = "PARTIAL"
        elif not context.browser_evidence:
            score = "SKIPPED"
            status = "SKIPPED"
        else:
            score = 0.0
            status = "FAIL"
        confidence_flags = []
        if score == "SKIPPED":
            confidence_flags.append("layout_skipped")
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={
                "browser_pass": browser_pass,
                "media_query_present": media_query_present,
            },
            skipped_reason="no_browser_evidence" if score == "SKIPPED" else None,
            confidence_flags=confidence_flags,
        )

    def _evaluate_quality_penalty(
        self,
        definition: RequirementDefinition,
        findings: Sequence[Finding],
    ) -> RequirementEvaluationResult:
        """Evaluate the quality penalty."""
        relevant = [
            finding
            for finding in findings
            if (
                finding.category == definition.component
                or finding.category == "consistency"
            )
            and (
                ".QUALITY." in finding.id
                or ".SECURITY." in finding.id
                or "CONSISTENCY." in finding.id
            )
        ]
        penalty = 0.0
        for finding in relevant:
            if finding.severity == Severity.FAIL:
                penalty += 0.2
            elif finding.severity == Severity.WARN:
                penalty += 0.1
        penalty = min(0.5, penalty)
        if penalty == 0:
            score = 1.0
            status = "PASS"
        elif penalty < 0.5:
            score = 0.5
            status = "PARTIAL"
        else:
            score = 0.0
            status = "FAIL"
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={
                "penalty": penalty,
                "issue_ids": [finding.id for finding in relevant],
            },
        )

    def _evaluate_api_usage(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
        findings: Sequence[Finding],
    ) -> RequirementEvaluationResult:
        """Evaluate the api usage."""
        api_paths = list(context.role_mapping.relevant_files.get("api", [])) if context.role_mapping else []
        api_findings = [
            finding
            for finding in findings
            if finding.id in {JID.API_EVIDENCE, PID.API_EVIDENCE}
        ]
        if api_paths or api_findings:
            score = 1.0
            status = "PASS"
        elif context.discovered_files.get("js") or context.discovered_files.get("php"):
            score = 0.0
            status = "FAIL"
        else:
            score = "SKIPPED"
            status = "SKIPPED"
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={
                "api_paths": api_paths,
                "api_evidence_ids": [finding.id for finding in api_findings],
            },
            contributing_paths=list(api_paths),
            skipped_reason="no_api_layer" if score == "SKIPPED" else None,
            confidence_flags=["runtime_skipped"] if score == "SKIPPED" else [],
        )

    def _evaluate_cross_file_result(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
        result_key: str,
    ) -> RequirementEvaluationResult:
        """Evaluate a cross-file alignment result from context metadata."""
        cross_file_results: dict = context.metadata.get("cross_file_results", {})  # type: ignore[assignment]
        result = cross_file_results.get(result_key) if cross_file_results else None
        if result is None:
            return build_requirement_result(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=definition.required,
                evidence={"reason": "cross_file_check_not_run"},
                skipped_reason="cross_file_check_not_run",
                confidence_flags=["cross_file_skipped"],
            )
        score = result.get("score", 0.0)
        status = result.get("status", "FAIL")
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence=result.get("evidence", {}),
        )

    def _evaluate_browser_console_clean(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate whether the browser console is free of fatal errors."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)
        browser = context.browser_evidence[0]
        errors = browser.console_errors or []
        fatal_errors = [e for e in errors if isinstance(e, str) and any(
            kw in e.lower() for kw in ("uncaught", "typeerror", "referenceerror", "syntaxerror")
        )]
        if not fatal_errors:
            score, status = 1.0, "PASS"
        elif len(fatal_errors) <= 2:
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        return build_requirement_result(
            definition,
            score=score,
            status=status,
            evidence={"fatal_error_count": len(fatal_errors), "console_errors": errors[:5]},
            confidence_flags=["browser_console"] if fatal_errors else [],
        )

    def _evaluate_browser_network_assets(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate whether all linked network assets resolved successfully."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)
        browser = context.browser_evidence[0]
        network_errors = getattr(browser, "network_errors", []) or []
        asset_errors = [e for e in network_errors if isinstance(e, str) and any(
            ext in e.lower() for ext in (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".woff")
        )]
        if not asset_errors:
            score, status = 1.0, "PASS"
        elif len(asset_errors) <= 2:
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        return build_requirement_result(
            definition,
            score=score,
            status=status,
            evidence={"asset_error_count": len(asset_errors), "network_errors": network_errors[:5]},
        )

    def _evaluate_browser_dom_structure(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate DOM structure from browser evidence."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)
        browser = context.browser_evidence[0]
        dom_structure = getattr(browser, "dom_structure", None) or {}
        has_body = bool(dom_structure.get("has_body", True))  # Default True if not checked
        element_count = dom_structure.get("element_count", 0)
        if browser.status == "pass" and has_body and element_count > 0:
            score, status = 1.0, "PASS"
        elif browser.status == "pass":
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.component,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={"dom_structure": dom_structure, "browser_status": browser.status},
        )

    def _evaluate_browser_accessibility(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        """Evaluate whether interactive elements are accessible (have labels/aria)."""
        if not context.browser_evidence:
            return self._skip_without_browser_evidence(definition)
        browser = context.browser_evidence[0]
        # Check via static HTML analysis if browser check not available
        html_files = context.files_for("html", relevant_only=True)
        has_labels = False
        has_aria = False
        for html_file in html_files[:2]:
            try:
                content = html_file.read_text(encoding="utf-8", errors="replace").lower()
                has_labels = has_labels or "for=" in content or "<label" in content
                has_aria = has_aria or "aria-label" in content or "aria-labelledby" in content or "role=" in content
            except OSError:
                pass
        if (has_labels or has_aria) and browser.status == "pass":
            score, status = 1.0, "PASS"
        elif has_labels or has_aria:
            score, status = 0.5, "PARTIAL"
        else:
            score, status = 0.0, "FAIL"
        return build_requirement_result(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={"has_labels": has_labels, "has_aria": has_aria, "browser_status": browser.status},
        )

    def _evaluate_rule_on_file(
        self,
        component: str,
        rule: RequiredRule,
        path: Path,
    ) -> _RuleFileResult:
        """Evaluate the rule on file."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        count, passed = _evaluate_rule(component, rule, content)
        snippet = _extract_snippet(component, content, rule.pattern)
        return _RuleFileResult(
            path=str(path),
            count=count,
            passed=passed,
            snippet=snippet,
        )

    def _finding_from_requirement(
        self,
        result: RequirementEvaluationResult,
        *,
        profile_name: str,
    ) -> Finding:
        """Convert a requirement evaluation result into a Finding."""
        passed_id, failed_id, skipped_id, missing_id = self._COMPONENT_IDS[result.component]

        if result.status == "PASS":
            finding_id, severity, finding_category = passed_id, Severity.INFO, FindingCategory.STRUCTURE
        elif result.status == "SKIPPED":
            finding_id, severity, finding_category = skipped_id, Severity.SKIPPED, FindingCategory.OTHER
        elif result.skipped_reason == "no_relevant_files":
            finding_id, severity, finding_category = missing_id, Severity.FAIL, FindingCategory.MISSING
        else:
            finding_id, severity, finding_category = failed_id, Severity.WARN, FindingCategory.MISSING

        snippets = result.evidence.get("snippets")
        primary_snippet = str(snippets[0]) if isinstance(snippets, list) and snippets else ""
        evidence = dict(result.evidence)
        evidence.setdefault("rule_id", result.requirement_id)
        evidence.setdefault("weight", result.weight)
        evidence.setdefault("snippet", primary_snippet)
        evidence.setdefault("required", result.required)

        return Finding(
            id=finding_id,
            category=result.component,
            message=_build_requirement_message(result),
            severity=severity,
            evidence=evidence,
            source="requirement_evaluator",
            finding_category=finding_category,
            profile=profile_name,
            required=result.required,
        )

    def _has_complete_html_shell(self, context: SubmissionContext) -> bool:
        """Return complete html shell."""
        html_files = context.files_for("html", relevant_only=True)
        if not html_files:
            return False
        try:
            content = html_files[0].read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return False
        required_tokens = ("<!doctype", "<html", "<head", "<body")
        return all(token in content for token in required_tokens)

    def _html_files_have_images(self, files: Sequence[Path]) -> bool:
        """Return files have images."""
        for path in files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if "<img" in content:
                return True
        return False


def _aggregate_file_results(aggregation_mode: str, file_results: Sequence[_RuleFileResult]) -> tuple[float, str]:
    """Return file results."""
    if not file_results:
        return 0.0, "FAIL"

    passed = [item for item in file_results if item.passed]
    touched = [item for item in file_results if item.count > 0]
    if aggregation_mode == AggregationMode.ALL_RELEVANT.value:
        if len(passed) == len(file_results):
            return 1.0, "PASS"
        if passed or touched:
            return 0.5, "PARTIAL"
        return 0.0, "FAIL"
    if aggregation_mode == AggregationMode.EXPECTED_SET.value:
        if len(passed) == len(file_results):
            return 1.0, "PASS"
        if passed or touched:
            return 0.5, "PARTIAL"
        return 0.0, "FAIL"
    if passed:
        return 1.0, "PASS"
    if touched:
        return 0.5, "PARTIAL"
    return 0.0, "FAIL"


def _build_requirement_message(result: RequirementEvaluationResult) -> str:
    """Build the requirement message."""
    if result.status == "PASS":
        return f"Requirement {result.requirement_id} satisfied."
    if result.status == "SKIPPED":
        return f"Requirement {result.requirement_id} skipped: {result.skipped_reason or 'not applicable'}."
    if result.skipped_reason == "no_relevant_files":
        return f"Requirement {result.requirement_id} not evaluated: no relevant mapped files."
    return f"Requirement {result.requirement_id} not fully satisfied."


def _extract_snippet(component: str, content: str, pattern: str) -> str:
    """Extract the snippet."""
    if not content.strip():
        return "(file is empty)"
    lines = content.splitlines()
    search = pattern.lower()
    html_like = component in {"html", "php"}
    if html_like and not search.startswith("<"):
        search = f"<{search.split('[', 1)[0].split('.', 1)[0].split('#', 1)[0].strip()}"
    for index, line in enumerate(lines):
        if search and search in line.lower():
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            return "\n".join(f"{line_no + 1:>4} | {lines[line_no]}" for line_no in range(start, end))

    # No direct token match: provide both start and end context to avoid
    # Hiding late-file implementation attempts (for example legacy handlers).
    if len(lines) <= 20:
        return "\n".join(
            f"{line_no + 1:>4} | {lines[line_no]}"
            for line_no in range(len(lines))
        )

    head = [f"{line_no + 1:>4} | {lines[line_no]}" for line_no in range(10)]
    tail_start = len(lines) - 10
    tail = [f"{line_no + 1:>4} | {lines[line_no]}" for line_no in range(tail_start, len(lines))]
    return "\n".join(head + ["  ... | ..."] + tail)


__all__ = ["RequirementEvaluationEngine"]
