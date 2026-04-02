from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from ams.core.profiles.base import AggregationMode, ProfileSpec, RequirementDefinition
from ams.core.profiles.builtin import PROFILE_ALIASES, PROFILE_SPECS, VISIBLE_PROFILE_SPECS

def get_profile_spec(name: str) -> ProfileSpec:
    """Return profile spec."""
    canonical_name = PROFILE_ALIASES.get(name, name)
    try:
        return PROFILE_SPECS[canonical_name]
    except KeyError as exc:
        raise ValueError(f"Unknown profile: {name}") from exc


def get_relevant_components(name: str) -> List[str]:
    """Return relevant components."""
    return get_profile_spec(name).relevant_artefacts


def get_visible_profile_specs() -> Dict[str, ProfileSpec]:
    """Return visible profile specs."""
    return dict(VISIBLE_PROFILE_SPECS)


def list_profile_names(*, include_aliases: bool = False, visible_only: bool = False) -> List[str]:
    """Return available profile names."""
    if visible_only:
        names = list(VISIBLE_PROFILE_SPECS.keys())
    else:
        names = list(PROFILE_SPECS.keys())
    if include_aliases:
        names.extend(PROFILE_ALIASES.keys())
    return sorted(dict.fromkeys(names))


def resolve_profile_spec(name: str, *, config_path: str | Path | None = None) -> ProfileSpec:
    """Resolve the profile spec."""
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
    """Build the custom profile."""
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
    """Normalise the string list."""
    if value is None:
        return list(default)
    if not isinstance(value, list):
        raise ValueError("Expected a list of strings in custom profile config")
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_component_list(value: object, default: Sequence[str]) -> List[str]:
    """Normalise the component list."""
    return _normalize_string_list(value, default)


def _normalize_mapping(value: object, default: Mapping[str, str]) -> Dict[str, str]:
    """Normalise the mapping."""
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
    """Normalise the weight map."""
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ValueError("Expected component_weights to be an object")
    weights = {str(key): float(item) for key, item in value.items()}
    total = sum(weight for component, weight in weights.items() if component in required_components)
    if total <= 0:
        raise ValueError("component_weights must contain a positive total for required components")
    return weights


