from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping

from ams.core.profiles import ProfileSpec, RequirementDefinition, resolve_profile_spec


@dataclass(frozen=True)
class ResolvedAssignmentConfig:
    requested_profile: str
    profile_name: str
    profile: ProfileSpec
    expected_layers: List[str]
    required_components: List[str]
    optional_components: List[str]
    enabled_static_checks: List[str]
    enabled_behavioural_checks: List[str]
    enabled_browser_checks: List[str]
    enabled_layout_checks: List[str]
    expected_entrypoint_types: List[str]
    component_weights: Dict[str, float]
    missing_component_treatment: Dict[str, str]
    role_expectations: Dict[str, str]
    requirement_definitions: List[RequirementDefinition]
    frontend_only: bool
    config_source: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "requested_profile": self.requested_profile,
            "profile_name": self.profile_name,
            "expected_layers": list(self.expected_layers),
            "required_components": list(self.required_components),
            "optional_components": list(self.optional_components),
            "enabled_static_checks": list(self.enabled_static_checks),
            "enabled_behavioural_checks": list(self.enabled_behavioural_checks),
            "enabled_browser_checks": list(self.enabled_browser_checks),
            "enabled_layout_checks": list(self.enabled_layout_checks),
            "expected_entrypoint_types": list(self.expected_entrypoint_types),
            "component_weights": dict(self.component_weights),
            "missing_component_treatment": dict(self.missing_component_treatment),
            "role_expectations": dict(self.role_expectations),
            "requirement_definitions": [item.to_dict() for item in self.requirement_definitions],
            "frontend_only": self.frontend_only,
            "config_source": self.config_source,
        }


def resolve_assignment_config(
    profile_name: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> ResolvedAssignmentConfig:
    metadata = metadata or {}
    config_path = metadata.get("profile_config_path") or metadata.get("assignment_profile_config")
    profile = resolve_profile_spec(profile_name, config_path=config_path)
    component_weights = _normalize_weights(profile)
    return ResolvedAssignmentConfig(
        requested_profile=profile_name,
        profile_name=profile.name,
        profile=profile,
        expected_layers=list(profile.expected_layers or profile.enabled_components()),
        required_components=list(profile.relevant_artefacts),
        optional_components=list(profile.optional_components),
        enabled_static_checks=list(profile.enabled_static_checks),
        enabled_behavioural_checks=list(profile.enabled_behavioural_checks),
        enabled_browser_checks=list(profile.enabled_browser_checks),
        enabled_layout_checks=list(profile.enabled_layout_checks),
        expected_entrypoint_types=list(profile.expected_entrypoint_types),
        component_weights=component_weights,
        missing_component_treatment=dict(profile.missing_component_treatment),
        role_expectations=dict(profile.role_expectations),
        requirement_definitions=list(profile.build_requirement_definitions()),
        frontend_only=profile.frontend_only,
        config_source=str(config_path) if config_path else "builtin",
    )


def _normalize_weights(profile: ProfileSpec) -> Dict[str, float]:
    if profile.component_weights:
        total = sum(
            weight
            for component, weight in profile.component_weights.items()
            if component in profile.relevant_artefacts
        )
        if total > 0:
            return {
                component: (float(weight) / total if component in profile.relevant_artefacts else float(weight))
                for component, weight in profile.component_weights.items()
            }
    required = list(profile.relevant_artefacts)
    return {
        component: (1.0 / len(required) if component in required and required else 0.0)
        for component in profile.enabled_components()
    }


__all__ = ["ResolvedAssignmentConfig", "resolve_assignment_config"]
