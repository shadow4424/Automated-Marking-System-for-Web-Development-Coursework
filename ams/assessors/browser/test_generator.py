from __future__ import annotations

from typing import Dict, List

from ams.assessors.browser.coursework_spec import (
    CourseworkSpecification,
    RequiredFeature,
    RequiredFlow,
)
from ams.assessors.browser.functional_tests import FunctionalTestSuite


class TestGenerator:
    """Generates functional tests from coursework specification."""
    
    def __init__(self, coursework_spec: CourseworkSpecification):
        self.spec = coursework_spec
    
    def generate_tests(self) -> FunctionalTestSuite:
        """Generate functional test suite from coursework specification."""
        tests: List[Dict[str, any]] = []
        
        # Generate tests from required features
        for feature in self.spec.required_features:
            feature_tests = self._generate_tests_for_feature(feature)
            tests.extend(feature_tests)
        
        # Generate tests from required flows
        for flow in self.spec.required_flows:
            flow_tests = self._generate_tests_for_flow(flow)
            tests.extend(flow_tests)
        
        return FunctionalTestSuite(tests=tests)
    
    def _generate_tests_for_feature(self, feature: RequiredFeature) -> List[Dict[str, any]]:
        """Generate tests for a specific required feature."""
        tests: List[Dict[str, any]] = []
        test_config = feature.test_config or {}
        
        if feature.feature_type == "form":
            tests.append({
                "id": f"feature_{feature.id}",
                "name": f"{feature.name} - Form Submission",
                "type": "form_submit",
                "selector": test_config.get("selector", "form"),
                "expected": {
                    "dom_updates": True,
                    "navigates": test_config.get("navigates", False),
                    "success_selector": test_config.get("success_selector"),
                },
                "test_values": test_config.get("test_values", {}),
                "component": feature.component,
                "weight": feature.weight,
            })
            
            # Add validation test if validation is required
            if test_config.get("validation", False):
                validation_fields = test_config.get("fields", [])
                for field in validation_fields:
                    if field in ["email", "email_address"]:
                        tests.append({
                            "id": f"feature_{feature.id}_validation_{field}",
                            "name": f"{feature.name} - {field.title()} Validation",
                            "type": "validation",
                            "selector": f"input[name={field}], input[type=email]",
                            "invalid_value": "not-an-email",
                            "expected": {
                                "error_message": "email",
                            },
                            "component": feature.component,
                            "weight": feature.weight * 0.5,
                        })
        
        elif feature.feature_type == "navigation":
            tests.append({
                "id": f"feature_{feature.id}",
                "name": f"{feature.name} - Navigation",
                "type": "navigation",
                "selector": test_config.get("selector", "a[href]"),
                "expected": {
                    "navigates": True,
                },
                "min_links": test_config.get("min_links", 1),
                "component": feature.component,
                "weight": feature.weight,
            })
        
        elif feature.feature_type == "dynamic_update":
            tests.append({
                "id": f"feature_{feature.id}",
                "name": f"{feature.name} - Dynamic Update",
                "type": "dom_update",
                "trigger_selector": test_config.get("trigger_selector", "button"),
                "expected": {
                    "element_selector": test_config.get("expected_selector"),
                },
                "component": feature.component,
                "weight": feature.weight,
            })
        
        elif feature.feature_type == "validation":
            tests.append({
                "id": f"feature_{feature.id}",
                "name": f"{feature.name} - Validation",
                "type": "validation",
                "selector": test_config.get("selector", "input"),
                "invalid_value": test_config.get("invalid_value", ""),
                "expected": {
                    "error_message": test_config.get("error_message", ""),
                },
                "component": feature.component,
                "weight": feature.weight,
            })
        
        elif feature.feature_type == "authentication":
            # Login flow test
            tests.append({
                "id": f"feature_{feature.id}_login",
                "name": f"{feature.name} - Login Form",
                "type": "form_submit",
                "selector": test_config.get("login_form_selector", "form"),
                "expected": {
                    "navigates": True,
                    "success_selector": test_config.get("success_selector", ".welcome, .dashboard"),
                },
                "test_values": {
                    "username": test_config.get("test_username", "testuser"),
                    "password": test_config.get("test_password", "testpass"),
                },
                "component": feature.component,
                "weight": feature.weight,
            })
        
        return tests
    
    def _generate_tests_for_flow(self, flow: RequiredFlow) -> List[Dict[str, any]]:
        """Generate tests for a required user flow."""
        tests: List[Dict[str, any]] = []
        
        # Create a multi-step flow test
        test_steps = []
        for step in flow.steps:
            test_steps.append({
                "action": step.get("action"),
                "expected": step.get("expected"),
            })
        
        tests.append({
            "id": f"flow_{flow.id}",
            "name": f"{flow.name} - Complete Flow",
            "type": "flow",
            "steps": test_steps,
            "component": flow.component,
            "weight": flow.weight,
        })
        
        return tests
    
    def get_missing_features(self, test_results: List[Dict]) -> List[str]:
        """Identify missing required features based on test results."""
        missing = []
        
        for feature in self.spec.required_features:
            # Check if any test for this feature passed
            feature_tests = [t for t in test_results if t.get("test_id", "").startswith(f"feature_{feature.id}")]
            if not feature_tests or all(t.get("status") != "pass" for t in feature_tests):
                missing.append(feature.id)
        
        return missing


__all__ = ["TestGenerator"]

