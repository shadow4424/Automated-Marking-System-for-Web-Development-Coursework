from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ams.assessors.base import Assessor
from ams.assessors.browser.coursework_spec import (
    CourseworkSpecification,
    create_default_coursework_spec,
    load_coursework_spec_from_dict,
)
from ams.assessors.browser.error_detection import ErrorDetector, ErrorEvidence
from ams.assessors.browser.functional_tests import (
    FunctionalTestRunner,
    FunctionalTestSuite,
    TestResult,
    create_default_test_suite,
)
from ams.assessors.browser.performance_checks import (
    PerformanceChecker,
    PerformanceThresholds,
)
from ams.assessors.browser.playwright_assessor import BrowserRunResult, PlaywrightRunner
from ams.assessors.browser.test_generator import TestGenerator
from ams.core.models import BrowserEvidence, Finding, FindingCategory, Severity, SubmissionContext
from ams.core.profiles import get_profile_spec


def _cap(text: str, limit: int = 10_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


class EnhancedPlaywrightAssessor(Assessor):
    """
    Enhanced browser automation assessor with comprehensive functional tests.
    
    Runs a suite of functional tests using Playwright to verify:
    - Form submission
    - Button interactions
    - Navigation
    - Client-side validation
    - Dynamic DOM updates
    """

    name = "enhanced_browser_automation"

    def __init__(
        self,
        runner: Optional[PlaywrightRunner] = None,
        test_suite: Optional[FunctionalTestSuite] = None,
        coursework_spec: Optional[CourseworkSpecification] = None,
        performance_thresholds: Optional[PerformanceThresholds] = None,
        output_cap: int = 10_000,
    ) -> None:
        self.runner = runner or PlaywrightRunner()
        self.coursework_spec = coursework_spec or create_default_coursework_spec()
        self.performance_thresholds = performance_thresholds or PerformanceThresholds(
            **self.coursework_spec.performance_thresholds
        ) if self.coursework_spec.performance_thresholds else PerformanceThresholds()
        
        # Generate tests from coursework spec if not provided
        if test_suite is None:
            test_generator = TestGenerator(self.coursework_spec)
            self.test_suite = test_generator.generate_tests()
        else:
            self.test_suite = test_suite
        
        self.output_cap = output_cap
        self.performance_checker = PerformanceChecker(self.performance_thresholds)
        self.error_detector = ErrorDetector()

    def run(self, context: SubmissionContext) -> List[Finding]:
        findings: List[Finding] = []
        profile = context.metadata.get("profile", "unknown")
        
        try:
            profile_spec = get_profile_spec(profile)
        except ValueError:
            profile_spec = None

        entry = self._select_entrypoint(context)
        if not entry:
            findings.append(
                self._finding(
                    "BROWSER.FUNCTIONAL.SKIPPED",
                    "No HTML entrypoint found for functional tests.",
                    Severity.SKIPPED,
                    profile=profile,
                    evidence={"reason": "no_html_found"},
                )
            )
            return findings

        # Run basic page load first
        basic_result = self.runner.run(entry, context.workspace_path, interaction=False)
        
        if basic_result.status != "pass":
            # If basic load fails, return basic findings
            findings.append(
                self._finding(
                    "BROWSER.PAGE_LOAD_FAIL" if basic_result.status == "fail" else "BROWSER.PAGE_LOAD_TIMEOUT",
                    f"Page load failed: {basic_result.status}",
                    Severity.FAIL,
                    profile=profile,
                    evidence={"entry": str(entry), "status": basic_result.status},
                )
            )
            return findings

        # Run functional tests
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            findings.append(
                self._finding(
                    "BROWSER.FUNCTIONAL.SKIPPED",
                    f"Playwright unavailable for functional tests: {exc}",
                    Severity.SKIPPED,
                    profile=profile,
                    evidence={"reason": "playwright_unavailable", "error": str(exc)},
                )
            )
            return findings

        screenshot_dir = context.workspace_path / "artifacts" / "browser" / "functional_tests"
        test_runner = FunctionalTestRunner(
            timeout_ms=self.runner.timeout_ms,
            screenshot_dir=screenshot_dir,
        )

        test_results: List[TestResult] = []
        browser_evidence_list: List[BrowserEvidence] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set up console and network error handlers
            console_errors: List[str] = []
            network_errors: List[str] = []
            total_requests = 0
            
            def _handle_console(msg) -> None:
                if msg.type == "error":
                    console_errors.append(_cap(msg.text, self.output_cap))
            
            def _handle_failed_request(request) -> None:
                network_errors.append(_cap(f"{request.url}: {request.failure}", self.output_cap))
            
            def _handle_request(request) -> None:
                nonlocal total_requests
                total_requests += 1
            
            page.on("console", _handle_console)
            page.on("requestfailed", _handle_failed_request)
            page.on("request", _handle_request)
            
            # Set up error tracking in page
            page.evaluate("""
                () => {
                    window.errors = [];
                    window.addEventListener('error', (e) => {
                        window.errors.push({
                            message: e.message,
                            filename: e.filename,
                            lineno: e.lineno,
                            colno: e.colno,
                            error: e.error ? e.error.toString() : null
                        });
                    });
                    window.addEventListener('unhandledrejection', (e) => {
                        window.errors.push({
                            message: 'Unhandled Promise Rejection: ' + (e.reason ? e.reason.toString() : 'Unknown'),
                            type: 'unhandledrejection'
                        });
                    });
                }
            """)
            
            entry_url = entry.as_uri()
            
            try:
                # Load the page and measure performance
                page_load_metrics = self.performance_checker.measure_page_load(page, entry_url)
                
                # Detect errors during page load
                js_errors = self.error_detector.detect_javascript_errors(page, console_errors)
                network_errors_detected = self.error_detector.detect_network_errors(network_errors, page)
                php_errors = self.error_detector.detect_php_errors(page)
                
                # Store total requests for performance checks
                self._total_requests = total_requests
                
                # Run functional test suite
                test_results = test_runner.run_test_suite(page, entry_url, self.test_suite)
                
                # Run performance checks
                performance_checks = self._run_performance_checks(page, test_results)
                
                # Detect errors during test execution
                form_errors = self.error_detector.detect_form_submission_errors(page)
                all_errors = self.error_detector.get_all_errors()
                
                # Create browser evidence for each test
                for test_result in test_results:
                    evidence = BrowserEvidence(
                        test_id=f"BROWSER.FUNCTIONAL.{test_result.test_id}",
                        stage="browser",
                        status=test_result.status,
                        duration_ms=test_result.duration_ms,
                        url=entry_url,
                        actions=[{
                            "type": "functional_test",
                            "test_id": test_result.test_id,
                            "test_name": test_result.test_name,
                            "status": test_result.status,
                        }],
                        dom_before="",  # Could capture if needed
                        dom_after="",
                        console_errors=console_errors[:20],
                        network_errors=network_errors[:20],
                        screenshot_paths=[test_result.screenshot_path] if test_result.screenshot_path else [],
                        notes=test_result.message,
                    )
                    browser_evidence_list.append(evidence)
                    context.browser_evidence.append(evidence)
                
                # Create evidence for performance checks
                if performance_checks:
                    perf_evidence = BrowserEvidence(
                        test_id="BROWSER.PERFORMANCE",
                        stage="browser",
                        status="pass" if all(p.get("passed", False) for p in performance_checks.values()) else "fail",
                        duration_ms=0,
                        url=entry_url,
                        actions=[{"type": "performance_check", "metrics": performance_checks}],
                        notes="Performance metrics collected",
                    )
                    browser_evidence_list.append(perf_evidence)
                    context.browser_evidence.append(perf_evidence)
                
                # Create evidence for error detection
                if all_errors:
                    error_evidence = BrowserEvidence(
                        test_id="BROWSER.ERRORS",
                        stage="browser",
                        status="fail" if self.error_detector.has_critical_errors() else "warn",
                        duration_ms=0,
                        url=entry_url,
                        actions=[{
                            "type": "error_detection",
                            "error_count": len(all_errors),
                            "critical_count": len(self.error_detector.get_critical_errors()),
                        }],
                        notes=f"Detected {len(all_errors)} errors",
                    )
                    browser_evidence_list.append(error_evidence)
                    context.browser_evidence.append(error_evidence)
                
            except Exception as exc:
                findings.append(
                    self._finding(
                        "BROWSER.FUNCTIONAL.ERROR",
                        f"Functional test execution error: {str(exc)}",
                        Severity.FAIL,
                        profile=profile,
                        evidence={"error": str(exc), "entry": str(entry)},
                    )
                )
            finally:
                browser.close()

        # Generate findings from test results
        findings.extend(self._generate_findings_from_tests(test_results, profile, entry))
        
        # Add summary finding
        passed_tests = sum(1 for tr in test_results if tr.status == "pass")
        failed_tests = sum(1 for tr in test_results if tr.status == "fail")
        total_tests = len(test_results)
        
        if total_tests > 0:
            findings.append(
                self._finding(
                    "BROWSER.FUNCTIONAL.SUMMARY",
                    f"Functional tests: {passed_tests}/{total_tests} passed, {failed_tests} failed",
                    Severity.INFO if failed_tests == 0 else Severity.WARN,
                    profile=profile,
                    evidence={
                        "total_tests": total_tests,
                        "passed": passed_tests,
                        "failed": failed_tests,
                        "skipped": sum(1 for tr in test_results if tr.status == "skipped"),
                    },
                )
            )
        
        # Add findings for missing required features
        test_results_dict = [{"test_id": tr.test_id, "status": tr.status} for tr in test_results]
        test_generator = TestGenerator(self.coursework_spec)
        missing_features = test_generator.get_missing_features(test_results_dict)
        
        if missing_features:
            findings.append(
                self._finding(
                    "BROWSER.REQUIRED_FEATURES_MISSING",
                    f"Missing required features: {', '.join(missing_features)}",
                    Severity.FAIL,
                    profile=profile,
                    evidence={
                        "missing_features": missing_features,
                        "total_required": len(self.coursework_spec.required_features),
                    },
                )
            )
        
        # Add performance findings
        if 'performance_checks' in locals():
            findings.extend(self._generate_performance_findings(performance_checks, profile))
        
        # Add error findings
        if 'all_errors' in locals():
            findings.extend(self._generate_error_findings(all_errors, profile))

        return findings
    
    def _run_performance_checks(self, page: Any, test_results: List[TestResult]) -> Dict[str, Any]:
        """Run performance checks."""
        checks = {}
        
        # Check console errors
        console_check = self.performance_checker.check_console_errors(
            [e.error_message for e in self.error_detector.get_errors_by_type("javascript")]
        )
        checks["console_errors"] = console_check
        
        # Check network health
        network_check = self.performance_checker.check_network_health(
            [e.error_message for e in self.error_detector.get_errors_by_type("network")],
            getattr(self, '_total_requests', 1)  # Avoid division by zero
        )
        checks["network_health"] = network_check
        
        # Check responsiveness
        responsiveness_check = self.performance_checker.check_responsiveness(page)
        checks["responsiveness"] = responsiveness_check
        
        # Check for infinite loops
        infinite_loop_check = self.performance_checker.check_infinite_loops(page)
        checks["infinite_loops"] = infinite_loop_check
        
        return checks
    
    def _generate_performance_findings(self, performance_checks: Dict[str, Any], profile: str) -> List[Finding]:
        """Generate findings from performance checks."""
        findings = []
        
        for check_name, check_result in performance_checks.items():
            if not check_result.get("passed", True):
                severity = Severity.WARN if check_name in ["console_errors", "network_health"] else Severity.FAIL
                findings.append(
                    self._finding(
                        f"BROWSER.PERFORMANCE.{check_name.upper()}",
                        f"Performance check failed: {check_name}",
                        severity,
                        profile=profile,
                        evidence=check_result,
                    )
                )
            else:
                findings.append(
                    self._finding(
                        f"BROWSER.PERFORMANCE.{check_name.upper()}_PASS",
                        f"Performance check passed: {check_name}",
                        Severity.INFO,
                        profile=profile,
                        evidence=check_result,
                    )
                )
        
        return findings
    
    def _generate_error_findings(self, errors: List[ErrorEvidence], profile: str) -> List[Finding]:
        """Generate findings from detected errors."""
        findings = []
        
        # Group errors by type
        errors_by_type: Dict[str, List[ErrorEvidence]] = {}
        for error in errors:
            error_type = error.error_type
            if error_type not in errors_by_type:
                errors_by_type[error_type] = []
            errors_by_type[error_type].append(error)
        
        # Create findings for each error type
        for error_type, type_errors in errors_by_type.items():
            critical_errors = [e for e in type_errors if e.severity == "error"]
            warnings = [e for e in type_errors if e.severity == "warning"]
            
            if critical_errors:
                findings.append(
                    self._finding(
                        f"BROWSER.ERROR.{error_type.upper()}_CRITICAL",
                        f"Critical {error_type} errors detected: {len(critical_errors)}",
                        Severity.FAIL,
                        profile=profile,
                        evidence={
                            "error_count": len(critical_errors),
                            "errors": [
                                {
                                    "message": e.error_message[:200],
                                    "source": e.error_source,
                                }
                                for e in critical_errors[:5]
                            ],
                        },
                    )
                )
            
            if warnings:
                findings.append(
                    self._finding(
                        f"BROWSER.ERROR.{error_type.upper()}_WARNINGS",
                        f"{error_type.title()} warnings detected: {len(warnings)}",
                        Severity.WARN,
                        profile=profile,
                        evidence={
                            "warning_count": len(warnings),
                            "warnings": [
                                {
                                    "message": e.error_message[:200],
                                    "source": e.error_source,
                                }
                                for e in warnings[:5]
                            ],
                        },
                    )
                )
        
        return findings

    def _generate_findings_from_tests(
        self, 
        test_results: List[TestResult], 
        profile: str, 
        entry: Path
    ) -> List[Finding]:
        """Generate structured findings from test results."""
        findings: List[Finding] = []
        
        for test_result in test_results:
            finding_id = f"BROWSER.FUNCTIONAL.{test_result.test_id}"
            
            if test_result.status == "pass":
                severity = Severity.INFO
                message = f"{test_result.test_name} passed: {test_result.message}"
            elif test_result.status == "fail":
                severity = Severity.WARN
                message = f"{test_result.test_name} failed: {test_result.message}"
            elif test_result.status == "timeout":
                severity = Severity.FAIL
                message = f"{test_result.test_name} timed out"
            else:  # skipped
                severity = Severity.SKIPPED
                message = f"{test_result.test_name} skipped: {test_result.message}"
            
            findings.append(
                Finding(
                    id=finding_id,
                    category="browser",
                    message=message,
                    severity=severity,
                    evidence={
                        "test_id": test_result.test_id,
                        "test_name": test_result.test_name,
                        "status": test_result.status,
                        "duration_ms": test_result.duration_ms,
                        "screenshot": test_result.screenshot_path,
                        **test_result.evidence,
                    },
                    source=self.name,
                    finding_category=FindingCategory.BEHAVIORAL,
                    profile=profile,
                    required=False,
                )
            )
        
        return findings

    def _select_entrypoint(self, context: SubmissionContext) -> Optional[Path]:
        """Select the HTML entry point for testing."""
        html_files = sorted(context.discovered_files.get("html", []))
        if not html_files:
            return None
        for path in html_files:
            if path.name.lower() == "index.html":
                return path
        return html_files[0]

    def _finding(
        self,
        code: str,
        message: str,
        severity: Severity,
        profile: str,
        evidence: Optional[Mapping[str, object]] = None,
        required: bool = False,
    ) -> Finding:
        """Helper to create findings."""
        evidence_data = dict(evidence or {})
        if profile is not None and "profile" not in evidence_data:
            evidence_data["profile"] = profile
        if required is not None and "required" not in evidence_data:
            evidence_data["required"] = required
        return Finding(
            id=code,
            category="browser",
            message=message,
            severity=severity,
            evidence=evidence_data,
            source=self.name,
            finding_category=FindingCategory.BEHAVIORAL,
            profile=profile,
            required=required,
        )


__all__ = ["EnhancedPlaywrightAssessor"]

