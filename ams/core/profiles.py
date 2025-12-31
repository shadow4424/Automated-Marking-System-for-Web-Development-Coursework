from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class RequiredHTMLRule:
    id: str
    description: str
    selector: str
    min_count: int = 1


@dataclass(frozen=True)
class RequiredCSSRule:
    id: str
    description: str
    needle: str
    min_count: int = 1


@dataclass(frozen=True)
class RequiredJSRule:
    id: str
    description: str
    needle: str
    min_count: int = 1


@dataclass(frozen=True)
class RequiredPHPRule:
    id: str
    description: str
    needle: str
    min_count: int = 1


@dataclass(frozen=True)
class RequiredSQLRule:
    id: str
    description: str
    needle: str
    min_count: int = 1


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    required_html: List[RequiredHTMLRule]
    required_css: List[RequiredCSSRule]
    required_js: List[RequiredJSRule]
    required_php: List[RequiredPHPRule]
    required_sql: List[RequiredSQLRule]
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

    def get_required_file_extensions(self) -> List[str]:
        """Get list of required file extensions for this profile."""
        return self.required_files.copy()


def _build_profile_specs() -> Dict[str, ProfileSpec]:
    html_rules = [
        RequiredHTMLRule(id="html.has_form", description="Page includes a <form>", selector="form", min_count=1),
        RequiredHTMLRule(id="html.has_input", description="Page includes at least one <input>", selector="input", min_count=1),
        RequiredHTMLRule(id="html.has_link", description="Page includes at least one link <a>", selector="a", min_count=1),
    ]
    css_rules = [
        RequiredCSSRule(id="css.has_rule_block", description="CSS includes at least one rule block", needle="{", min_count=1),
    ]
    js_rules = [
        RequiredJSRule(id="js.has_event_listener", description="JS registers an event listener", needle="addeventlistener", min_count=1),
    ]
    php_rules_fullstack = [
        RequiredPHPRule(id="php.has_open_tag", description="PHP file has opening tag", needle="<?php", min_count=1),
        RequiredPHPRule(id="php.uses_request", description="PHP uses request superglobal", needle="$_", min_count=1),
        RequiredPHPRule(id="php.outputs", description="PHP outputs content", needle="echo", min_count=1),
    ]
    sql_rules_fullstack = [
        RequiredSQLRule(id="sql.has_create_table", description="SQL defines a table", needle="create table", min_count=1),
        RequiredSQLRule(id="sql.has_insert", description="SQL inserts data", needle="insert into", min_count=1),
        RequiredSQLRule(id="sql.has_select", description="SQL selects data", needle="select ", min_count=1),
    ]

    frontend = ProfileSpec(
        name="frontend",
        required_html=html_rules,
        required_css=css_rules,
        required_js=js_rules,
        required_php=[],
        required_sql=[],
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
    "RequiredHTMLRule",
    "RequiredCSSRule",
    "RequiredJSRule",
    "RequiredPHPRule",
    "RequiredSQLRule",
    "ProfileSpec",
    "get_profile_spec",
    "get_relevant_components",
    "PROFILES",
]
