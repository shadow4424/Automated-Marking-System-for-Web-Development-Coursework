from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence


@dataclass(frozen=True)
class RequiredRule:
    """Unified rule definition for all assessable components.

    The ``pattern`` field holds the match string used by assessors — this
    replaces the former ``selector`` (HTML) and ``needle`` (CSS/JS/PHP/SQL)
    fields.  Backward-compatible factory functions are provided below so
    that existing code using ``RequiredHTMLRule(selector=…)`` or
    ``RequiredCSSRule(needle=…)`` continues to work without changes.
    """
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

    # ------------------------------------------------------------------
    # Backward-compatible property aliases so that existing assessor code
    # using ``rule.selector`` or ``rule.needle`` keeps working.
    # ------------------------------------------------------------------
    @property
    def selector(self) -> str:  # used by HTML assessors
        return self.pattern

    @property
    def needle(self) -> str:  # used by CSS / JS / PHP / SQL assessors
        return self.pattern


def _make_rule_factory(legacy_kwarg: str):
    """Return a factory that accepts *legacy_kwarg* and maps it to ``pattern``."""
    def _factory(*args, **kwargs):
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
    """Dynamic behavioral rule for runtime testing."""
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
        """Check if a component is required for this profile."""
        return component in self.relevant_artefacts

    def has_required_rules(self, component: str) -> bool:
        """Check if a component has required rules defined for this profile."""
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
        return {
            "html": list(self.required_html),
            "css": list(self.required_css),
            "js": list(self.required_js),
            "php": list(self.required_php),
            "sql": list(self.required_sql),
            "api": list(self.required_api),
        }

    def enabled_components(self) -> List[str]:
        ordered = list(self.relevant_artefacts)
        for component in self.optional_components:
            if component not in ordered:
                ordered.append(component)
        return ordered

    def get_component_weight(self, component: str) -> float:
        if self.component_weights:
            return float(self.component_weights.get(component, 0.0))
        required = list(self.relevant_artefacts)
        return 1.0 / len(required) if required and component in required else 0.0

    def to_dict(self) -> Dict[str, object]:
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


