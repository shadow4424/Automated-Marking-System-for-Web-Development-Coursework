"""LLM Provider Abstraction for Local and Cloud APIs.

This module provides a unified interface for different LLM backends:
- LocalLMStudioProvider: For LM Studio at localhost:1234
- OpenAIProvider: For OpenAI cloud API
- MockProvider: For testing without a server

Key Feature: JSON repair for "chatty" small models (Llama 3.2 3B).
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Response Dataclass
# =============================================================================


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached: bool = False
    latency_ms: int = 0
    raw_response: dict = field(default_factory=dict)
    error: str | None = None
    
    @property
    def success(self) -> bool:
        """True if the response contains valid content."""
        return self.error is None and bool(self.content)
    
    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached": self.cached,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


# =============================================================================
# Abstract Base Provider
# =============================================================================


class LLMProvider(ABC):
    """Abstract base class for LLM providers.
    
    Allows swapping between Local/OpenAI/Azure without refactoring.
    """
    
    @abstractmethod
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Generate a completion from the LLM."""
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier."""
        pass
    
    def _clean_json_response(self, text: str) -> str:
        """Clean JSON response from chatty small models.
        
        Llama 3.2 3B often wraps JSON in markdown or adds preambles like
        "Here is the JSON:" before the actual content. This method strips:
        - Markdown code fences (```json ... ```)
        - Common preambles before JSON
        - Trailing text after JSON
        
        Args:
            text: Raw LLM output that may contain wrapped JSON
            
        Returns:
            Clean JSON string ready for parsing
        """
        if not text:
            return text
        
        original = text
        
        # Pattern 1: Strip markdown code fences
        # Matches ```json\n{...}\n``` or ```{...}```
        fence_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
        match = re.search(fence_pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            logger.debug("Stripped markdown fences from LLM response")
        
        # Pattern 2: Find JSON object/array in text
        # Look for the first { or [ and find its matching closing bracket
        json_start = None
        for i, char in enumerate(text):
            if char in "{[":
                json_start = i
                break
        
        if json_start is not None:
            # Find the matching closing bracket
            bracket_map = {"{": "}", "[": "]"}
            open_bracket = text[json_start]
            close_bracket = bracket_map[open_bracket]
            depth = 0
            json_end = None
            
            for i in range(json_start, len(text)):
                if text[i] == open_bracket:
                    depth += 1
                elif text[i] == close_bracket:
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break
            
            if json_end:
                text = text[json_start:json_end]
        
        # Validate it's actually JSON
        try:
            json.loads(text)
            if text != original:
                logger.debug(f"Cleaned JSON: removed {len(original) - len(text)} chars")
            return text
        except json.JSONDecodeError:
            # Return original if cleaning broke something
            logger.warning("JSON cleaning failed, returning original")
            return original


# =============================================================================
# Local LM Studio Provider
# =============================================================================


class LocalLMStudioProvider(LLMProvider):
    """Provider for LM Studio running locally.
    
    Connects to http://localhost:1234/v1 using the OpenAI SDK.
    Optimized for Llama 3.2 3B Instruct.
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "llama-3.2-3b-instruct",
        timeout: int = 120,
    ):
        """Initialize local provider.
        
        Args:
            base_url: LM Studio API endpoint
            model: Model name as shown in LM Studio
            timeout: Request timeout in seconds
        """
        self._base_url = base_url
        self._model = model
        self._timeout = timeout
        self._client = None
    
    def _get_client(self):
        """Lazy-load the OpenAI client configured for local use."""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    base_url=self._base_url,
                    api_key="lm-studio",  # LM Studio doesn't need a real key
                    timeout=self._timeout,
                )
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client
    
    def health_check(self) -> tuple[bool, str]:
        """Check if LM Studio is running and responsive.
        
        Returns:
            Tuple of (is_healthy, message)
        """
        try:
            client = self._get_client()
            # Simple test completion
            response = client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "Reply with OK"}],
                max_tokens=10,
                temperature=0.0,
            )
            content = response.choices[0].message.content or ""
            return True, f"LM Studio OK: {content[:50]}"
        except Exception as e:
            return False, f"LM Studio Error: {e}"
    
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Generate a completion from the local LLM."""
        try:
            client = self._get_client()
        except ImportError as e:
            return LLMResponse(
                content="",
                model=self._model,
                error=str(e),
            )
        
        messages: list[dict[str, str]] = []
        
        # Build system prompt for JSON mode
        if json_mode:
            json_instruction = (
                "You MUST respond with valid JSON only. "
                "Do NOT include any text before or after the JSON. "
                "Do NOT wrap the JSON in markdown code blocks. "
                "Do NOT say 'Here is the JSON' or similar."
            )
            if system_prompt:
                system_prompt = f"{system_prompt}\n\n{json_instruction}"
            else:
                system_prompt = json_instruction
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        start_time = time.time()
        
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.time() - start_time) * 1000)
            
            content = response.choices[0].message.content or ""
            
            # Clean JSON if requested
            if json_mode:
                content = self._clean_json_response(content)
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                model=self._model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                cached=False,
                latency_ms=latency_ms,
                raw_response=response.model_dump() if hasattr(response, "model_dump") else {},
            )
            
        except Exception as e:
            error_msg = str(e)
            if "Connection" in error_msg or "refused" in error_msg.lower():
                error_msg = (
                    f"Cannot connect to LM Studio at {self._base_url}. "
                    "Please ensure LM Studio is running and the server is started."
                )
            
            logger.error(f"Local LLM error: {error_msg}")
            return LLMResponse(
                content="",
                model=self._model,
                latency_ms=int((time.time() - start_time) * 1000),
                error=error_msg,
            )
    
    @property
    def model_name(self) -> str:
        return self._model


# =============================================================================
# OpenAI Cloud Provider
# =============================================================================


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider for cloud fallback."""
    
    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        import os
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None
    
    def _get_client(self):
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self._api_key)
            except ImportError:
                raise ImportError("openai package not installed")
        return self._client
    
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        try:
            client = self._get_client()
        except ImportError as e:
            return LLMResponse(content="", model=self._model, error=str(e))
        
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        
        start_time = time.time()
        
        try:
            response = client.chat.completions.create(**kwargs)
            latency_ms = int((time.time() - start_time) * 1000)
            usage = response.usage
            
            return LLMResponse(
                content=response.choices[0].message.content or "",
                model=self._model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                cached=False,
                latency_ms=latency_ms,
            )
        except Exception as e:
            return LLMResponse(
                content="",
                model=self._model,
                latency_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )
    
    @property
    def model_name(self) -> str:
        return self._model


# =============================================================================
# Mock Provider for Testing
# =============================================================================


class MockProvider(LLMProvider):
    """Mock provider for testing without any server."""
    
    def __init__(self, responses: dict[str, str] | None = None):
        self._responses = responses or {}
        self._call_count = 0
    
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        self._call_count += 1
        
        # Check for canned responses
        for key, response in self._responses.items():
            if key in prompt:
                return LLMResponse(
                    content=response,
                    model="mock",
                    input_tokens=len(prompt) // 4,
                    output_tokens=len(response) // 4,
                )
        
        # Default mock response
        if json_mode:
            content = '{"feedback": "Mock feedback", "score": 0.8}'
        else:
            content = f"Mock response #{self._call_count}"
        
        return LLMResponse(
            content=content,
            model="mock",
            input_tokens=len(prompt) // 4,
            output_tokens=len(content) // 4,
        )
    
    @property
    def model_name(self) -> str:
        return "mock"
    
    @property
    def call_count(self) -> int:
        return self._call_count


__all__ = [
    "LLMResponse",
    "LLMProvider",
    "LocalLMStudioProvider",
    "OpenAIProvider",
    "MockProvider",
]
