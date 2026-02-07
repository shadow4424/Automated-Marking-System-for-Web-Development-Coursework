"""Vision Analysis Module for Screenshot-Based Grading.

This module provides the VisionAnalyst class which uses Vision-capable LLMs
(e.g., Qwen2-VL) to analyze screenshots and detect layout/visual issues.

Phase 3.2: Vision Analysis Logic
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ams.core.factory import get_llm_provider

logger = logging.getLogger(__name__)


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class VisionResult:
    """Result of a vision analysis check."""
    result: str  # "PASS" or "FAIL"
    reason: str
    confidence: float = 1.0
    raw_response: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "reason": self.reason,
            "confidence": self.confidence,
        }


# =============================================================================
# Vision Analyst
# =============================================================================

class VisionAnalyst:
    """High-level interface for visual grading using Vision LLMs.
    
    This class provides methods to analyze screenshots against
    specific requirements and return structured feedback.
    
    Example:
        >>> analyst = VisionAnalyst()
        >>> result = analyst.detect_layout_issues(
        ...     "screenshot.png",
        ...     "The page should have a blue header"
        ... )
        >>> print(result["result"])  # "PASS" or "FAIL"
    """
    
    SYSTEM_PROMPT = """You are a UI/UX QA expert. Your job is to analyze screenshots of web pages and determine if they meet specific visual requirements.

Be strict and objective in your analysis. Focus on what is actually visible in the screenshot, not assumptions.

Always respond with valid JSON in this exact format:
{"result": "PASS" or "FAIL", "reason": "Brief explanation of your assessment"}

Do not include any text outside the JSON block."""

    def __init__(self, provider=None):
        """Initialize the VisionAnalyst.
        
        Args:
            provider: Optional LLMProvider instance. If None, uses factory.
        """
        self._provider = provider
    
    @property
    def provider(self):
        """Lazy-load the provider to avoid import issues."""
        if self._provider is None:
            self._provider = get_llm_provider()
        return self._provider
    
    def detect_layout_issues(
        self, 
        screenshot_path: str, 
        requirement_context: str
    ) -> dict:
        """Analyze a screenshot against a specific visual requirement.
        
        Args:
            screenshot_path: Path to the screenshot image file.
            requirement_context: The requirement to check against.
            
        Returns:
            Dict with keys: "result" (str), "reason" (str)
            
        Raises:
            FileNotFoundError: If the screenshot doesn't exist.
            ValueError: If the LLM response cannot be parsed.
        """
        path = Path(screenshot_path)
        if not path.exists():
            raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")
        
        user_prompt = f"""Requirement: {requirement_context}

Analyze the provided screenshot and determine if it meets this requirement.

Respond with JSON: {{"result": "PASS" or "FAIL", "reason": "..."}}"""

        logger.info(f"Analyzing screenshot: {path.name}")
        logger.debug(f"Requirement: {requirement_context}")
        
        response = self.provider.complete(
            prompt=user_prompt,
            system_prompt=self.SYSTEM_PROMPT,
            image_path=str(path),
            json_mode=True,
        )
        
        if response.error:
            logger.error(f"Vision analysis failed: {response.error}")
            return {
                "result": "ERROR",
                "reason": f"LLM error: {response.error}",
            }
        
        # Parse the JSON response
        try:
            result = self._parse_response(response.content)
            logger.info(f"Vision result: {result['result']} - {result['reason']}")
            return result
        except ValueError as e:
            logger.error(f"Failed to parse vision response: {e}")
            return {
                "result": "ERROR",
                "reason": f"Parse error: {e}",
                "raw_response": response.content,
            }
    
    def check_responsiveness(
        self,
        desktop_screenshot: str,
        mobile_screenshot: str,
    ) -> dict:
        """Compare desktop and mobile screenshots for responsive design.
        
        Args:
            desktop_screenshot: Path to desktop viewport screenshot.
            mobile_screenshot: Path to mobile viewport screenshot.
            
        Returns:
            Dict with responsiveness assessment.
        """
        # For now, analyze mobile screenshot with responsiveness requirement
        return self.detect_layout_issues(
            mobile_screenshot,
            "The page should be responsive and readable on mobile. "
            "Content should not overflow, text should be legible, "
            "and navigation should be accessible."
        )
    
    def _parse_response(self, content: str) -> dict:
        """Parse the LLM response into a structured result.
        
        Args:
            content: Raw LLM response string.
            
        Returns:
            Dict with "result" and "reason" keys.
            
        Raises:
            ValueError: If parsing fails.
        """
        content = content.strip()
        
        # Try direct JSON parse
        try:
            data = json.loads(content)
            if "result" in data and "reason" in data:
                return {
                    "result": data["result"].upper(),
                    "reason": data["reason"],
                }
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code block
        if "```" in content:
            import re
            json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(1).strip())
                    if "result" in data and "reason" in data:
                        return {
                            "result": data["result"].upper(),
                            "reason": data["reason"],
                        }
                except json.JSONDecodeError:
                    pass
        
        raise ValueError(f"Could not parse response: {content[:200]}")


# =============================================================================
# Module Export
# =============================================================================

__all__ = ["VisionAnalyst", "VisionResult"]


# =============================================================================
# Demo / Standalone Test
# =============================================================================

if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    
    if len(sys.argv) < 2:
        print("Usage: python -m ams.llm.vision <screenshot.png> [requirement]")
        print("Example: python -m ams.llm.vision page.png 'Header should be blue'")
        sys.exit(1)
    
    screenshot = sys.argv[1]
    requirement = sys.argv[2] if len(sys.argv) > 2 else "The page should be visually correct and well-designed"
    
    analyst = VisionAnalyst()
    result = analyst.detect_layout_issues(screenshot, requirement)
    
    print(f"\nResult: {result['result']}")
    print(f"Reason: {result['reason']}")
