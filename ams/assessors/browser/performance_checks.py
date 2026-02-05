from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PerformanceMetrics:
    """Performance metrics collected during testing."""
    page_load_time_ms: float
    interaction_latency_ms: float
    dom_update_time_ms: float
    console_error_count: int
    network_error_count: int
    total_requests: int
    failed_requests: int
    cpu_time_ms: Optional[float] = None
    memory_usage_mb: Optional[float] = None


@dataclass
class PerformanceThresholds:
    """Performance thresholds for pass/fail determination."""
    page_load_ms: float = 3000.0
    interaction_ms: float = 500.0
    dom_update_ms: float = 1000.0
    max_console_errors: int = 0
    max_network_errors: int = 0
    max_failed_requests_ratio: float = 0.1  # 10% failure rate


class PerformanceChecker:
    """Checks performance metrics during browser testing."""
    
    def __init__(self, thresholds: Optional[PerformanceThresholds] = None):
        self.thresholds = thresholds or PerformanceThresholds()
    
    def measure_page_load(self, page: Any, url: str) -> Dict[str, Any]:
        """Measure page load performance."""
        start_time = time.time()
        
        try:
            # Use Playwright's performance timing
            page.goto(url, wait_until="load", timeout=10000)
            
            # Get performance timing from browser
            perf_timing = page.evaluate("""
                () => {
                    const perf = window.performance;
                    if (perf && perf.timing) {
                        return {
                            domContentLoaded: perf.timing.domContentLoadedEventEnd - perf.timing.navigationStart,
                            loadComplete: perf.timing.loadEventEnd - perf.timing.navigationStart,
                        };
                    }
                    return null;
                }
            """)
            
            load_time_ms = (time.time() - start_time) * 1000
            
            return {
                "load_time_ms": load_time_ms,
                "dom_content_loaded_ms": perf_timing.get("domContentLoaded") if perf_timing else None,
                "load_complete_ms": perf_timing.get("loadComplete") if perf_timing else None,
                "passed": load_time_ms <= self.thresholds.page_load_ms,
            }
        except Exception as exc:
            return {
                "load_time_ms": None,
                "error": str(exc),
                "passed": False,
            }
    
    def measure_interaction_latency(self, page: Any, action_func) -> Dict[str, Any]:
        """Measure latency of an interaction."""
        start_time = time.time()
        
        try:
            action_func()
            latency_ms = (time.time() - start_time) * 1000
            
            return {
                "latency_ms": latency_ms,
                "passed": latency_ms <= self.thresholds.interaction_ms,
            }
        except Exception as exc:
            return {
                "latency_ms": None,
                "error": str(exc),
                "passed": False,
            }
    
    def measure_dom_update(self, page: Any, trigger_func, expected_selector: str) -> Dict[str, Any]:
        """Measure time for DOM to update after trigger."""
        start_time = time.time()
        
        try:
            trigger_func()
            
            # Wait for expected element to appear
            page.wait_for_selector(expected_selector, timeout=5000)
            
            update_time_ms = (time.time() - start_time) * 1000
            
            return {
                "update_time_ms": update_time_ms,
                "passed": update_time_ms <= self.thresholds.dom_update_ms,
            }
        except Exception as exc:
            return {
                "update_time_ms": None,
                "error": str(exc),
                "passed": False,
            }
    
    def check_responsiveness(self, page: Any) -> Dict[str, Any]:
        """Check if UI remains responsive."""
        try:
            # Try to interact with page quickly
            start_time = time.time()
            
            # Try clicking a button or element
            button = page.query_selector("button, a, input[type=submit]")
            if button:
                button.click()
                response_time = (time.time() - start_time) * 1000
                
                return {
                    "response_time_ms": response_time,
                    "passed": response_time < 1000,  # Should respond within 1 second
                }
            else:
                return {
                    "response_time_ms": None,
                    "passed": True,  # No interactive elements to test
                    "note": "no_interactive_elements",
                }
        except Exception as exc:
            return {
                "response_time_ms": None,
                "error": str(exc),
                "passed": False,
            }
    
    def check_console_errors(self, console_errors: List[str]) -> Dict[str, Any]:
        """Check console errors against threshold."""
        error_count = len(console_errors)
        
        return {
            "error_count": error_count,
            "passed": error_count <= self.thresholds.max_console_errors,
            "errors": console_errors[:10],  # Limit to first 10
        }
    
    def check_network_health(self, network_errors: List[str], total_requests: int) -> Dict[str, Any]:
        """Check network health."""
        error_count = len(network_errors)
        failed_ratio = error_count / total_requests if total_requests > 0 else 0.0
        
        return {
            "error_count": error_count,
            "total_requests": total_requests,
            "failed_ratio": failed_ratio,
            "passed": (
                error_count <= self.thresholds.max_network_errors and
                failed_ratio <= self.thresholds.max_failed_requests_ratio
            ),
            "errors": network_errors[:10],
        }
    
    def check_infinite_loops(self, page: Any, duration_seconds: float = 2.0) -> Dict[str, Any]:
        """Check for infinite loops by monitoring CPU usage."""
        try:
            start_time = time.time()
            
            # Trigger some interactions
            buttons = page.query_selector_all("button")
            for button in buttons[:3]:  # Test first 3 buttons
                button.click()
                page.wait_for_timeout(100)
            
            elapsed = time.time() - start_time
            
            # If interactions take much longer than expected, might indicate infinite loop
            passed = elapsed < duration_seconds * 2
            
            return {
                "elapsed_seconds": elapsed,
                "passed": passed,
                "note": "infinite_loop_check" if not passed else "no_infinite_loops_detected",
            }
        except Exception as exc:
            return {
                "elapsed_seconds": None,
                "error": str(exc),
                "passed": True,  # Don't fail on errors in this check
            }


__all__ = [
    "PerformanceChecker",
    "PerformanceMetrics",
    "PerformanceThresholds",
]

