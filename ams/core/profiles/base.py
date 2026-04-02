from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class RequiredRule:
    """Unified rule definition for all assessable components."""
    id: str
    description: str
    pattern: str
    min_count: int = 1
    weight: float = 1.0  # Weight for scoring (higher = more important)
    # LLM Metadata (Phase 0)
    category: str = ""
    partial_allowed: bool = False
    partial_range: tuple[float, float] = (0.0, 0.0)
    severity: str = "medium"  # "low", "medium", "high"
    llm_guidance: str = ""
    pii_sensitivity: bool = False  # True if evidence may contain student IDs/names
    visual_check: bool = False  # Phase 3: True if rule requires vision capabilities
    # Phase D: Fair Partial Credit
    attempt_signal: Optional[str] = None  # Regex pattern to detect attempt
    related_rules: tuple[str, ...] = ()  # Related rule IDs for conflict resolution


    # Backward-compatible property aliases so that existing assessor code
    # Using rule.selector or rule.needle keeps working.

    @property
    def selector(self) -> str:  # Used by HTML assessors
        """Return selector."""
        return self.pattern

    @property
    def needle(self) -> str:  # Used by CSS / JS / PHP / SQL assessors
        """Return search needle."""
        return self.pattern


def _make_rule_factory(legacy_kwarg: str):
    """Return a factory that accepts *legacy_kwarg* and maps it to pattern."""
    def _factory(*args, **kwargs):
        """Return factory."""
        if legacy_kwarg in kwargs:
            kwargs["pattern"] = kwargs.pop(legacy_kwarg)
        return RequiredRule(*args, **kwargs)
    _factory.__qualname__ = _factory.__name__ = f"Required{legacy_kwarg.title().replace('_','')}Rule"
    return _factory


# Backward-compatible constructors: accept the old keyword names.
RequiredHTMLRule = _make_rule_factory("selector")
RequiredCSSRule  = _make_rule_factory("needle")
RequiredJSRule   = _make_rule_factory("needle")
RequiredPHPRule  = _make_rule_factory("needle")
RequiredSQLRule  = _make_rule_factory("needle")
RequiredAPIRule  = _make_rule_factory("needle")


@dataclass(frozen=True)
class BehavioralRule:
    """Dynamic behavioural rule for runtime testing."""
    id: str
    description: str
    test_type: str  # "page_load", "form_submit", "js_interaction", "db_persist"
    component: str  # "html", "js", "php", "sql"
    weight: float = 1.0  # Weight for scoring
    # LLM Metadata (Phase 0)
    category: str = ""
    partial_allowed: bool = False
    partial_range: tuple[float, float] = (0.0, 0.0)
    severity: str = "medium"
    llm_guidance: str = ""
    pii_sensitivity: bool = False
    visual_check: bool = False  # Phase 3: True if rule requires vision capabilities
    # Phase D: Fair Partial Credit
    attempt_signal: Optional[str] = None
    related_rules: tuple[str, ...] = ()


class AggregationMode(str, Enum):
    ANY = "ANY"
    ALL_RELEVANT = "ALL_RELEVANT"
    EXPECTED_SET = "EXPECTED_SET"
    CAPPED_PENALTY = "CAPPED_PENALTY"


