from __future__ import annotations

from ams.core.config import LLM_PROVIDER, LLMProviderType, LLM_BASE_URL, LLM_MODEL_NAME, LLM_TIMEOUT
from ams.llm.providers import LLMProvider, LocalLMStudioProvider, OpenAIProvider, MockProvider

# Function to get the appropriate LLM provider.
def get_llm_provider() -> LLMProvider:
    # If the provider is local, return the LocalLMStudioProvider.
    if LLM_PROVIDER == LLMProviderType.LOCAL:
        return LocalLMStudioProvider(
            base_url=LLM_BASE_URL,
            model=LLM_MODEL_NAME,
            timeout=LLM_TIMEOUT,
        )
    # If the provider is OpenAI, return the OpenAIProvider.
    elif LLM_PROVIDER == LLMProviderType.OPENAI:
        from ams.core.config import LLM_OPENAI_MODEL
        return OpenAIProvider(model=LLM_OPENAI_MODEL)
    # If the provider is Mock, return the MockProvider (Testing purposes).
    elif LLM_PROVIDER == LLMProviderType.MOCK:
        return MockProvider()
    else:
        raise ValueError(f"Unsupported LLM provider type: {LLM_PROVIDER}")


__all__ = ["get_llm_provider"]
