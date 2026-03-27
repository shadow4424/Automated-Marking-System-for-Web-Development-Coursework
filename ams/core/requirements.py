from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence

from ams.assessors.html_parser import TagCountingParser
from ams.core.finding_ids import API as AID, CSS as CID, HTML as HID, JS as JID, PHP as PID, SQL as SID
from ams.core.models import Finding, FindingCategory, RequirementEvaluationResult, Severity, SubmissionContext
from ams.core.profiles import AggregationMode, BehavioralRule, ProfileSpec, RequirementDefinition, RequiredRule


@dataclass(frozen=True)
class _RuleFileResult:
    path: str
    count: int
    passed: bool
    snippet: str


class RequirementEvaluationEngine:
    def evaluate(
        self,
        context: SubmissionContext,
        findings: Iterable[Finding],
    ) -> tuple[List[RequirementEvaluationResult], List[Finding]]:
        resolved = context.resolved_config
        if resolved is None:
            raise ValueError("SubmissionContext.resolved_config must be populated before evaluating requirements")

        profile = resolved.profile
        profile_name = str(context.metadata.get("profile") or profile.name)
        findings_list = list(findings)
        results: List[RequirementEvaluationResult] = []
        generated_findings: List[Finding] = []

        for definition in resolved.requirement_definitions:
            result = self._evaluate_definition(
                definition=definition,
                context=context,
                findings=findings_list,
                profile=profile,
            )
            results.append(result)

            if definition.stage == "static" and definition.rule is not None:
                generated_findings.append(
                    self._finding_from_requirement(result, profile_name=profile_name)
                )

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

    def _evaluate_definition(
        self,
        *,
        definition: RequirementDefinition,
        context: SubmissionContext,
        findings: Sequence[Finding],
        profile: ProfileSpec,
    ) -> RequirementEvaluationResult:
        evaluator = definition.evaluator
        if evaluator == "required_rule" and isinstance(definition.rule, RequiredRule):
            return self._evaluate_static_rule(definition, context, profile)
        if evaluator == "behavioral_rule" and isinstance(definition.rule, BehavioralRule):
            return self._evaluate_behavioral_rule(definition, context)
        if evaluator == "browser_page_load":
            return self._evaluate_browser_page_load(definition, context)
        if evaluator == "browser_interaction":
            return self._evaluate_browser_interaction(definition, context)
        if evaluator == "layout_responsive":
            return self._evaluate_layout_requirement(definition, context, findings)
        if evaluator == "quality_penalty":
            return self._evaluate_quality_penalty(definition, findings)
        if evaluator == "api_usage_presence":
            return self._evaluate_api_usage(definition, context, findings)
        raise ValueError(f"Unsupported requirement evaluator: {evaluator}")

    def _evaluate_static_rule(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
        profile: ProfileSpec,
    ) -> RequirementEvaluationResult:
        component = definition.component
        required = profile.is_component_required(component)
        files = context.files_for(component, relevant_only=True)
        discovered_count = len(context.discovered_files.get(component, []))
        relevant_count = len(files)

        if not required:
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=False,
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "component_not_required",
                    "discovered_count": discovered_count,
                    "relevant_count": relevant_count,
                },
                skipped_reason="component_not_required",
            )

        if not files:
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score=0.0,
                status="FAIL",
                weight=definition.weight,
                required=True,
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "no_relevant_files",
                    "discovered_count": discovered_count,
                    "relevant_count": 0,
                },
                skipped_reason="no_relevant_files",
            )

        if (
            component == "html"
            and isinstance(definition.rule, RequiredRule)
            and definition.rule.id == "html.has_alt_attributes"
            and not self._html_files_have_images(files)
        ):
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=True,
                evidence={
                    "rule_id": definition.id,
                    "skip_reason": "not_applicable",
                    "discovered_count": discovered_count,
                    "relevant_count": relevant_count,
                },
                skipped_reason="not_applicable",
            )

        file_results = [
            self._evaluate_rule_on_file(component, definition.rule, path)
            for path in sorted(files)
        ]
        score, status = _aggregate_file_results(definition.aggregation_mode, file_results)
        count = max((item.count for item in file_results), default=0)
        snippets = [item.snippet for item in file_results if item.snippet]
        contributing_paths = [item.path for item in file_results]
        return RequirementEvaluationResult(
            requirement_id=definition.id,
            component=component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
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

    def _evaluate_behavioral_rule(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        rule = definition.rule
        assert isinstance(rule, BehavioralRule)
        if rule.test_type == "page_load":
            return self._evaluate_browser_page_load(definition, context)
        if rule.test_type == "js_interaction":
            return self._evaluate_browser_interaction(definition, context)

        statuses = []
        evidence_rows = []
        for item in context.behavioural_evidence:
            test_id = item.test_id.upper()
            if rule.test_type == "form_submit" and test_id.startswith(("PHP.FORM", "PHP.SMOKE")):
                statuses.append(item.status.lower())
                evidence_rows.append(item.to_dict())
            elif rule.test_type == "db_persist" and test_id.startswith("SQL.SQLITE_EXEC"):
                statuses.append(item.status.lower())
                evidence_rows.append(item.to_dict())
            elif rule.test_type == "api_exec" and test_id.startswith("API.EXEC"):
                statuses.append(item.status.lower())
                evidence_rows.append(item.to_dict())

        if not statuses:
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=definition.required,
                evidence={"reason": "no_runtime_evidence"},
                skipped_reason="no_runtime_evidence",
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
        return RequirementEvaluationResult(
            requirement_id=definition.id,
            component=definition.component,
            description=definition.description,
            stage=definition.stage,
            aggregation_mode=definition.aggregation_mode,
            score=score,
            status=status,
            weight=definition.weight,
            required=definition.required,
            evidence={"statuses": statuses, "evidence": evidence_rows[:3]},
            confidence_flags=confidence_flags,
            skipped_reason="runtime_skipped" if score == "SKIPPED" else None,
        )

    def _evaluate_browser_page_load(
        self,
        definition: RequirementDefinition,
        context: SubmissionContext,
    ) -> RequirementEvaluationResult:
        if not context.browser_evidence:
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=definition.required,
                evidence={"reason": "no_browser_evidence"},
                skipped_reason="no_browser_evidence",
                confidence_flags=["browser_skipped"],
            )

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
        return RequirementEvaluationResult(
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
        if not context.browser_evidence:
            return RequirementEvaluationResult(
                requirement_id=definition.id,
                component=definition.component,
                description=definition.description,
                stage=definition.stage,
                aggregation_mode=definition.aggregation_mode,
                score="SKIPPED",
                status="SKIPPED",
                weight=definition.weight,
                required=definition.required,
                evidence={"reason": "no_browser_evidence"},
                skipped_reason="no_browser_evidence",
                confidence_flags=["browser_skipped"],
            )

        if not context.files_for("js", relevant_only=True):
            return RequirementEvaluationResult(
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
        return RequirementEvaluationResult(
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
        relevant_files = context.files_for(definition.component, relevant_only=True)
        if definition.required and not relevant_files:
            return RequirementEvaluationResult(
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
        return RequirementEvaluationResult(
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
        return RequirementEvaluationResult(
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
        return RequirementEvaluationResult(
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

    def _evaluate_rule_on_file(
        self,
        component: str,
        rule: RequiredRule,
        path: Path,
    ) -> _RuleFileResult:
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
        component = result.component
        mapping = {
            "html": (HID.REQ_PASS, HID.REQ_FAIL, HID.REQ_SKIPPED, HID.REQ_MISSING_FILES),
            "css": (CID.REQ_PASS, CID.REQ_FAIL, CID.REQ_SKIPPED, CID.REQ_MISSING_FILES),
            "js": (JID.REQ_PASS, JID.REQ_FAIL, JID.REQ_SKIPPED, JID.REQ_MISSING_FILES),
            "php": (PID.REQ_PASS, PID.REQ_FAIL, PID.REQ_SKIPPED, PID.REQ_MISSING_FILES),
            "sql": (SID.REQ_PASS, SID.REQ_FAIL, SID.REQ_SKIPPED, SID.REQ_MISSING_FILES),
            "api": (AID.REQ_PASS, AID.REQ_FAIL, AID.REQ_SKIPPED, AID.REQ_MISSING_FILES),
        }
        passed_id, failed_id, skipped_id, missing_id = mapping[component]
        if result.status == "PASS":
            finding_id = passed_id
            severity = Severity.INFO
            finding_category = FindingCategory.STRUCTURE
        elif result.status == "SKIPPED":
            finding_id = skipped_id
            severity = Severity.SKIPPED
            finding_category = FindingCategory.OTHER
        elif result.skipped_reason == "no_relevant_files":
            finding_id = missing_id
            severity = Severity.FAIL
            finding_category = FindingCategory.MISSING
        else:
            finding_id = failed_id
            severity = Severity.WARN
            finding_category = FindingCategory.MISSING
        primary_snippet = ""
        snippets = result.evidence.get("snippets")
        if isinstance(snippets, list) and snippets:
            primary_snippet = str(snippets[0])
        evidence = dict(result.evidence)
        evidence.setdefault("rule_id", result.requirement_id)
        evidence.setdefault("weight", result.weight)
        evidence.setdefault("snippet", primary_snippet)
        evidence.setdefault("required", result.required)
        return Finding(
            id=finding_id,
            category=component,
            message=_build_requirement_message(result),
            severity=severity,
            evidence=evidence,
            source="requirement_evaluator",
            finding_category=finding_category,
            profile=profile_name,
            required=result.required,
        )

    def _has_complete_html_shell(self, context: SubmissionContext) -> bool:
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
        for path in files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            if "<img" in content:
                return True
        return False


def _aggregate_file_results(aggregation_mode: str, file_results: Sequence[_RuleFileResult]) -> tuple[float, str]:
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
    if result.status == "PASS":
        return f"Requirement {result.requirement_id} satisfied."
    if result.status == "SKIPPED":
        return f"Requirement {result.requirement_id} skipped: {result.skipped_reason or 'not applicable'}."
    if result.skipped_reason == "no_relevant_files":
        return f"Requirement {result.requirement_id} not evaluated: no relevant mapped files."
    return f"Requirement {result.requirement_id} not fully satisfied."


def _extract_snippet(component: str, content: str, pattern: str) -> str:
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
    return "\n".join(
        f"{line_no + 1:>4} | {lines[line_no]}"
        for line_no in range(min(10, len(lines)))
    )


def _evaluate_rule(component: str, rule: RequiredRule, content: str) -> tuple[int, bool]:
    lowered = content.lower()
    if component == "html":
        parser = TagCountingParser()
        parser.feed(content)
        selector = rule.selector.lower()
        if selector == "!doctype" or rule.id == "html.has_doctype":
            count = 1 if parser.has_doctype else 0
            return count, count >= rule.min_count
        if selector == "semantic" or rule.id == "html.has_semantic_structure":
            count = 1 if parser.has_semantic else 0
            return count, count >= rule.min_count
        if selector == "heading" or rule.id == "html.has_heading_hierarchy":
            count = 1 if parser.has_heading else 0
            return count, count >= rule.min_count
        if selector == "list" or rule.id == "html.has_lists":
            count = 1 if parser.has_list else 0
            return count, count >= rule.min_count
        if selector == "meta_charset" or rule.id == "html.has_meta_charset":
            count = 1 if parser.has_meta_charset else 0
            return count, count >= rule.min_count
        if selector == "meta_viewport" or rule.id == "html.has_meta_viewport":
            count = 1 if parser.has_meta_viewport else 0
            return count, count >= rule.min_count
        if selector == "html_lang" or rule.id == "html.has_lang_attribute":
            count = 1 if parser.has_html_lang else 0
            return count, count >= rule.min_count
        if selector == "img_alt" or rule.id == "html.has_alt_attributes":
            if parser.img_count == 0:
                return 1, True
            count = parser.img_with_alt
            return count, parser.img_with_alt == parser.img_count
        if selector == "label" or rule.id == "html.has_labels":
            count = parser.label_count
            return count, count >= rule.min_count
        count = parser.counts.get(selector, 0)
        return count, count >= rule.min_count

    if component == "css":
        brace_count = content.count("{")
        needle = rule.needle.lower()
        if needle == "{":
            return brace_count, brace_count >= rule.min_count
        if needle == "multiple_rules" or rule.id == "css.has_multiple_rules":
            return brace_count, brace_count >= rule.min_count
        if needle == "element_selector" or rule.id == "css.has_element_selector":
            selectors = ["body", "html", "h1", "h2", "h3", "p", "a", "div", "form", "input", "button", "nav", "main", "section"]
            count = sum(1 for selector in selectors if selector in lowered)
            return count, count >= rule.min_count
        if needle == "layout" or rule.id == "css.has_layout":
            props = ["margin", "padding", "display", "position", "width", "height", "top", "left", "right", "bottom"]
            count = sum(1 for item in props if item in lowered)
            return count, count >= rule.min_count
        if needle == "flexbox" or rule.id == "css.has_flexbox":
            count = 1 if ("display: flex" in lowered or "display:flex" in lowered) else 0
            return count, count >= rule.min_count
        if needle == "grid" or rule.id == "css.has_grid":
            count = 1 if ("display: grid" in lowered or "display:grid" in lowered) else 0
            return count, count >= rule.min_count
        if needle == "typography" or rule.id == "css.has_typography":
            props = ["font-family", "font-size", "line-height", "font-weight", "letter-spacing", "text-align"]
            count = sum(1 for item in props if item in lowered)
            return count, count >= rule.min_count
        if needle == "custom_properties" or rule.id == "css.has_custom_properties":
            count = content.count("--")
            return count, count >= rule.min_count
        if needle == "comments" or rule.id == "css.has_comments":
            count = content.count("/*")
            return count, count >= rule.min_count
        count = content.count(rule.needle)
        return count, count >= rule.min_count

    if component == "js":
        needle = rule.needle.lower()
        if needle == "dom_query" or rule.id == "js.has_dom_query":
            patterns = ["queryselector", "getelementbyid", "getelementsbyclass", "getelementsbytagname", "queryselectorall"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "dom_manipulation" or rule.id == "js.has_dom_manipulation":
            patterns = ["innerhtml", "textcontent", "appendchild", "removechild", "createelement", "setattribute", "classlist", "style."]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "loops" or rule.id == "js.has_loops":
            values = [
                "for " in lowered or "for(" in lowered,
                "while " in lowered or "while(" in lowered,
                ".foreach" in lowered,
                ".map(" in lowered,
            ]
            count = sum(values)
            return count, count >= rule.min_count
        if needle == "form_validation" or rule.id == "js.has_form_validation":
            patterns = [".value", "validity", "checkvalidity", "required", "pattern", ".length", "isnan", "typeof"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "async_patterns" or rule.id == "js.has_async_patterns":
            patterns = ["async ", "await ", "fetch(", "promise", ".then("]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "const_let" or rule.id == "js.has_const_let":
            count = (1 if "const " in lowered else 0) + (1 if "let " in lowered else 0)
            return count, count >= rule.min_count
        if needle == "`" or rule.id == "js.has_template_literals":
            count = content.count("`")
            return count, count >= rule.min_count
        count = lowered.count(needle)
        return count, count >= rule.min_count

    if component == "php":
        needle = rule.needle.lower()
        if needle == "request_superglobal" or rule.id == "php.uses_request":
            patterns = ["$_get", "$_post", "$_request"]
            count = sum(lowered.count(item) for item in patterns)
            return count, count >= rule.min_count
        if needle == "validation" or rule.id == "php.has_validation":
            funcs = ["isset", "empty", "filter_var", "filter_input", "is_numeric", "is_array"]
            count = sum(1 for item in funcs if item in lowered)
            return count, count >= rule.min_count
        if needle == "sanitisation" or rule.id == "php.has_sanitisation":
            funcs = ["htmlspecialchars", "htmlentities", "strip_tags", "addslashes", "mysqli_real_escape_string"]
            count = sum(1 for item in funcs if item in lowered)
            return count, count >= rule.min_count
        if needle == "output" or rule.id == "php.outputs":
            count = lowered.count("echo") + lowered.count("print")
            return count, count >= rule.min_count
        if needle == "database" or rule.id == "php.uses_database":
            patterns = ["mysqli", "pdo", "mysql_connect", "pg_connect"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "prepared_statements" or rule.id == "php.uses_prepared_statements":
            patterns = ["prepare(", "bind_param", "execute(", "bindvalue", "bindparam"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "sessions" or rule.id == "php.uses_sessions":
            patterns = ["session_start", "$_session", "session_destroy"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "loops" or rule.id == "php.has_loops":
            count = sum([
                "for " in lowered or "for(" in lowered,
                "while " in lowered or "while(" in lowered,
                "foreach" in lowered,
            ])
            return count, count >= rule.min_count
        if needle == "error_handling" or rule.id == "php.has_error_handling":
            patterns = ["try", "catch", "error_reporting", "set_error_handler", "exception"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        count = lowered.count(needle)
        return count, count >= rule.min_count

    if component == "sql":
        needle = rule.needle.lower()
        if needle == "foreign_key" or rule.id == "sql.has_foreign_key":
            patterns = ["foreign key", "references "]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "constraints" or rule.id == "sql.has_constraints":
            patterns = ["not null", "unique", "check ", "default "]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "data_types" or rule.id == "sql.has_data_types":
            patterns = ["int", "varchar", "text", "date", "datetime", "boolean", "decimal", "float", "char(", "timestamp"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "aggregate" or rule.id == "sql.has_aggregate":
            patterns = ["count(", "sum(", "avg(", "min(", "max(", "group by"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        count = lowered.count(needle)
        return count, count >= rule.min_count

    if component == "api":
        needle = rule.needle.lower()
        if needle == "json_encode" or rule.id == "api.json_encode":
            count = lowered.count("json_encode(")
            return count, count >= rule.min_count
        if needle == "application/json" or rule.id == "api.json_content_type":
            count = lowered.count("application/json")
            return count, count >= rule.min_count
        if needle == "request_method" or rule.id == "api.request_method":
            patterns = ['$_server["request_method"]', "$_server['request_method']", "request_method"]
            count = sum(1 for item in patterns if item in lowered)
            return count, count >= rule.min_count
        if needle == "json_decode" or rule.id == "api.json_decode":
            count = lowered.count("json_decode(")
            return count, count >= rule.min_count
        if needle == "fetch" or rule.id == "api.fetch":
            count = lowered.count("fetch(") + lowered.count("fetch (")
            return count, count >= rule.min_count
        count = lowered.count(needle)
        return count, count >= rule.min_count

    raise ValueError(f"Unsupported component: {component}")


__all__ = ["RequirementEvaluationEngine"]