@dataclass(frozen=True)
class RequirementDefinition:
    id: str
    component: str
    description: str
    stage: str
    aggregation_mode: str
    weight: float = 1.0
    required: bool = True
    rule: RequiredRule | BehavioralRule | None = None
    evaluator: str = ""
    expected_roles: tuple[str, ...] = ()
    skip_reason: str | None = None

    def to_dict(self) -> Dict[str, object]:
        """Return this requirement definition as a dictionary."""
        return {
            "id": self.id,
            "component": self.component,
            "description": self.description,
            "stage": self.stage,
            "aggregation_mode": self.aggregation_mode,
            "weight": self.weight,
            "required": self.required,
            "evaluator": self.evaluator,
            "expected_roles": list(self.expected_roles),
        }


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    required_html: List[RequiredRule]
    required_css: List[RequiredRule]
    required_js: List[RequiredRule]
    required_php: List[RequiredRule]
    required_sql: List[RequiredRule]
    behavioral_rules: List[BehavioralRule]
    required_files: List[str]
    relevant_artefacts: List[str]
    optional_components: List[str] = field(default_factory=list)
    required_api: List[RequiredRule] = field(default_factory=list)
    expected_layers: List[str] = field(default_factory=list)
    enabled_static_checks: List[str] = field(default_factory=list)
    enabled_behavioural_checks: List[str] = field(default_factory=list)
    enabled_browser_checks: List[str] = field(default_factory=list)
    enabled_layout_checks: List[str] = field(default_factory=list)
    expected_entrypoint_types: List[str] = field(default_factory=list)
    component_weights: Dict[str, float] = field(default_factory=dict)
    missing_component_treatment: Dict[str, str] = field(default_factory=dict)
    role_expectations: Dict[str, str] = field(default_factory=dict)
    frontend_only: bool = False
    custom_config_path: str | None = None
    aliases: tuple[str, ...] = ()

    def is_component_required(self, component: str) -> bool:
        """Check whether a component is required for this profile."""
        return component in self.relevant_artefacts

    def has_required_rules(self, component: str) -> bool:
        """Check whether a component has required rules defined for this profile."""
        rule_map = {
            "html": self.required_html,
            "css": self.required_css,
            "js": self.required_js,
            "php": self.required_php,
            "sql": self.required_sql,
            "api": self.required_api,
        }
        rules = rule_map.get(component, [])
        return len(rules) > 0

    def component_rule_map(self) -> Dict[str, List[RequiredRule]]:
        """Return rule map."""
        return {
            "html": list(self.required_html),
            "css": list(self.required_css),
            "js": list(self.required_js),
            "php": list(self.required_php),
            "sql": list(self.required_sql),
            "api": list(self.required_api),
        }

    def enabled_components(self) -> List[str]:
        """Return components."""
        ordered = list(self.relevant_artefacts)
        for component in self.optional_components:
            if component not in ordered:
                ordered.append(component)
        return ordered

    def get_component_weight(self, component: str) -> float:
        """Return component weight."""
        if self.component_weights:
            return float(self.component_weights.get(component, 0.0))
        required = list(self.relevant_artefacts)
        return 1.0 / len(required) if required and component in required else 0.0

    def to_dict(self) -> Dict[str, object]:
        """Return this profile spec as a dictionary."""
        return {
            "name": self.name,
            "required_components": list(self.relevant_artefacts),
            "optional_components": list(self.optional_components),
            "expected_layers": list(self.expected_layers),
            "enabled_static_checks": list(self.enabled_static_checks),
            "enabled_behavioural_checks": list(self.enabled_behavioural_checks),
            "enabled_browser_checks": list(self.enabled_browser_checks),
            "enabled_layout_checks": list(self.enabled_layout_checks),
            "expected_entrypoint_types": list(self.expected_entrypoint_types),
            "component_weights": dict(self.component_weights),
            "missing_component_treatment": dict(self.missing_component_treatment),
            "role_expectations": dict(self.role_expectations),
            "frontend_only": self.frontend_only,
            "custom_config_path": self.custom_config_path,
            "aliases": list(self.aliases),
        }

    def build_requirement_definitions(self) -> List[RequirementDefinition]:
        """Build the requirement definitions."""
        definitions: List[RequirementDefinition] = []
        for component, rules in self.component_rule_map().items():
            required = self.is_component_required(component)
            for rule in rules:
                definitions.append(
                    RequirementDefinition(
                        id=rule.id,
                        component=component,
                        description=rule.description,
                        stage="static",
                        aggregation_mode=_static_aggregation_mode(component, rule),
                        weight=float(getattr(rule, "weight", 1.0)),
                        required=required,
                        rule=rule,
                        evaluator="required_rule",
                        expected_roles=_expected_roles_for_component(component),
                    )
                )

        for rule in self.behavioral_rules:
            definitions.append(
                RequirementDefinition(
                    id=rule.id,
                    component=rule.component,
                    description=rule.description,
                    stage="runtime",
                    aggregation_mode=_behavioural_aggregation_mode(rule),
                    weight=float(getattr(rule, "weight", 1.0)),
                    required=self.is_component_required(rule.component),
                    rule=rule,
                    evaluator="behavioral_rule",
                    expected_roles=_expected_roles_for_component(rule.component),
                )
            )

        for definition in _default_profile_level_requirements(self):
            definitions.append(definition)
        return definitions



def _static_aggregation_mode(component: str, rule: RequiredRule) -> str:
    """Return aggregation mode."""
    strict_ids = {
        "html.has_doctype",
        "html.has_html_tag",
        "html.has_head",
        "html.has_body",
        "html.has_title",
        "html.has_meta_charset",
        "html.has_meta_viewport",
        "html.has_lang_attribute",
        "html.has_alt_attributes",
        "html.has_labels",
        "html.links_stylesheet",
        "css.parses_cleanly",
        "sql.parses_cleanly",
    }
    if rule.id in strict_ids:
        return AggregationMode.ALL_RELEVANT.value
    if component in {"php", "sql"} and rule.id.endswith(("uses_database", "uses_request")):
        return AggregationMode.EXPECTED_SET.value
    return AggregationMode.ANY.value


def _behavioural_aggregation_mode(rule: BehavioralRule) -> str:
    """Return aggregation mode."""
    if rule.test_type in {"form_submit", "db_persist", "api_exec",
                          "calculator_sequence", "calculator_display", "calculator_operator"}:
        return AggregationMode.EXPECTED_SET.value
    return AggregationMode.ANY.value


def _expected_roles_for_component(component: str) -> tuple[str, ...]:
    """Return roles for component."""
    mapping = {
        "html": ("primary_page", "secondary_page"),
        "css": ("stylesheet_set",),
        "js": ("script_set",),
        "php": ("backend_entrypoint",),
        "sql": ("database_schema_file",),
        "api": ("api_client_code",),
    }
    return mapping.get(component, ())