def _build_profile_specs() -> Dict[str, ProfileSpec]:
    # HTML Rules - Expanded with comprehensive web development criteria
    # Weights are normalised so that the total = 1.0 for the HTML component.
    # Categories: Structure, Metadata, Semantic, Interactive/Forms, Accessibility
    html_rules = [
        # === STRUCTURE (0.26 total) ===
        RequiredHTMLRule(
            id="html.has_doctype",
            description="Document begins with a valid DOCTYPE declaration",
            selector="!doctype",
            min_count=1,
            weight=0.08,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="DOCTYPE is binary - either present or not. No partial credit.",
        ),
        RequiredHTMLRule(
            id="html.has_html_tag",
            description="Document has a root <html> element",
            selector="html",
            min_count=1,
            weight=0.06,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Root element is binary - either present or not.",
        ),
        RequiredHTMLRule(
            id="html.has_head",
            description="Document includes a <head> section",
            selector="head",
            min_count=1,
            weight=0.06,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Head section is binary - either present or not.",
        ),
        RequiredHTMLRule(
            id="html.has_body",
            description="Document includes a <body> section",
            selector="body",
            min_count=1,
            weight=0.06,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Body section is binary - either present or not.",
        ),
        # === METADATA (0.16 total) ===
        RequiredHTMLRule(
            id="html.has_title",
            description="Document has a <title> element in the head",
            selector="title",
            min_count=1,
            weight=0.06,
            category="Metadata",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if title exists but is empty or generic (e.g., 'Untitled').",
        ),
        RequiredHTMLRule(
            id="html.has_meta_charset",
            description="Document specifies character encoding via <meta charset>",
            selector="meta_charset",
            min_count=1,
            weight=0.05,
            category="Metadata",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Charset declaration is binary - either present or not.",
        ),
        RequiredHTMLRule(
            id="html.has_meta_viewport",
            description="Document includes a viewport meta tag for responsive design",
            selector="meta_viewport",
            min_count=1,
            weight=0.05,
            category="Metadata",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if viewport exists but has suboptimal settings.",
        ),
        # === SEMANTIC HTML (0.18 total) ===
        RequiredHTMLRule(
            id="html.has_semantic_structure",
            description="Uses semantic container elements (header, nav, main, section, article, aside, footer)",
            selector="semantic",
            min_count=1,
            weight=0.08,
            category="Semantics",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="medium",
            llm_guidance="Award partial based on variety of semantic tags used. Full credit for 3+ different semantic elements.",
        ),
        RequiredHTMLRule(
            id="html.has_heading_hierarchy",
            description="Uses heading elements (h1-h6) for content hierarchy",
            selector="heading",
            min_count=1,
            weight=0.06,
            category="Semantics",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if headings exist but hierarchy is broken (e.g., h1 followed by h3).",
        ),
        RequiredHTMLRule(
            id="html.has_lists",
            description="Uses list elements (ul, ol, or dl) for structured content",
            selector="list",
            min_count=1,
            weight=0.04,
            category="Semantics",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="List usage is binary - either present or not.",
        ),
        # === INTERACTIVE / FORMS (0.26 total) ===
        RequiredHTMLRule(
            id="html.has_form",
            description="Page includes at least one <form> element",
            selector="form",
            min_count=1,
            weight=0.08,
            category="Forms",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Form presence is binary - either present or not.",
        ),
        RequiredHTMLRule(
            id="html.has_input",
            description="Page includes at least one <input> element",
            selector="input",
            min_count=1,
            weight=0.06,
            category="Forms",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial based on input variety (text, email, password, etc.).",
        ),
        RequiredHTMLRule(
            id="html.has_labels",
            description="Form controls have associated <label> elements",
            selector="label",
            min_count=1,
            weight=0.08,
            category="Accessibility",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="high",
            llm_guidance="Award partial based on percentage of inputs with associated labels.",
        ),
        RequiredHTMLRule(
            id="html.has_link",
            description="Page includes at least one anchor <a> element",
            selector="a",
            min_count=1,
            weight=0.04,
            category="Forms",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Link presence is binary - either present or not.",
        ),
        # === ACCESSIBILITY (0.14 total) ===
        RequiredHTMLRule(
            id="html.has_alt_attributes",
            description="All <img> elements include meaningful alt attributes",
            selector="img_alt",
            min_count=1,
            weight=0.08,
            category="Accessibility",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="high",
            llm_guidance="Award partial based on percentage of images with meaningful alt text. Empty alt='' for decorative images is acceptable.",
        ),
        RequiredHTMLRule(
            id="html.has_lang_attribute",
            description="The <html> element specifies a lang attribute",
            selector="html_lang",
            min_count=1,
            weight=0.06,
            category="Accessibility",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Lang attribute is binary - either present or not.",
        ),
        # === ASSETS / LINKAGE (0.04 total, weights trimmed from structure rules) ===
        RequiredHTMLRule(
            id="html.links_stylesheet",
            description="Page links to an external stylesheet via <link rel='stylesheet'>",
            selector="link_stylesheet",
            min_count=1,
            weight=0.04,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Stylesheet linkage is binary - either present or not.",
        ),
        RequiredHTMLRule(
            id="html.links_script_or_js",
            description="Page includes JavaScript execution path via <script> tag",
            selector="link_script",
            min_count=0,
            weight=0.02,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Script linkage is optional. Award if present.",
        ),
        # === CONTENT ELEMENTS (optional - bonus if used) ===
        RequiredHTMLRule(
            id="html.has_table",
            description="Page uses table markup when tabular content is expected",
            selector="table",
            min_count=0,
            weight=0.02,
            category="Content",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Table usage is optional. Award if present.",
        ),
        RequiredHTMLRule(
            id="html.has_image",
            description="Page uses image elements when visual content is expected",
            selector="img",
            min_count=0,
            weight=0.02,
            category="Content",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Image usage is optional. Award if present.",
        ),
    ]

    
    # CSS Rules - Expanded with comprehensive web development criteria
    # Weights are normalised so that the total = 1.0 for the CSS component.
    # Categories: Structure, Selectors, Layout, Styling, Responsiveness, Maintainability
    css_rules = [
        # === STRUCTURE (0.14 total) ===
        RequiredCSSRule(
            id="css.has_rule_block",
            description="Stylesheet contains at least one CSS rule block",
            needle="{",
            min_count=1,
            weight=0.08,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Rule block presence is binary - either present or not.",
        ),
        RequiredCSSRule(
            id="css.has_multiple_rules",
            description="Stylesheet contains multiple style rules (3+)",
            needle="multiple_rules",
            min_count=3,
            weight=0.06,
            category="Structure",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if 1-2 rules exist. Full credit for 3+.",
        ),
        # === SELECTORS (0.20 total) ===
        RequiredCSSRule(
            id="css.has_class_selector",
            description="Uses class selectors for reusable styling",
            needle=".",
            min_count=1,
            weight=0.10,
            category="Selectors",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Class selector usage is binary.",
        ),
        RequiredCSSRule(
            id="css.has_id_selector",
            description="Uses ID selectors for unique elements",
            needle="#",
            min_count=0,
            weight=0.04,
            category="Selectors",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="ID selector is optional. Award if present.",
        ),
        RequiredCSSRule(
            id="css.has_element_selector",
            description="Uses element/type selectors (body, h1, p, etc.)",
            needle="element_selector",
            min_count=1,
            weight=0.06,
            category="Selectors",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Element selector usage is binary.",
        ),
        # === LAYOUT (0.24 total) ===
        RequiredCSSRule(
            id="css.has_layout",
            description="Uses layout properties (margin, padding, display, position)",
            needle="layout",
            min_count=1,
            weight=0.10,
            category="Layout",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial based on variety of layout properties used.",
        ),
        RequiredCSSRule(
            id="css.has_flexbox",
            description="Uses Flexbox for flexible layout",
            needle="flexbox",
            min_count=1,
            weight=0.08,
            category="Layout",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if flex is declared but not fully utilized.",
        ),
        RequiredCSSRule(
            id="css.has_grid",
            description="Uses CSS Grid for grid-based layout",
            needle="grid",
            min_count=0,
            weight=0.06,
            category="Layout",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Grid is optional. Award if present.",
        ),
        # === STYLING (0.16 total) ===
        RequiredCSSRule(
            id="css.has_color",
            description="Defines color and background properties",
            needle="color:",
            min_count=0,
            weight=0.08,
            category="Styling",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Color usage is binary.",
        ),
        RequiredCSSRule(
            id="css.has_typography",
            description="Uses typography properties (font-family, font-size)",
            needle="typography",
            min_count=1,
            weight=0.08,
            category="Styling",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial based on typography property variety.",
        ),
        # === RESPONSIVENESS (0.16 total) ===
        RequiredCSSRule(
            id="css.has_media_query",
            description="Uses media queries for responsive design",
            needle="@media",
            min_count=1,  # Changed from 0 to require at least one media query
            weight=0.16,
            category="Responsiveness",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="high",
            llm_guidance="Award partial based on media query coverage. Full credit for multiple breakpoints.",
            visual_check=True,  # Phase 3: Enable vision-based responsiveness check
        ),
        # === MAINTAINABILITY (0.10 total) ===
        RequiredCSSRule(
            id="css.has_custom_properties",
            description="Uses CSS custom properties (variables) for maintainability",
            needle="custom_properties",
            min_count=0,
            weight=0.06,
            category="Maintainability",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Custom properties are optional. Award if present.",
        ),
        RequiredCSSRule(
            id="css.has_comments",
            description="Includes comments for code documentation",
            needle="comments",
            min_count=0,
            weight=0.04,
            category="Maintainability",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Comments are optional. Award if present.",
        ),
        # === QUALITY / RESET (new generic rules) ===
        RequiredCSSRule(
            id="css.has_universal_reset",
            description="Applies a universal CSS reset or box-sizing strategy",
            needle="universal_reset",
            min_count=0,
            weight=0.04,
            category="Reset",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Universal reset is optional. Award if * selector or explicit reset strategy is present.",
        ),
        RequiredCSSRule(
            id="css.parses_cleanly",
            description="CSS is syntactically valid with balanced braces",
            needle="parses_cleanly",
            min_count=1,
            weight=0.06,
            category="Quality",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award 0.5 for minor brace imbalance (1-2 unmatched). 0 for severely broken CSS.",
        ),
    ]

    # CSS Lab Rules — extends css_rules with visual/design-intent checks for CSS-specific labs
    # These rules are only required by the frontend_css_lab profile.
    css_lab_rules = list(css_rules) + [
        RequiredCSSRule(
            id="css.body_card_layout",
            description="Body or main container uses card-like layout (max-width, centred, padding, shadow, radius)",
            needle="body_card_layout",
            min_count=1,
            weight=0.10,
            category="Layout",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for max-width + margin:auto + padding + box-shadow + border-radius. Partial for 2-3 of these traits.",
        ),
        RequiredCSSRule(
            id="css.h1_styled",
            description="Main heading (h1) has non-default colour and size styling",
            needle="h1_styled",
            min_count=1,
            weight=0.08,
            category="Typography",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for colour + size + alignment. Partial for one or two of these.",
        ),
        RequiredCSSRule(
            id="css.table_profile_layout",
            description="Table element has width, spacing, or centering applied",
            needle="table_profile_layout",
            min_count=0,
            weight=0.06,
            category="Layout",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Award for table with max-width/width + margin or border-spacing. Optional if no table in task.",
        ),
        RequiredCSSRule(
            id="css.image_rounding_shadow",
            description="Images use border-radius (circular/rounded) and box-shadow",
            needle="image_rounding_shadow",
            min_count=0,
            weight=0.06,
            category="Styling",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Full credit for both border-radius + box-shadow. Partial for one.",
        ),
        RequiredCSSRule(
            id="css.h2_section_style",
            description="Section headings (h2) have colour and size styling",
            needle="h2_section_style",
            min_count=1,
            weight=0.06,
            category="Typography",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for colour + size + spacing. Partial for one or two.",
        ),
        RequiredCSSRule(
            id="css.list_readability_style",
            description="List elements (ul/li) have custom bullets or spacing for readability",
            needle="list_readability_style",
            min_count=0,
            weight=0.06,
            category="Styling",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Award for list-style + spacing on ul or li. Optional if no lists in task.",
        ),
        RequiredCSSRule(
            id="css.link_hover_style",
            description="Links have a distinct hover state (underline or colour change)",
            needle="link_hover_style",
            min_count=1,
            weight=0.08,
            category="Interaction",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for clear hover + normal state differentiation. Partial for hover only or very subtle change.",
        ),
    ]

    
    # JavaScript Rules - Expanded with comprehensive web development criteria
    # Weights are normalised so that the total = 1.0 for the JS component.
    # Categories: Events, DOM, Functions, Control Flow, Validation, Async, Error Handling, Modern JS
    js_rules = [
        # === EVENTS (0.12 total) ===
        RequiredJSRule(
            id="js.has_event_listener",
            description="Registers event listeners for user interaction",
            needle="addeventlistener",
            min_count=1,
            weight=0.12,
            category="Events",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Award partial if inline event handlers (onclick) are used instead of addEventListener.",
        ),
        # === DOM (0.20 total) ===
        RequiredJSRule(
            id="js.has_dom_query",
            description="Queries DOM elements (querySelector, getElementById, etc.)",
            needle="dom_query",
            min_count=1,
            weight=0.10,
            category="DOM",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="DOM query usage is binary.",
        ),
        RequiredJSRule(
            id="js.has_dom_manipulation",
            description="Manipulates DOM content (innerHTML, textContent, appendChild, etc.)",
            needle="dom_manipulation",
            min_count=1,
            weight=0.10,
            category="DOM",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Award partial based on variety and appropriateness of DOM manipulation methods.",
        ),
        # === FUNCTIONS (0.16 total) ===
        RequiredJSRule(
            id="js.has_functions",
            description="Defines reusable functions",
            needle="function ",
            min_count=1,
            weight=0.10,
            category="Functions",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Function definition is binary.",
        ),
        RequiredJSRule(
            id="js.has_arrow_functions",
            description="Uses arrow function syntax",
            needle="=>",
            min_count=0,
            weight=0.06,
            category="Modern Practices",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Arrow functions are optional. Award if present.",
        ),
        # === CONTROL FLOW (0.14 total) ===
        RequiredJSRule(
            id="js.has_conditionals",
            description="Uses conditional statements (if/else, switch)",
            needle="if ",
            min_count=1,
            weight=0.08,
            category="Control Flow",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Conditional usage is binary.",
        ),
        RequiredJSRule(
            id="js.has_loops",
            description="Uses loops (for, while, forEach)",
            needle="loops",
            min_count=0,
            weight=0.06,
            category="Control Flow",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Loop usage is optional. Award if present.",
        ),
        # === VALIDATION (0.08 total) ===
        RequiredJSRule(
            id="js.has_form_validation",
            description="Validates form input or user data",
            needle="form_validation",
            min_count=0,
            weight=0.08,
            category="Validation",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="medium",
            llm_guidance="Award partial based on validation coverage and robustness.",
        ),
        # === ASYNC (0.08 total) ===
        RequiredJSRule(
            id="js.has_async_patterns",
            description="Uses async patterns (async/await, fetch, Promise)",
            needle="async_patterns",
            min_count=0,
            weight=0.08,
            category="Async",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if callbacks are used instead of modern async/await.",
        ),
        # === ERROR HANDLING (0.08 total) ===
        RequiredJSRule(
            id="js.has_error_handling",
            description="Includes error handling (try-catch)",
            needle="try",
            min_count=0,
            weight=0.08,
            category="Error Handling",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Error handling usage is optional. Award if present.",
        ),
        # === MODERN JS (0.14 total) ===
        RequiredJSRule(
            id="js.has_const_let",
            description="Uses modern variable declarations (const/let)",
            needle="const_let",
            min_count=1,
            weight=0.08,
            category="Modern Practices",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial if let is used but const is not for constants.",
        ),
        RequiredJSRule(
            id="js.has_template_literals",
            description="Uses template literals for string formatting",
            needle="`",
            min_count=0,
            weight=0.06,
            category="Modern Practices",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Template literals are optional. Award if present.",
        ),
    ]

    # JavaScript Calculator Rules — extends js_rules with calculator-specific DOM and logic checks.
    # Only required by the frontend_calculator profile.
    js_calculator_rules = list(js_rules) + [
        RequiredJSRule(
            id="js.creates_display_dom",
            description="JavaScript creates or references the calculator display (input#theDisplay)",
            needle="creates_display_dom",
            min_count=1,
            weight=0.08,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for getElementById('theDisplay') or createElement of display input. Partial for display element referenced but not explicitly created.",
        ),
        RequiredJSRule(
            id="js.creates_digit_buttons",
            description="JavaScript creates digit buttons (0-9, decimal, equals) dynamically",
            needle="creates_digit_buttons",
            min_count=1,
            weight=0.06,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for all 12 digit/decimal/equals buttons. Partial for 4-7 digit buttons.",
        ),
        RequiredJSRule(
            id="js.creates_operator_buttons",
            description="JavaScript creates operator buttons (+, -, *, /)",
            needle="creates_operator_buttons",
            min_count=1,
            weight=0.06,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for all 4 operators. Partial for 2-3.",
        ),
        RequiredJSRule(
            id="js.has_updateDisplay",
            description="Function or logic appends clicked value to the calculator display",
            needle="has_updatedisplay",
            min_count=1,
            weight=0.08,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for updateDisplay function or equivalent display.value += pattern. Partial for partially correct logic.",
        ),
        RequiredJSRule(
            id="js.has_prevalue_preop_state",
            description="Tracks previous value (preValue) and previous operator (preOp) for chained calculations",
            needle="has_prevalue_preop",
            min_count=1,
            weight=0.08,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for explicit preValue + preOp state. Partial for equivalent state tracking without those names.",
        ),
        RequiredJSRule(
            id="js.has_doCalc",
            description="Performs the stored arithmetic operation (doCalc function or equivalent)",
            needle="has_docalc",
            min_count=1,
            weight=0.08,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Full credit for doCalc function handling all 4 operations. Partial for 2-3 operations handled.",
        ),
        RequiredJSRule(
            id="js.clears_or_updates_display_correctly",
            description="Operator press clears the display; equals shows the result",
            needle="clears_display",
            min_count=1,
            weight=0.06,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for both clear-on-operator and result-on-equals. Partial for one only.",
        ),
        RequiredJSRule(
            id="js.uses_createElement",
            description="Uses document.createElement() for dynamic DOM construction",
            needle="uses_createelement",
            min_count=1,
            weight=0.06,
            category="Modern Practices",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="createElement usage is binary in the calculator context.",
        ),
        RequiredJSRule(
            id="js.avoids_document_write",
            description="Does not rely on document.write() for UI construction",
            needle="avoids_document_write",
            min_count=1,
            weight=0.06,
            category="Modern Practices",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for no document.write. Partial for minimal leftover non-core use.",
        ),
        RequiredJSRule(
            id="js.extra_features",
            description="Implements optional calculator extras (clear, sqrt, percentage, memory, etc.)",
            needle="extra_features",
            min_count=0,
            weight=0.04,
            category="Calculator",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Award for one or more working extras. Partial for attempted but broken extras.",
        ),
    ]


    # PHP Rules - Expanded with comprehensive web development criteria
    # Weights are normalised so that the total = 1.0 for the PHP component.
    # Categories: Structure, Input, Output, Database, Sessions, Functions, Control Flow, Error Handling, Includes
    php_rules_fullstack = [
        # === STRUCTURE (0.06 total) ===
        RequiredPHPRule(
            id="php.has_open_tag",
            description="PHP file contains opening tag",
            needle="<?php",
            min_count=1,
            weight=0.06,
            category="Structure",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="PHP opening tag is binary.",
        ),
        # === INPUT HANDLING (0.20 total) ===
        RequiredPHPRule(
            id="php.uses_request",
            description="Uses request superglobals ($_GET, $_POST, $_REQUEST)",
            needle="request_superglobal",
            min_count=1,
            weight=0.10,
            category="Input",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Request superglobal usage is binary.",
        ),
        RequiredPHPRule(
            id="php.has_validation",
            description="Validates input (isset, empty, filter_var)",
            needle="validation",
            min_count=1,
            weight=0.10,
            category="Validation",
            partial_allowed=True,
            partial_range=(0.0, 0.75),
            severity="high",
            llm_guidance="Award partial based on depth and coverage of validation logic.",
        ),
        # === SECURITY (0.10 total) ===
        RequiredPHPRule(
            id="php.has_sanitisation",
            description="Sanitises output (htmlspecialchars, htmlentities, strip_tags)",
            needle="sanitisation",
            min_count=0,
            weight=0.10,
            category="Security",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Award partial if some but not all output is sanitised.",
        ),
        # === OUTPUT (0.08 total) ===
        RequiredPHPRule(
            id="php.outputs",
            description="Outputs content (echo, print)",
            needle="output",
            min_count=1,
            weight=0.08,
            category="Output",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Output usage is binary.",
        ),
        # === DATABASE (0.26 total) ===
        RequiredPHPRule(
            id="php.uses_database",
            description="Interacts with database (mysqli, PDO)",
            needle="database",
            min_count=0,
            weight=0.16,
            category="Database",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Database interaction is optional. Award if present.",
        ),
        RequiredPHPRule(
            id="php.uses_prepared_statements",
            description="Uses prepared statements for SQL safety",
            needle="prepared_statements",
            min_count=0,
            weight=0.10,
            category="Security",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="high",
            llm_guidance="Award partial if some queries use prepared statements but not all.",
        ),
        # === SESSIONS (0.08 total) ===
        RequiredPHPRule(
            id="php.uses_sessions",
            description="Uses session handling (session_start, $_SESSION)",
            needle="sessions",
            min_count=0,
            weight=0.08,
            category="Sessions",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Session usage is optional. Award if present.",
        ),
        # === FUNCTIONS (0.06 total) ===
        RequiredPHPRule(
            id="php.has_functions",
            description="Defines reusable functions",
            needle="function ",
            min_count=0,
            weight=0.06,
            category="Functions",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Function definition is optional. Award if present.",
        ),
        # === CONTROL FLOW (0.10 total) ===
        RequiredPHPRule(
            id="php.has_conditionals",
            description="Uses conditional statements (if/else, switch)",
            needle="if ",
            min_count=1,
            weight=0.06,
            category="Control Flow",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Conditional usage is binary.",
        ),
        RequiredPHPRule(
            id="php.has_loops",
            description="Uses loops (for, while, foreach)",
            needle="loops",
            min_count=0,
            weight=0.04,
            category="Control Flow",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Loop usage is optional. Award if present.",
        ),
        # === ERROR HANDLING (0.06 total) ===
        RequiredPHPRule(
            id="php.has_error_handling",
            description="Includes error handling (try-catch, error_reporting)",
            needle="error_handling",
            min_count=0,
            weight=0.06,
            category="Error Handling",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Error handling is optional. Award if present.",
        ),
        # === RESPONSE PATH / ALIGNMENT (new generic checks) ===
        RequiredPHPRule(
            id="php.response_path_complete",
            description="Script has a complete request-receive-process-output path",
            needle="response_path_complete",
            min_count=1,
            weight=0.06,
            category="Architecture",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for input superglobal + conditional logic + output. Partial for partial path only.",
        ),
    ]


    # SQL Rules - Expanded with comprehensive database design criteria
    # Weights are normalised so that the total = 1.0 for the SQL component.
    # Categories: Schema, Constraints, CRUD, Queries, Advanced
    sql_rules_fullstack = [
        # === SCHEMA (0.30 total) ===
        RequiredSQLRule(
            id="sql.has_create_table",
            description="Defines tables (CREATE TABLE)",
            needle="create table",
            min_count=1,
            weight=0.12,
            category="Schema",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Table creation is binary.",
        ),
        RequiredSQLRule(
            id="sql.has_primary_key",
            description="Defines primary keys for unique row identification",
            needle="primary key",
            min_count=1,
            weight=0.10,
            category="Schema",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="Primary key usage is binary.",
        ),
        RequiredSQLRule(
            id="sql.has_foreign_key",
            description="Defines foreign keys for referential integrity",
            needle="foreign_key",
            min_count=0,
            weight=0.08,
            category="Constraints",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="Foreign key is optional. Award if present.",
        ),
        # === CONSTRAINTS (0.14 total) ===
        RequiredSQLRule(
            id="sql.has_constraints",
            description="Uses constraints (NOT NULL, UNIQUE, CHECK, DEFAULT)",
            needle="constraints",
            min_count=0,
            weight=0.08,
            category="Constraints",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial based on variety of constraints used.",
        ),
        RequiredSQLRule(
            id="sql.has_data_types",
            description="Specifies appropriate data types (INT, VARCHAR, TEXT, DATE)",
            needle="data_types",
            min_count=1,
            weight=0.06,
            category="Schema",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Award partial based on appropriateness of data types for column data.",
        ),
        # === CRUD (0.32 total) ===
        RequiredSQLRule(
            id="sql.has_insert",
            description="Inserts data (INSERT INTO)",
            needle="insert into",
            min_count=1,
            weight=0.10,
            category="CRUD",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="INSERT usage is binary.",
        ),
        RequiredSQLRule(
            id="sql.has_select",
            description="Selects data (SELECT)",
            needle="select ",
            min_count=1,
            weight=0.12,
            category="CRUD",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="high",
            llm_guidance="SELECT usage is binary.",
        ),
        RequiredSQLRule(
            id="sql.has_update",
            description="Updates data (UPDATE)",
            needle="update ",
            min_count=0,
            weight=0.06,
            category="CRUD",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="UPDATE is optional. Award if present.",
        ),
        RequiredSQLRule(
            id="sql.has_delete",
            description="Deletes data (DELETE)",
            needle="delete ",
            min_count=0,
            weight=0.04,
            category="CRUD",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="DELETE is optional. Award if present.",
        ),
        # === QUERIES (0.16 total) ===
        RequiredSQLRule(
            id="sql.has_where",
            description="Uses WHERE clauses for filtering",
            needle="where ",
            min_count=0,
            weight=0.08,
            category="Queries",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="WHERE usage is optional. Award if present.",
        ),
        RequiredSQLRule(
            id="sql.has_join",
            description="Uses JOIN operations for combining tables",
            needle="join ",
            min_count=0,
            weight=0.08,
            category="Queries",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="medium",
            llm_guidance="JOIN is optional. Award if present.",
        ),
        # === ADVANCED (0.08 total) ===
        RequiredSQLRule(
            id="sql.has_aggregate",
            description="Uses aggregate functions (COUNT, SUM, AVG, GROUP BY)",
            needle="aggregate",
            min_count=0,
            weight=0.08,
            category="Queries",
            partial_allowed=False,
            partial_range=(0.0, 0.0),
            severity="low",
            llm_guidance="Aggregate functions are optional. Award if present.",
        ),
        # === QUALITY ===
        RequiredSQLRule(
            id="sql.parses_cleanly",
            description="SQL is syntactically valid with semicolons and balanced structure",
            needle="parses_cleanly",
            min_count=1,
            weight=0.06,
            category="Quality",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for valid SQL with semicolons. Partial for minor syntax issues.",
        ),
    ]


    # Behavioral Rules - Dynamic runtime testing criteria
    # Weights are normalised so that the total = 1.0 for the behavioral component.
    behavioral_rules_frontend = [
        BehavioralRule(
            id="behavior.page_loads",
            description="HTML page renders without errors in browser",
            test_type="page_load",
            component="html",
            weight=0.30,
        ),
        BehavioralRule(
            id="behavior.js_interactive",
            description="JavaScript responds to user events and modifies DOM",
            test_type="js_interaction",
            component="js",
            weight=0.40,
        ),
        BehavioralRule(
            id="behavior.hover_style_visible",
            description="Link hover state creates a visible style change in browser",
            test_type="hover_check",
            component="css",
            weight=0.15,
        ),
        BehavioralRule(
            id="behavior.responsive_resize",
            description="Layout remains structurally usable after viewport change (mobile/desktop)",
            test_type="viewport_resize",
            component="css",
            weight=0.15,
        ),
    ]

    behavioral_rules_fullstack = [
        BehavioralRule(
            id="behavior.page_loads",
            description="HTML page renders without errors in browser",
            test_type="page_load",
            component="html",
            weight=0.20,
        ),
        BehavioralRule(
            id="behavior.js_interactive",
            description="JavaScript responds to user events and modifies DOM",
            test_type="js_interaction",
            component="js",
            weight=0.20,
        ),
        BehavioralRule(
            id="behavior.form_submits",
            description="Form submission triggers PHP processing",
            test_type="form_submit",
            component="php",
            weight=0.30,
        ),
        BehavioralRule(
            id="behavior.db_persists",
            description="Database operations persist and retrieve data correctly",
            test_type="db_persist",
            component="sql",
            weight=0.30,
        ),
    ]

    behavioral_rules_api = [
        BehavioralRule(
            id="behavior.page_loads",
            description="HTML page renders without errors in browser",
            test_type="page_load",
            component="html",
            weight=0.25,
        ),
        BehavioralRule(
            id="behavior.js_interactive",
            description="JavaScript responds to user events and modifies DOM",
            test_type="js_interaction",
            component="js",
            weight=0.35,
        ),
        BehavioralRule(
            id="behavior.api_exec",
            description="API-backed flow returns a usable response",
            test_type="api_exec",
            component="api",
            weight=0.40,
        ),
    ]

    api_required_rules = [
        RequiredAPIRule(
            id="api.json_encode",
            description="Server-side script encodes responses as JSON",
            needle="json_encode",
            min_count=1,
            weight=1.5,
        ),
        RequiredAPIRule(
            id="api.json_content_type",
            description="Server-side script sets JSON Content-Type header",
            needle="application/json",
            min_count=1,
            weight=1.0,
        ),
        RequiredAPIRule(
            id="api.accepts_method",
            description="API handler checks or routes by HTTP request method",
            needle="accepts_method",
            min_count=1,
            weight=0.8,
            category="HTTP",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for explicit method routing. Partial for any method awareness.",
        ),
        RequiredAPIRule(
            id="api.valid_json_shape",
            description="JSON response has a meaningful structure (array or keyed object)",
            needle="valid_json_shape",
            min_count=1,
            weight=1.0,
            category="Response",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="medium",
            llm_guidance="Full credit for json_encode with array/object. Partial for bare variable encode.",
        ),
        RequiredAPIRule(
            id="api.http_status_codes",
            description="Uses appropriate HTTP status codes for success and error responses",
            needle="http_status_codes",
            min_count=0,
            weight=0.6,
            category="HTTP",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Award for http_response_code() or header() status codes. Optional.",
        ),
        RequiredAPIRule(
            id="api.error_response_path",
            description="Error conditions produce a structured JSON error response",
            needle="error_response_path",
            min_count=0,
            weight=0.6,
            category="Response",
            partial_allowed=True,
            partial_range=(0.0, 0.5),
            severity="low",
            llm_guidance="Award for json_encode inside if/catch with error key. Optional.",
        ),
    ]

    # Calculator-specific behavioral rules
    behavioral_rules_calculator = [
        BehavioralRule(
            id="behavior.calculator_sequence",
            description="Calculator correctly computes standard arithmetic sequences (2+3=5, 9-4=5, 6*7=42, 8/2=4)",
            test_type="calculator_sequence",
            component="js",
            weight=0.40,
        ),
        BehavioralRule(
            id="behavior.display_append",
            description="Clicking digits 1, 2, 3 sequentially shows 123 in the display",
            test_type="calculator_display",
            component="js",
            weight=0.30,
        ),
        BehavioralRule(
            id="behavior.operator_state_flow",
            description="Operator press stores state and clears display; equals shows result",
            test_type="calculator_operator",
            component="js",
            weight=0.30,
        ),
    ]

    frontend_basic = ProfileSpec(
        name="frontend_basic",
        required_html=html_rules,
        required_css=css_rules,
        required_js=[],
        required_php=[],
        required_sql=[],
        behavioral_rules=[
            BehavioralRule(
                id="behavior.page_loads",
                description="Primary HTML page renders without errors in browser",
                test_type="page_load",
                component="html",
                weight=1.0,
            ),
        ],
        required_files=[".html"],
        relevant_artefacts=["html", "css"],
        optional_components=["js"],
        expected_layers=["html", "css", "js"],
        enabled_static_checks=["html", "css", "consistency"],
        enabled_behavioural_checks=["page_load"],
        enabled_browser_checks=["page_load"],
        enabled_layout_checks=["visibility"],
        expected_entrypoint_types=["html"],
        component_weights={"html": 0.55, "css": 0.45},
        missing_component_treatment={"html": "zero", "css": "zero", "js": "warning"},
        role_expectations={
            "primary_page": "single primary HTML page",
            "stylesheet_set": "linked stylesheet set",
        },
        frontend_only=True,
    )

    frontend_interactive = ProfileSpec(
        name="frontend_interactive",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=[],
        required_sql=[],
        behavioral_rules=behavioral_rules_frontend,
        required_files=[".html"],
        relevant_artefacts=["html", "css", "js"],
        expected_layers=["html", "css", "js"],
        enabled_static_checks=["html", "css", "js", "consistency"],
        enabled_behavioural_checks=["page_load", "js_interaction"],
        enabled_browser_checks=["page_load", "interaction", "console"],
        enabled_layout_checks=["responsive", "visibility"],
        expected_entrypoint_types=["html"],
        component_weights={"html": 0.34, "css": 0.33, "js": 0.33},
        missing_component_treatment={"html": "zero", "css": "zero", "js": "zero"},
        role_expectations={
            "primary_page": "single primary HTML page",
            "secondary_page": "reachable linked pages",
            "stylesheet_set": "linked stylesheet set",
            "script_set": "linked script set",
        },
        frontend_only=True,
        aliases=("frontend",),
    )

    fullstack_form_php = ProfileSpec(
        name="fullstack_form_php",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=php_rules_fullstack,
        required_sql=[],
        behavioral_rules=[
            rule
            for rule in behavioral_rules_fullstack
            if rule.id != "behavior.db_persists"
        ],
        required_files=[".html", ".php"],
        relevant_artefacts=["html", "css", "js", "php"],
        optional_components=["sql"],
        expected_layers=["html", "css", "js", "php"],
        enabled_static_checks=["html", "css", "js", "php", "consistency"],
        enabled_behavioural_checks=["page_load", "js_interaction", "form_submit"],
        enabled_browser_checks=["page_load", "interaction", "console"],
        enabled_layout_checks=["responsive", "visibility"],
        expected_entrypoint_types=["html", "php"],
        component_weights={"html": 0.25, "css": 0.2, "js": 0.2, "php": 0.35},
        missing_component_treatment={
            "html": "zero",
            "css": "zero",
            "js": "zero",
            "php": "zero",
            "sql": "warning",
        },
        role_expectations={
            "primary_page": "single primary HTML page",
            "backend_entrypoint": "form-processing PHP entrypoint",
            "script_set": "interactive scripts",
        },
        frontend_only=False,
    )

    fullstack_php_sql = ProfileSpec(
        name="fullstack_php_sql",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=php_rules_fullstack,
        required_sql=sql_rules_fullstack,
        behavioral_rules=behavioral_rules_fullstack,
        required_files=[".html", ".php", ".sql"],
        relevant_artefacts=["html", "css", "js", "php", "sql"],
        expected_layers=["html", "css", "js", "php", "sql"],
        enabled_static_checks=["html", "css", "js", "php", "sql", "consistency"],
        enabled_behavioural_checks=["page_load", "js_interaction", "form_submit", "db_persist"],
        enabled_browser_checks=["page_load", "interaction", "console"],
        enabled_layout_checks=["responsive", "visibility"],
        expected_entrypoint_types=["html", "php", "sql"],
        component_weights={"html": 0.2, "css": 0.18, "js": 0.18, "php": 0.24, "sql": 0.2},
        missing_component_treatment={
            "html": "zero",
            "css": "zero",
            "js": "zero",
            "php": "zero",
            "sql": "zero",
        },
        role_expectations={
            "primary_page": "single primary HTML page",
            "backend_entrypoint": "reachable PHP entrypoint",
            "database_schema_file": "database/schema SQL file",
        },
        frontend_only=False,
        aliases=("fullstack",),
    )

    api_backed_web = ProfileSpec(
        name="api_backed_web",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=[],
        required_sql=[],
        behavioral_rules=behavioral_rules_api,
        required_api=api_required_rules,
        required_files=[".html", ".js"],
        relevant_artefacts=["html", "css", "js", "api"],
        optional_components=["php", "sql"],
        expected_layers=["html", "css", "js", "api"],
        enabled_static_checks=["html", "css", "js", "api", "consistency"],
        enabled_behavioural_checks=["page_load", "js_interaction", "api_exec"],
        enabled_browser_checks=["page_load", "interaction", "console"],
        enabled_layout_checks=["responsive", "visibility"],
        expected_entrypoint_types=["html", "api"],
        component_weights={"html": 0.2, "css": 0.2, "js": 0.25, "api": 0.35},
        missing_component_treatment={
            "html": "zero",
            "css": "zero",
            "js": "zero",
            "api": "zero",
            "php": "warning",
            "sql": "warning",
        },
        role_expectations={
            "primary_page": "single primary HTML page",
            "script_set": "API client script set",
            "api_client_code": "API request/response flow",
        },
        frontend_only=False,
    )

    frontend_css_lab = ProfileSpec(
        name="frontend_css_lab",
        required_html=html_rules,
        required_css=css_lab_rules,
        required_js=js_rules,
        required_php=[],
        required_sql=[],
        behavioral_rules=behavioral_rules_frontend,
        required_files=[".html", ".css"],
        relevant_artefacts=["html", "css", "js"],
        optional_components=[],
        expected_layers=["html", "css", "js"],
        enabled_static_checks=["html", "css", "js", "consistency"],
        enabled_behavioural_checks=["page_load", "js_interaction", "hover_check", "viewport_resize"],
        enabled_browser_checks=["page_load", "interaction", "console", "computed_style", "extended_browser"],
        enabled_layout_checks=["responsive", "visibility", "computed_style"],
        expected_entrypoint_types=["html"],
        component_weights={"html": 0.30, "css": 0.45, "js": 0.25},
        missing_component_treatment={"html": "zero", "css": "zero", "js": "warning"},
        role_expectations={
            "primary_page": "single primary HTML page with profile layout",
            "stylesheet_set": "linked CSS stylesheet with visual design",
        },
        frontend_only=True,
        aliases=("css_lab",),
    )

    frontend_calculator = ProfileSpec(
        name="frontend_calculator",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_calculator_rules,
        required_php=[],
        required_sql=[],
        behavioral_rules=behavioral_rules_frontend + behavioral_rules_calculator,
        required_files=[".html", ".js"],
        relevant_artefacts=["html", "css", "js"],
        optional_components=[],
        expected_layers=["html", "css", "js"],
        enabled_static_checks=["html", "css", "js", "consistency"],
        enabled_behavioural_checks=[
            "page_load", "js_interaction",
            "calculator_sequence", "calculator_display", "calculator_operator",
        ],
        enabled_browser_checks=["page_load", "interaction", "console", "extended_browser"],
        enabled_layout_checks=["responsive", "visibility"],
        expected_entrypoint_types=["html"],
        component_weights={"html": 0.25, "css": 0.20, "js": 0.55},
        missing_component_treatment={"html": "zero", "css": "warning", "js": "zero"},
        role_expectations={
            "primary_page": "calculator HTML page",
            "script_set": "JavaScript calculator logic",
        },
        frontend_only=True,
        aliases=("calculator",),
    )

    specs = {
        spec.name: spec
        for spec in (
            frontend_basic,
            frontend_interactive,
            fullstack_form_php,
            fullstack_php_sql,
            api_backed_web,
            frontend_css_lab,
            frontend_calculator,
        )
    }
    return specs


