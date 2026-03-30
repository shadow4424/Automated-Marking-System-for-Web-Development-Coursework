"""LLM Provider Abstraction for Local and Cloud APIs."""
from __future__ import annotations

import base64
import logging
import mimetypes
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Optional PIL import for image resizing
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from ams.core.config import VISION_MAX_IMAGE_SIZE
from ams.llm.utils import encode_image_safely

logger = logging.getLogger(__name__)


# Response Dataclass


@dataclass
class LLMResponse:
    """Standardised response from any LLM provider."""
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


# Abstract Base Provider


class LLMProvider(ABC):
    """Abstract base class for LLM providers. Allows swapping between Local/OpenAI/Azure without refactoring."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
        image_path: str | None = None,
    ) -> LLMResponse:
        """Generate a completion from the LLM."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return model identifier."""
        pass

    def _clean_json_response(self, text: str) -> str:
        """Clean JSON response from chatty small models. Delegates to the shared utility in ams.llm.utils."""
        from ams.llm.utils import clean_json_response
        return clean_json_response(text)


# Local LM Studio Provider (Phase 3: Vision Support)


class LocalLMStudioProvider(LLMProvider):
    """Provider for LM Studio running locally."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "qwen2.5-vl-7b-instruct",
        timeout: int = 120,
    ):
        """Initialise local provider."""
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

    def _encode_image(
        self, image_path: str, max_size: int = VISION_MAX_IMAGE_SIZE,
    ) -> tuple[str, str]:
        """Encode an image file as a compressed JPEG Base64 string."""
        path = Path(image_path)

        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        if HAS_PIL:
            base64_string = encode_image_safely(
                str(path), max_size=max_size,
            )
            mime_type = "image/jpeg"
            logger.debug(
                "Encoded image (safe): %s -> JPEG, max_dim=%d",
                path.name, max_size,
            )
            return base64_string, mime_type

        # Fallback: PIL not available – send raw bytes (unchanged behaviour)
        logger.warning(
            "PIL not installed. Image will be sent at full resolution. "
            "Install with: pip install pillow"
        )
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type is None or not mime_type.startswith("image/"):
            mime_type = "image/png"

        with open(path, "rb") as f:
            image_data = f.read()

        base64_string = base64.b64encode(image_data).decode("utf-8")
        logger.debug(f"Encoded image (raw): {path.name} ({len(image_data)} bytes, {mime_type})")
        return base64_string, mime_type

    def health_check(self) -> tuple[bool, str]:
        """Check whether LM Studio is running and responsive. Returns: Tuple of (is_healthy, message)"""
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

    # Patterns in LM Studio / llama.cpp errors that indicate VRAM / slot
    # Exhaustion. These are retryable with a smaller image payload.
    _RETRYABLE_PATTERNS = (
        "memory slot",
        "failed to find a memory slot",
        "failed to process image",
        "channel error",
    )

    _MAX_ATTEMPTS = 3
    _BACKOFF_SCHEDULE = (0.5, 1.5)  # Seconds to sleep before attempt 2, 3
    _SHRINK_FACTOR = 0.8            # Reduce max_size by 20% each retry
    _MIN_IMAGE_SIZE = 320           # Never shrink below this

    def _is_retryable(self, error_msg: str) -> bool:
        """Return True if *error_msg* looks like a transient slot/OOM error."""
        lower = error_msg.lower()
        return any(p in lower for p in self._RETRYABLE_PATTERNS)

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
        image_path: str | None = None,
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

        # Prepare system prompt (once.
        effective_system_prompt = system_prompt
        if json_mode:
            json_instruction = (
                "You MUST respond with valid JSON only. "
                "Do NOT include any text before or after the JSON. "
                "Do NOT wrap the JSON in markdown code blocks. "
                "Do NOT say 'Here is the JSON' or similar."
            )
            if effective_system_prompt:
                effective_system_prompt = (
                    f"{effective_system_prompt}\n\n{json_instruction}"
                )
            else:
                effective_system_prompt = json_instruction

        # Retry loop (vision requests only.
        current_max_size = VISION_MAX_IMAGE_SIZE
        last_error: str = ""
        start_time = time.time()

        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            # Build a *fresh* messages list each attempt.
            messages: list[dict[str, Any]] = []

            if effective_system_prompt:
                messages.append(
                    {"role": "system", "content": effective_system_prompt}
                )

            if image_path is not None:
                try:
                    base64_image, mime_type = self._encode_image(
                        image_path, max_size=current_max_size,
                    )
                    user_content: list[dict[str, Any]] = [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{mime_type};base64,{base64_image}"
                                )
                            },
                        },
                    ]
                    messages.append(
                        {"role": "user", "content": user_content}
                    )
                    logger.debug(
                        "Sending multimodal request [attempt %d/%d] "
                        "image=%s max_size=%d",
                        attempt, self._MAX_ATTEMPTS,
                        image_path, current_max_size,
                    )
                except FileNotFoundError as e:
                    return LLMResponse(
                        content="",
                        model=self._model,
                        error=str(e),
                    )
            else:
                messages.append({"role": "user", "content": prompt})

            # Call the LLM.
            try:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                latency_ms = int((time.time() - start_time) * 1000)
                content = response.choices[0].message.content or ""

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
                    raw_response=(
                        response.model_dump()
                        if hasattr(response, "model_dump")
                        else {}
                    ),
                )

            except Exception as e:
                last_error = str(e)

                # Check for retryable slot/OOM error.
                if (
                    image_path is not None
                    and self._is_retryable(last_error)
                    and attempt < self._MAX_ATTEMPTS
                ):
                    backoff = self._BACKOFF_SCHEDULE[attempt - 1]
                    new_size = max(
                        self._MIN_IMAGE_SIZE,
                        int(current_max_size * self._SHRINK_FACTOR),
                    )
                    logger.warning(
                        "LM Studio slot/OOM error (attempt %d/%d): %s. "
                        "Retrying in %.1fs with max_size=%d",
                        attempt, self._MAX_ATTEMPTS,
                        last_error[:120], backoff, new_size,
                    )
                    time.sleep(backoff)
                    current_max_size = new_size
                    continue  # Retry with smaller image

                # Non-retryable or final attempt.
                break

        # All attempts exhausted or non-retryable error.
        if "Connection" in last_error or "refused" in last_error.lower():
            last_error = (
                f"Cannot connect to LM Studio at {self._base_url}. "
                "Please ensure LM Studio is running and the server is started."
            )

        logger.error("Local LLM error: %s", last_error)
        return LLMResponse(
            content="",
            model=self._model,
            latency_ms=int((time.time() - start_time) * 1000),
            error=last_error,
        )

    @property
    def model_name(self) -> str:
        return self._model


# OpenAI Cloud Provider


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
        image_path: str | None = None,
    ) -> LLMResponse:
        if image_path:
            return LLMResponse(
                content="", model=self._model,
                error="OpenAIProvider does not support vision/image_path",
            )
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


# Mock Provider for Testing


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
        image_path: str | None = None,
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
