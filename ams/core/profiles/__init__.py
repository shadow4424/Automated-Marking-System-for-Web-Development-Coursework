"""Profile definitions, builtin profiles, and profile resolution."""
from __future__ import annotations

from ams.core.profiles.base import (
    AggregationMode,
    BehavioralRule,
    RequiredRule,
    RequiredHTMLRule,
    RequiredCSSRule,
    RequiredJSRule,
    RequiredPHPRule,
    RequiredSQLRule,
    RequiredAPIRule,
    RequirementDefinition,
    ProfileSpec,
)
from ams.core.profiles.builtin import (
    PROFILE_ALIASES,
    PROFILE_SPECS,
    VISIBLE_PROFILE_SPECS,
)
from ams.core.profiles.loader import (
    get_profile_spec,
    get_relevant_components,
    get_visible_profile_specs,
    list_profile_names,
    resolve_profile_spec,
    PROFILES,
)

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