PROFILE_SPECS = _build_profile_specs()
PROFILE_ALIASES = {
    alias: spec.name
    for spec in PROFILE_SPECS.values()
    for alias in spec.aliases
}
VISIBLE_PROFILE_SPECS = {
    name: spec for name, spec in PROFILE_SPECS.items()
}


def get_profile_spec(name: str) -> ProfileSpec:
    canonical_name = PROFILE_ALIASES.get(name, name)
    try:
        return PROFILE_SPECS[canonical_name]
    except KeyError as exc:
        raise ValueError(f"Unknown profile: {name}") from exc


def get_relevant_components(name: str) -> List[str]:
    return get_profile_spec(name).relevant_artefacts


def get_visible_profile_specs() -> Dict[str, ProfileSpec]:
    return dict(VISIBLE_PROFILE_SPECS)


def list_profile_names(*, include_aliases: bool = False, visible_only: bool = False) -> List[str]:
    if visible_only:
        names = list(VISIBLE_PROFILE_SPECS.keys())
    else:
        names = list(PROFILE_SPECS.keys())
    if include_aliases:
        names.extend(PROFILE_ALIASES.keys())
    return sorted(dict.fromkeys(names))


def resolve_profile_spec(name: str, *, config_path: str | Path | None = None) -> ProfileSpec:
    if name != "custom_profile":
        return get_profile_spec(name)
    if not config_path:
        raise ValueError("custom_profile requires a config file path")
    return _build_custom_profile(Path(config_path))


