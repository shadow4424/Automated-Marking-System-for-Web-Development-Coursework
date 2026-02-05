from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ErrorEvidence:
    """Structured evidence for detected errors."""
    error_type: str  # "javascript", "php", "network", "console"
    error_message: str
    error_source: Optional[str] = None  # File, URL, etc.
    stack_trace: Optional[str] = None
    severity: str = "error"  # "error", "warning", "notice"
    context: Dict[str, Any] = None


class ErrorDetector:
    """Detects and categorizes errors during browser testing."""
    
    def __init__(self):
        self.detected_errors: List[ErrorEvidence] = []
    
    def detect_javascript_errors(self, page: Any, console_errors: List[str]) -> List[ErrorEvidence]:
        """Detect JavaScript runtime errors."""
        errors: List[ErrorEvidence] = []
        
        for console_error in console_errors:
            # Categorize error
            error_type = "javascript"
            severity = "error"
            
            # Check for specific error patterns
            if "Uncaught" in console_error or "Error:" in console_error:
                severity = "error"
            elif "Warning:" in console_error:
                severity = "warning"
            
            errors.append(ErrorEvidence(
                error_type=error_type,
                error_message=console_error,
                severity=severity,
                context={"source": "console"},
            ))
        
        # Also check for uncaught exceptions via page evaluation
        try:
            page_errors = page.evaluate("""
                () => {
                    if (window.errors && window.errors.length > 0) {
                        return window.errors;
                    }
                    return [];
                }
            """)
            
            for page_error in page_errors:
                errors.append(ErrorEvidence(
                    error_type="javascript",
                    error_message=str(page_error),
                    severity="error",
                    context={"source": "page_errors"},
                ))
        except Exception:
            pass  # Ignore errors in error detection
        
        self.detected_errors.extend(errors)
        return errors
    
    def detect_network_errors(self, network_errors: List[str], page: Any) -> List[ErrorEvidence]:
        """Detect network-related errors."""
        errors: List[ErrorEvidence] = []
        
        for network_error in network_errors:
            error_type = "network"
            severity = "error"
            
            # Categorize by error type
            if "404" in network_error:
                severity = "error"
                error_type = "network_404"
            elif "500" in network_error:
                severity = "error"
                error_type = "network_500"
            elif "timeout" in network_error.lower():
                severity = "warning"
                error_type = "network_timeout"
            elif "CORS" in network_error or "cors" in network_error.lower():
                severity = "error"
                error_type = "network_cors"
            
            # Extract URL from error message
            url = None
            if "http" in network_error:
                try:
                    url = network_error.split("http")[1].split()[0] if "http" in network_error else None
                    if url:
                        url = "http" + url
                except Exception:
                    pass
            
            errors.append(ErrorEvidence(
                error_type=error_type,
                error_message=network_error,
                error_source=url,
                severity=severity,
                context={"source": "network"},
            ))
        
        # Check for missing assets
        try:
            missing_assets = page.evaluate("""
                () => {
                    const resources = performance.getEntriesByType('resource');
                    const failed = resources.filter(r => 
                        r.transferSize === 0 && 
                        (r.name.includes('.css') || r.name.includes('.js') || r.name.includes('.png') || r.name.includes('.jpg'))
                    );
                    return failed.map(r => r.name);
                }
            """)
            
            for asset in missing_assets:
                errors.append(ErrorEvidence(
                    error_type="network",
                    error_message=f"Missing or failed to load asset: {asset}",
                    error_source=asset,
                    severity="warning",
                    context={"source": "missing_asset"},
                ))
        except Exception:
            pass
        
        self.detected_errors.extend(errors)
        return errors
    
    def detect_php_errors(self, page: Any, response: Any = None) -> List[ErrorEvidence]:
        """Detect PHP errors from server responses."""
        errors: List[ErrorEvidence] = []
        
        try:
            # Check page content for PHP error messages
            content = page.content()
            
            php_error_patterns = [
                ("Fatal error", "error"),
                ("Parse error", "error"),
                ("Warning:", "warning"),
                ("Notice:", "notice"),
                ("Deprecated:", "warning"),
            ]
            
            for pattern, severity in php_error_patterns:
                if pattern in content:
                    # Extract error message (simplified)
                    error_lines = [line for line in content.split("\n") if pattern in line]
                    for error_line in error_lines[:5]:  # Limit to first 5
                        errors.append(ErrorEvidence(
                            error_type="php",
                            error_message=error_line[:500],  # Limit length
                            severity=severity,
                            context={"source": "page_content", "pattern": pattern},
                        ))
        except Exception:
            pass
        
        # Check response headers for PHP errors
        if response:
            try:
                status = response.status
                if status >= 500:
                    errors.append(ErrorEvidence(
                        error_type="php",
                        error_message=f"Server error: HTTP {status}",
                        severity="error",
                        context={"source": "http_status", "status": status},
                    ))
            except Exception:
                pass
        
        self.detected_errors.extend(errors)
        return errors
    
    def detect_form_submission_errors(self, page: Any, form_selector: str = "form") -> List[ErrorEvidence]:
        """Detect errors during form submission."""
        errors: List[ErrorEvidence] = []
        
        try:
            form = page.query_selector(form_selector)
            if not form:
                return errors
            
            # Check for validation errors
            invalid_inputs = page.query_selector_all("input:invalid, textarea:invalid, select:invalid")
            for invalid_input in invalid_inputs:
                validation_message = invalid_input.evaluate("el => el.validationMessage")
                if validation_message:
                    errors.append(ErrorEvidence(
                        error_type="validation",
                        error_message=f"Validation error: {validation_message}",
                        error_source=invalid_input.evaluate("el => el.name || el.id"),
                        severity="warning",
                        context={"source": "form_validation"},
                    ))
        except Exception as exc:
            errors.append(ErrorEvidence(
                error_type="form",
                error_message=f"Error checking form: {str(exc)}",
                severity="error",
                context={"source": "form_check_error"},
            ))
        
        self.detected_errors.extend(errors)
        return errors
    
    def get_all_errors(self) -> List[ErrorEvidence]:
        """Get all detected errors."""
        return self.detected_errors.copy()
    
    def get_errors_by_type(self, error_type: str) -> List[ErrorEvidence]:
        """Get errors filtered by type."""
        return [e for e in self.detected_errors if e.error_type == error_type or e.error_type.startswith(error_type)]
    
    def get_critical_errors(self) -> List[ErrorEvidence]:
        """Get only critical errors (severity == 'error')."""
        return [e for e in self.detected_errors if e.severity == "error"]
    
    def has_critical_errors(self) -> bool:
        """Check if any critical errors were detected."""
        return len(self.get_critical_errors()) > 0


__all__ = [
    "ErrorDetector",
    "ErrorEvidence",
]

