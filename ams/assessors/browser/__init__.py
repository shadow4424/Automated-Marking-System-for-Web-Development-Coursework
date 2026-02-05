from __future__ import annotations

from .coursework_spec import (
    CourseworkSpecification,
    RequiredFeature,
    RequiredFlow,
    RequiredPage,
    create_default_coursework_spec,
    load_coursework_spec_from_dict,
    load_coursework_spec_from_json,
)
from .enhanced_playwright_assessor import EnhancedPlaywrightAssessor
from .error_detection import ErrorDetector, ErrorEvidence
from .functional_tests import (
    FunctionalTestRunner,
    FunctionalTestSuite,
    TestResult,
    create_default_test_suite,
)
from .performance_checks import (
    PerformanceChecker,
    PerformanceMetrics,
    PerformanceThresholds,
)
from .playwright_assessor import BrowserRunner, BrowserRunResult, PlaywrightAssessor, PlaywrightRunner
from .test_generator import TestGenerator

__all__ = [
    "PlaywrightAssessor",
    "EnhancedPlaywrightAssessor",
    "BrowserRunner",
    "PlaywrightRunner",
    "BrowserRunResult",
    "FunctionalTestRunner",
    "FunctionalTestSuite",
    "TestResult",
    "create_default_test_suite",
    "CourseworkSpecification",
    "RequiredPage",
    "RequiredFeature",
    "RequiredFlow",
    "create_default_coursework_spec",
    "load_coursework_spec_from_dict",
    "load_coursework_spec_from_json",
    "TestGenerator",
    "PerformanceChecker",
    "PerformanceThresholds",
    "PerformanceMetrics",
    "ErrorDetector",
    "ErrorEvidence",
]