# Compatibility mapping for legacy imports expecting PROFILES
PROFILES = {
    name: {"relevant_artefacts": spec.relevant_artefacts}
    for name, spec in {**VISIBLE_PROFILE_SPECS, **{alias: get_profile_spec(alias) for alias in PROFILE_ALIASES}}.items()
}


def _build_custom_profile(config_path: Path) -> ProfileSpec:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    base_name = str(payload.get("base_profile") or "frontend_interactive")
    base = get_profile_spec(base_name)

    required_components = _normalize_component_list(
        payload.get("required_components"),
        default=base.relevant_artefacts,
    )
    optional_components = _normalize_component_list(
        payload.get("optional_components"),
        default=base.optional_components,
    )
    expected_layers = _normalize_component_list(
        payload.get("expected_layers"),
        default=base.expected_layers or list(dict.fromkeys(required_components + optional_components)),
    )

    return replace(
        base,
        name="custom_profile",
        relevant_artefacts=required_components,
        optional_components=optional_components,
        expected_layers=expected_layers,
        enabled_static_checks=_normalize_string_list(payload.get("enabled_static_checks"), base.enabled_static_checks),
        enabled_behavioural_checks=_normalize_string_list(payload.get("enabled_behavioural_checks"), base.enabled_behavioural_checks),
        enabled_browser_checks=_normalize_string_list(payload.get("enabled_browser_checks"), base.enabled_browser_checks),
        enabled_layout_checks=_normalize_string_list(payload.get("enabled_layout_checks"), base.enabled_layout_checks),
        expected_entrypoint_types=_normalize_string_list(payload.get("expected_entrypoint_types"), base.expected_entrypoint_types),
        component_weights=_normalize_weight_map(payload.get("component_weights"), base.component_weights, required_components),
        missing_component_treatment=_normalize_mapping(payload.get("missing_component_treatment"), base.missing_component_treatment),
        role_expectations=_normalize_mapping(payload.get("role_expectations"), base.role_expectations),
        frontend_only=bool(payload.get("frontend_only", base.frontend_only)),
        custom_config_path=str(config_path),
        aliases=(),
    )


