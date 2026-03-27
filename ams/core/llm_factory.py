"""Factory for LLM Provider instantiation.

Provides a centralized function to get the correctly configured LLM provider
based on settings in `ams.core.config`. This avoids circular imports and
duplication across modules that need LLM access.
"""
from __future__ import annotations

from ams.core.config import LLM_PROVIDER, LLMProviderType, LLM_BASE_URL, LLM_MODEL_NAME, LLM_TIMEOUT
from ams.llm.providers import LLMProvider, LocalLMStudioProvider, OpenAIProvider, MockProvider


def get_llm_provider() -> LLMProvider:
    """Get an LLM provider instance based on current configuration.
    
    Returns:
        An instance of `LLMProvider` configured according to `config.py`.
    
    Raises:
        ValueError: If the configured provider type is not supported.
    """
    if LLM_PROVIDER == LLMProviderType.LOCAL:
        return LocalLMStudioProvider(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL_NAME,
            timeout=LLM_TIMEOUT,
        )
    elif LLM_PROVIDER == LLMProviderType.OPENAI:
        from ams.core.config import LLM_OPENAI_MODEL
        return OpenAIProvider(model=LLM_OPENAI_MODEL)
    elif LLM_PROVIDER == LLMProviderType.MOCK:
        return MockProvider()
    else:
        raise ValueError(f"Unsupported LLM provider type: {LLM_PROVIDER}")


__all__ = ["get_llm_provider"]
