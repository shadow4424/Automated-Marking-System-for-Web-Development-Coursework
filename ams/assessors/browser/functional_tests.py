from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ams.core.models import BrowserEvidence


@dataclass
class TestResult:
    """Result of a single functional test."""
    test_id: str
    test_name: str
    status: str  # "pass", "fail", "timeout", "skipped"
    message: str
    evidence: Dict[str, Any]
    duration_ms: int
    screenshot_path: Optional[str] = None


@dataclass
class FunctionalTestSuite:
    """Collection of functional tests to run."""
    tests: List[Dict[str, Any]]


class FunctionalTestRunner:
    """Runs functional tests using Playwright."""
    
    def __init__(self, timeout_ms: int = 10000, screenshot_dir: Optional[Path] = None):
        self.timeout_ms = timeout_ms
        self.screenshot_dir = screenshot_dir
    
    def run_test_suite(
        self, 
        page: Any,  # Playwright Page object
        entry_url: str,
        test_suite: FunctionalTestSuite
    ) -> List[TestResult]:
        """Run all tests in the suite and return results."""
        results: List[TestResult] = []
        
        for test_def in test_suite.tests:
            test_id = test_def.get("id", "unknown")
            test_name = test_def.get("name", test_id)
            test_type = test_def.get("type", "unknown")
            
            start_time = time.time()
            try:
                if test_type == "form_submit":
                    result = self._test_form_submit(page, test_def, entry_url)
                elif test_type == "button_click":
                    result = self._test_button_click(page, test_def, entry_url)
                elif test_type == "navigation":
                    result = self._test_navigation(page, test_def, entry_url)
                elif test_type == "validation":
                    result = self._test_validation(page, test_def, entry_url)
                elif test_type == "dom_update":
                    result = self._test_dom_update(page, test_def, entry_url)
                elif test_type == "link_click":
                    result = self._test_link_click(page, test_def, entry_url)
                else:
                    result = TestResult(
                        test_id=test_id,
                        test_name=test_name,
                        status="skipped",
                        message=f"Unknown test type: {test_type}",
                        evidence={"test_type": test_type},
                        duration_ms=0,
                    )
            except Exception as exc:
                result = TestResult(
                    test_id=test_id,
                    test_name=test_name,
                    status="fail",
                    message=f"Test execution error: {str(exc)}",
                    evidence={"error": str(exc), "test_type": test_type},
                    duration_ms=int((time.time() - start_time) * 1000),
                )
            
            result.duration_ms = int((time.time() - start_time) * 1000)
            results.append(result)
        
        return results
    
    def _test_form_submit(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test form submission functionality."""
        test_id = test_def.get("id", "form_submit")
        selector = test_def.get("selector", "form")
        expected_behavior = test_def.get("expected", {})
        
        try:
            # Navigate to page if needed
            if page.url != entry_url:
                page.goto(entry_url, wait_until="load", timeout=self.timeout_ms)
            
            # Find form
            form = page.query_selector(selector)
            if not form:
                return TestResult(
                    test_id=test_id,
                    test_name="Form Submit Test",
                    status="fail",
                    message=f"Form not found with selector: {selector}",
                    evidence={"selector": selector, "url": page.url},
                    duration_ms=0,
                )
            
            # Fill form fields
            inputs = form.query_selector_all("input, textarea, select")
            filled_fields = []
            for input_elem in inputs:
                input_type = input_elem.get_attribute("type") or "text"
                name = input_elem.get_attribute("name") or ""
                
                if input_type in ["text", "email", "password", "number", "tel", "url"]:
                    test_value = test_def.get("test_values", {}).get(name, "test_value")
                    input_elem.fill(test_value)
                    filled_fields.append({"name": name, "type": input_type, "value": test_value})
                elif input_type == "checkbox":
                    input_elem.check()
                    filled_fields.append({"name": name, "type": input_type, "checked": True})
                elif input_type == "radio":
                    input_elem.check()
                    filled_fields.append({"name": name, "type": input_type, "checked": True})
            
            # Capture DOM before submission
            dom_before = page.content()
            
            # Submit form
            submit_button = form.query_selector("button[type=submit], input[type=submit]")
            if submit_button:
                submit_button.click()
            else:
                form.evaluate("form => form.submit()")
            
            # Wait for response or navigation
            page.wait_for_timeout(1000)
            
            # Capture DOM after submission
            dom_after = page.content()
            
            # Check expected behaviors
            checks_passed = []
            checks_failed = []
            
            # Check for navigation (if expected)
            if expected_behavior.get("navigates"):
                if page.url != entry_url:
                    checks_passed.append("navigation_occurred")
                else:
                    checks_failed.append("navigation_expected_but_not_occurred")
            
            # Check for success message
            success_selector = expected_behavior.get("success_selector")
            if success_selector:
                success_elem = page.query_selector(success_selector)
                if success_elem:
                    checks_passed.append("success_message_found")
                else:
                    checks_failed.append("success_message_not_found")
            
            # Check for error message (if validation failed)
            error_selector = expected_behavior.get("error_selector")
            if error_selector:
                error_elem = page.query_selector(error_selector)
                if error_elem:
                    checks_passed.append("error_message_found")
                else:
                    # Error message not found is OK if form submitted successfully
                    pass
            
            # Check DOM changed (form submitted)
            dom_changed = dom_before != dom_after
            if dom_changed:
                checks_passed.append("dom_updated")
            else:
                checks_failed.append("dom_not_updated")
            
            # Take screenshot
            screenshot_path = None
            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(self.screenshot_dir / f"{test_id}_{int(time.time() * 1000)}.png")
                page.screenshot(path=screenshot_path, full_page=True)
            
            status = "pass" if not checks_failed else "fail"
            message = f"Form submit test: {len(checks_passed)} checks passed, {len(checks_failed)} failed"
            
            return TestResult(
                test_id=test_id,
                test_name="Form Submit Test",
                status=status,
                message=message,
                evidence={
                    "selector": selector,
                    "filled_fields": filled_fields,
                    "checks_passed": checks_passed,
                    "checks_failed": checks_failed,
                    "dom_changed": dom_changed,
                    "url_before": entry_url,
                    "url_after": page.url,
                },
                duration_ms=0,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            return TestResult(
                test_id=test_id,
                test_name="Form Submit Test",
                status="fail",
                message=f"Form submit test failed: {str(exc)}",
                evidence={"error": str(exc), "selector": selector},
                duration_ms=0,
            )
    
    def _test_button_click(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test button click functionality."""
        test_id = test_def.get("id", "button_click")
        selector = test_def.get("selector", "button")
        expected_behavior = test_def.get("expected", {})
        
        try:
            if page.url != entry_url:
                page.goto(entry_url, wait_until="load", timeout=self.timeout_ms)
            
            button = page.query_selector(selector)
            if not button:
                return TestResult(
                    test_id=test_id,
                    test_name="Button Click Test",
                    status="fail",
                    message=f"Button not found with selector: {selector}",
                    evidence={"selector": selector},
                    duration_ms=0,
                )
            
            # Capture state before click
            dom_before = page.content()
            text_before = button.inner_text()
            
            # Click button
            button.click()
            page.wait_for_timeout(500)
            
            # Capture state after click
            dom_after = page.content()
            button_after = page.query_selector(selector)
            text_after = button_after.inner_text() if button_after else ""
            
            # Check expected behaviors
            checks_passed = []
            checks_failed = []
            
            # Check if DOM updated
            dom_changed = dom_before != dom_after
            if dom_changed:
                checks_passed.append("dom_updated")
            else:
                checks_failed.append("dom_not_updated")
            
            # Check if button text changed (if expected)
            if expected_behavior.get("text_changes"):
                if text_before != text_after:
                    checks_passed.append("button_text_changed")
                else:
                    checks_failed.append("button_text_expected_to_change")
            
            # Check for specific element appearing
            appear_selector = expected_behavior.get("element_appears")
            if appear_selector:
                appeared = page.query_selector(appear_selector)
                if appeared:
                    checks_passed.append("expected_element_appeared")
                else:
                    checks_failed.append("expected_element_not_appeared")
            
            screenshot_path = None
            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(self.screenshot_dir / f"{test_id}_{int(time.time() * 1000)}.png")
                page.screenshot(path=screenshot_path, full_page=True)
            
            status = "pass" if not checks_failed else "fail"
            message = f"Button click test: {len(checks_passed)} checks passed, {len(checks_failed)} failed"
            
            return TestResult(
                test_id=test_id,
                test_name="Button Click Test",
                status=status,
                message=message,
                evidence={
                    "selector": selector,
                    "checks_passed": checks_passed,
                    "checks_failed": checks_failed,
                    "dom_changed": dom_changed,
                    "text_before": text_before,
                    "text_after": text_after,
                },
                duration_ms=0,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            return TestResult(
                test_id=test_id,
                test_name="Button Click Test",
                status="fail",
                message=f"Button click test failed: {str(exc)}",
                evidence={"error": str(exc), "selector": selector},
                duration_ms=0,
            )
    
    def _test_navigation(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test navigation functionality."""
        test_id = test_def.get("id", "navigation")
        link_selector = test_def.get("selector", "a")
        expected_url = test_def.get("expected", {}).get("url")
        
        try:
            if page.url != entry_url:
                page.goto(entry_url, wait_until="load", timeout=self.timeout_ms)
            
            link = page.query_selector(link_selector)
            if not link:
                return TestResult(
                    test_id=test_id,
                    test_name="Navigation Test",
                    status="fail",
                    message=f"Link not found with selector: {link_selector}",
                    evidence={"selector": link_selector},
                    duration_ms=0,
                )
            
            href = link.get_attribute("href")
            url_before = page.url
            
            # Click link
            link.click()
            page.wait_for_timeout(1000)
            
            url_after = page.url
            
            # Check if navigation occurred
            navigated = url_before != url_after
            checks_passed = []
            checks_failed = []
            
            if navigated:
                checks_passed.append("navigation_occurred")
                if expected_url and expected_url in url_after:
                    checks_passed.append("navigated_to_expected_url")
                elif expected_url:
                    checks_failed.append("navigated_to_unexpected_url")
            else:
                checks_failed.append("navigation_not_occurred")
            
            screenshot_path = None
            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(self.screenshot_dir / f"{test_id}_{int(time.time() * 1000)}.png")
                page.screenshot(path=screenshot_path, full_page=True)
            
            status = "pass" if navigated and not checks_failed else "fail"
            message = f"Navigation test: {len(checks_passed)} checks passed, {len(checks_failed)} failed"
            
            return TestResult(
                test_id=test_id,
                test_name="Navigation Test",
                status=status,
                message=message,
                evidence={
                    "selector": link_selector,
                    "href": href,
                    "url_before": url_before,
                    "url_after": url_after,
                    "checks_passed": checks_passed,
                    "checks_failed": checks_failed,
                },
                duration_ms=0,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            return TestResult(
                test_id=test_id,
                test_name="Navigation Test",
                status="fail",
                message=f"Navigation test failed: {str(exc)}",
                evidence={"error": str(exc), "selector": link_selector},
                duration_ms=0,
            )
    
    def _test_validation(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test client-side validation."""
        test_id = test_def.get("id", "validation")
        input_selector = test_def.get("selector", "input")
        invalid_value = test_def.get("invalid_value", "")
        expected_error = test_def.get("expected", {}).get("error_message")
        
        try:
            if page.url != entry_url:
                page.goto(entry_url, wait_until="load", timeout=self.timeout_ms)
            
            input_elem = page.query_selector(input_selector)
            if not input_elem:
                return TestResult(
                    test_id=test_id,
                    test_name="Validation Test",
                    status="fail",
                    message=f"Input not found with selector: {input_selector}",
                    evidence={"selector": input_selector},
                    duration_ms=0,
                )
            
            # Try to submit invalid value
            input_elem.fill(invalid_value)
            
            # Try to submit form - find form containing this input
            form_selector = "form"
            form = page.query_selector(form_selector)
            if form:
                submit_button = page.query_selector("button[type=submit], input[type=submit]")
                if submit_button:
                    submit_button.click()
                else:
                    form.evaluate("form => form.submit()")
            
            page.wait_for_timeout(500)
            
            # Check for validation message
            validation_message = input_elem.evaluate("el => el.validationMessage")
            is_valid = input_elem.evaluate("el => el.validity.valid")
            
            checks_passed = []
            checks_failed = []
            
            if not is_valid:
                checks_passed.append("validation_triggered")
                if validation_message:
                    checks_passed.append("validation_message_shown")
                    if expected_error and expected_error.lower() in validation_message.lower():
                        checks_passed.append("expected_error_message_found")
                else:
                    checks_failed.append("validation_message_not_shown")
            else:
                checks_failed.append("validation_not_triggered")
            
            screenshot_path = None
            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(self.screenshot_dir / f"{test_id}_{int(time.time() * 1000)}.png")
                page.screenshot(path=screenshot_path, full_page=True)
            
            status = "pass" if checks_passed and not checks_failed else "fail"
            message = f"Validation test: {len(checks_passed)} checks passed, {len(checks_failed)} failed"
            
            return TestResult(
                test_id=test_id,
                test_name="Validation Test",
                status=status,
                message=message,
                evidence={
                    "selector": input_selector,
                    "invalid_value": invalid_value,
                    "validation_message": validation_message,
                    "is_valid": is_valid,
                    "checks_passed": checks_passed,
                    "checks_failed": checks_failed,
                },
                duration_ms=0,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            return TestResult(
                test_id=test_id,
                test_name="Validation Test",
                status="fail",
                message=f"Validation test failed: {str(exc)}",
                evidence={"error": str(exc), "selector": input_selector},
                duration_ms=0,
            )
    
    def _test_dom_update(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test dynamic DOM updates."""
        test_id = test_def.get("id", "dom_update")
        trigger_selector = test_def.get("trigger_selector", "button")
        expected_selector = test_def.get("expected", {}).get("element_selector")
        
        try:
            if page.url != entry_url:
                page.goto(entry_url, wait_until="load", timeout=self.timeout_ms)
            
            trigger = page.query_selector(trigger_selector)
            if not trigger:
                return TestResult(
                    test_id=test_id,
                    test_name="DOM Update Test",
                    status="fail",
                    message=f"Trigger element not found: {trigger_selector}",
                    evidence={"trigger_selector": trigger_selector},
                    duration_ms=0,
                )
            
            # Check if expected element exists before trigger
            element_before = page.query_selector(expected_selector) if expected_selector else None
            
            # Trigger action
            trigger.click()
            page.wait_for_timeout(1000)
            
            # Check if expected element appears/changes
            element_after = page.query_selector(expected_selector) if expected_selector else None
            
            checks_passed = []
            checks_failed = []
            
            if expected_selector:
                if element_after and not element_before:
                    checks_passed.append("element_appeared")
                elif element_after and element_before:
                    # Check if content changed
                    text_before = element_before.inner_text()
                    text_after = element_after.inner_text()
                    if text_before != text_after:
                        checks_passed.append("element_content_updated")
                    else:
                        checks_failed.append("element_content_not_updated")
                else:
                    checks_failed.append("expected_element_not_found")
            
            screenshot_path = None
            if self.screenshot_dir:
                self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = str(self.screenshot_dir / f"{test_id}_{int(time.time() * 1000)}.png")
                page.screenshot(path=screenshot_path, full_page=True)
            
            status = "pass" if checks_passed and not checks_failed else "fail"
            message = f"DOM update test: {len(checks_passed)} checks passed, {len(checks_failed)} failed"
            
            return TestResult(
                test_id=test_id,
                test_name="DOM Update Test",
                status=status,
                message=message,
                evidence={
                    "trigger_selector": trigger_selector,
                    "expected_selector": expected_selector,
                    "checks_passed": checks_passed,
                    "checks_failed": checks_failed,
                },
                duration_ms=0,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            return TestResult(
                test_id=test_id,
                test_name="DOM Update Test",
                status="fail",
                message=f"DOM update test failed: {str(exc)}",
                evidence={"error": str(exc)},
                duration_ms=0,
            )
    
    def _test_link_click(self, page: Any, test_def: Dict[str, Any], entry_url: str) -> TestResult:
        """Test link click functionality (similar to navigation but simpler)."""
        return self._test_navigation(page, test_def, entry_url)


def create_default_test_suite() -> FunctionalTestSuite:
    """Create a default test suite with common functional tests."""
    return FunctionalTestSuite(
        tests=[
            {
                "id": "test_form_submit_1",
                "name": "Form Submission Test",
                "type": "form_submit",
                "selector": "form",
                "expected": {
                    "dom_updates": True,
                },
                "test_values": {
                    "name": "Test User",
                    "email": "test@example.com",
                },
            },
            {
                "id": "test_button_click_1",
                "name": "Button Click Test",
                "type": "button_click",
                "selector": "button",
                "expected": {
                    "dom_updates": True,
                },
            },
            {
                "id": "test_navigation_1",
                "name": "Navigation Link Test",
                "type": "navigation",
                "selector": "a[href]",
                "expected": {
                    "navigates": True,
                },
            },
            {
                "id": "test_validation_email",
                "name": "Email Validation Test",
                "type": "validation",
                "selector": "input[type=email]",
                "invalid_value": "not-an-email",
                "expected": {
                    "error_message": "email",
                },
            },
        ]
    )


__all__ = [
    "FunctionalTestRunner",
    "FunctionalTestSuite",
    "TestResult",
    "create_default_test_suite",
]