def _normalize_string_list(value: object, default: Sequence[str]) -> List[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings in custom profile config")
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_component_list(value: object, default: Sequence[str]) -> List[str]:
    return _normalize_string_list(value, default)


def _normalize_mapping(value: object, default: Mapping[str, str]) -> Dict[str, str]:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ValueError("Expected an object in custom profile config")
    return {str(key): str(item) for key, item in value.items()}


def _normalize_weight_map(
    value: object,
    default: Mapping[str, float],
    required_components: Sequence[str],
) -> Dict[str, float]:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ValueError("Expected component_weights to be an object")
    weights = {str(key): float(item) for key, item in value.items()}
    total = sum(weight for component, weight in weights.items() if component in required_components)
    if total <= 0:
        raise ValueError("component_weights must contain a positive total for required components")
    return weights


def _static_aggregation_mode(component: str, rule: RequiredRule) -> str:
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
    if rule.test_type in {"form_submit", "db_persist", "api_exec",
                          "calculator_sequence", "calculator_display", "calculator_operator"}:
        return AggregationMode.EXPECTED_SET.value
    return AggregationMode.ANY.value


def _expected_roles_for_component(component: str) -> tuple[str, ...]:
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


__all__ = [
    "AggregationMode",
    "RequiredRule",
    "RequiredHTMLRule",
    "RequiredCSSRule",
    "RequiredJSRule",
    "RequiredPHPRule",
    "RequiredSQLRule",
    "RequiredAPIRule",
    "BehavioralRule",
    "RequirementDefinition",
    "ProfileSpec",
    "get_profile_spec",
    "get_visible_profile_specs",
    "list_profile_names",
    "resolve_profile_spec",
    "get_relevant_components",
    "PROFILES",
]
