from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


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
        }
        rules = rule_map.get(component, [])
        return len(rules) > 0


def _build_profile_specs() -> Dict[str, ProfileSpec]:
    # HTML Rules - Expanded with comprehensive web development criteria
    # Weights are normalized so that the total = 1.0 for the HTML component.
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
    ]

    
    # CSS Rules - Expanded with comprehensive web development criteria
    # Weights are normalized so that the total = 1.0 for the CSS component.
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
    ]

    
    # JavaScript Rules - Expanded with comprehensive web development criteria
    # Weights are normalized so that the total = 1.0 for the JS component.
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

    
    # PHP Rules - Expanded with comprehensive web development criteria
    # Weights are normalized so that the total = 1.0 for the PHP component.
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
    ]

    
    # SQL Rules - Expanded with comprehensive database design criteria
    # Weights are normalized so that the total = 1.0 for the SQL component.
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
    ]


    # Behavioral Rules - Dynamic runtime testing criteria
    # Weights are normalized so that the total = 1.0 for the behavioral component.
    behavioral_rules_frontend = [
        BehavioralRule(
            id="behavior.page_loads",
            description="HTML page renders without errors in browser",
            test_type="page_load",
            component="html",
            weight=0.40,
        ),
        BehavioralRule(
            id="behavior.js_interactive",
            description="JavaScript responds to user events and modifies DOM",
            test_type="js_interaction",
            component="js",
            weight=0.60,
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

    frontend = ProfileSpec(
        name="frontend",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=[],
        required_sql=[],
        behavioral_rules=behavioral_rules_frontend,
        required_files=[".html"],
        relevant_artefacts=["html", "css", "js"],
    )

    fullstack = ProfileSpec(
        name="fullstack",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=php_rules_fullstack,
        required_sql=sql_rules_fullstack,
        behavioral_rules=behavioral_rules_fullstack,
        required_files=[".html"],
        relevant_artefacts=["html", "css", "js", "php", "sql"],
    )

    return {p.name: p for p in (frontend, fullstack)}


PROFILE_SPECS = _build_profile_specs()


def get_profile_spec(name: str) -> ProfileSpec:
    try:
        return PROFILE_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown profile: {name}") from exc


def get_relevant_components(name: str) -> List[str]:
    return get_profile_spec(name).relevant_artefacts


# Compatibility mapping for legacy imports expecting PROFILES
PROFILES = {name: {"relevant_artefacts": spec.relevant_artefacts} for name, spec in PROFILE_SPECS.items()}


__all__ = [
    "RequiredRule",
    "RequiredHTMLRule",
    "RequiredCSSRule",
    "RequiredJSRule",
    "RequiredPHPRule",
    "RequiredSQLRule",
    "BehavioralRule",
    "ProfileSpec",
    "get_profile_spec",
    "get_relevant_components",
    "PROFILES",
]