def _default_profile_level_requirements(profile: ProfileSpec) -> List[RequirementDefinition]:
    """Return profile level requirements."""
    definitions: List[RequirementDefinition] = []
    if profile.is_component_required("api"):
        definitions.append(
            RequirementDefinition(
                id="api.usage_present",
                component="api",
                description="API-backed behaviour is evidenced in the mapped submission flow",
                stage="static",
                aggregation_mode=AggregationMode.ANY.value,
                weight=1.0,
                required=True,
                evaluator="api_usage_presence",
                expected_roles=("api_client_code",),
            )
        )
    if "page_load" in profile.enabled_browser_checks or "page_load" in profile.enabled_behavioural_checks:
        definitions.append(
            RequirementDefinition(
                id=f"{profile.name}.browser.page_load",
                component="html",
                description="Primary page loads successfully in the browser",
                stage="browser",
                aggregation_mode=AggregationMode.EXPECTED_SET.value,
                weight=0.0,
                required=profile.is_component_required("html"),
                evaluator="browser_page_load",
                expected_roles=("primary_page",),
            )
        )
    if "interaction" in profile.enabled_browser_checks:
        definitions.append(
            RequirementDefinition(
                id=f"{profile.name}.browser.interaction",
                component="js",
                description="Expected browser interaction completes without client-side failure",
                stage="browser",
                aggregation_mode=AggregationMode.EXPECTED_SET.value,
                weight=0.0,
                required=profile.is_component_required("js"),
                evaluator="browser_interaction",
                expected_roles=("primary_page", "script_set"),
            )
        )
    if profile.enabled_layout_checks:
        definitions.append(
            RequirementDefinition(
                id=f"{profile.name}.layout.responsive",
                component="css",
                description="Responsive/layout evidence is present for the expected UI flow",
                stage="layout",
                aggregation_mode=AggregationMode.EXPECTED_SET.value,
                weight=0.0,
                required=profile.is_component_required("css"),
                evaluator="layout_responsive",
                expected_roles=("primary_page", "stylesheet_set"),
            )
        )
    for component in profile.relevant_artefacts:
        definitions.append(
            RequirementDefinition(
                id=f"{component}.quality.capped_penalty",
                component=component,
                description=f"Capped quality and consistency penalty for {component.upper()}",
                stage="quality",
                aggregation_mode=AggregationMode.CAPPED_PENALTY.value,
                weight=0.0,
                required=profile.is_component_required(component),
                evaluator="quality_penalty",
                expected_roles=_expected_roles_for_component(component),
            )
        )

    # Cross-file alignment rules — added when both PHP and HTML are required
    if profile.is_component_required("php") and profile.is_component_required("html"):
        definitions.append(
            RequirementDefinition(
                id="php.form_handler_alignment",
                component="php",
                description="PHP request keys match the HTML form field names",
                stage="consistency",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=True,
                evaluator="cross_file_php_form",
                expected_roles=("backend_entrypoint",),
            )
        )

    if profile.is_component_required("sql") and profile.is_component_required("php"):
        definitions.append(
            RequirementDefinition(
                id="sql.matches_application_usage",
                component="sql",
                description="SQL schema tables and columns align with PHP/application references",
                stage="consistency",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=True,
                evaluator="cross_file_sql_alignment",
                expected_roles=("database_schema_file",),
            )
        )

    if profile.is_component_required("api"):
        definitions.append(
            RequirementDefinition(
                id="api.client_server_alignment",
                component="api",
                description="JS/client fetch URLs match server-side API route handlers",
                stage="consistency",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=True,
                evaluator="cross_file_api_alignment",
                expected_roles=("api_client_code",),
            )
        )

    # Extended browser checks — added only for profiles with extended_browser flag
    if "extended_browser" in profile.enabled_browser_checks:
        definitions.append(
            RequirementDefinition(
                id="browser.console_clean",
                component="js",
                description="No fatal console errors (uncaught exceptions or critical failures) during page load",
                stage="browser",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=profile.is_component_required("js"),
                evaluator="browser_console_clean",
                expected_roles=("primary_page",),
            )
        )
        definitions.append(
            RequirementDefinition(
                id="browser.network_assets_resolve",
                component="html",
                description="Core linked assets (CSS, JS, images) resolve without 404 errors",
                stage="browser",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=profile.is_component_required("html"),
                evaluator="browser_network_assets",
                expected_roles=("primary_page",),
            )
        )
        definitions.append(
            RequirementDefinition(
                id="browser.dom_expected_structure",
                component="html",
                description="Runtime DOM contains expected structural elements after scripts execute",
                stage="browser",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=profile.is_component_required("html"),
                evaluator="browser_dom_structure",
                expected_roles=("primary_page",),
            )
        )
        definitions.append(
            RequirementDefinition(
                id="browser.accessible_interaction_targets",
                component="html",
                description="Required interactive elements (buttons, links, inputs) are in viewport and interactable",
                stage="browser",
                aggregation_mode=AggregationMode.ANY.value,
                weight=0.0,
                required=profile.is_component_required("html"),
                evaluator="browser_accessibility",
                expected_roles=("primary_page",),
            )
        )

    return definitions

