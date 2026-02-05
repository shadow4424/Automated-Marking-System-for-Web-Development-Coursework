"""Configuration for AMS including LLM integration settings (Phase 0)."""
from __future__ import annotations

from enum import Enum


class ScoringMode(str, Enum):
    """Scoring mode for the AMS marking pipeline.
    
    This controls how static assessors and LLM feedback interact.
    Currently defaults to STATIC_ONLY - no LLM integration yet.
    """
    STATIC_ONLY = "static_only"
    STATIC_PLUS_LLM = "static_plus_llm"
    LLM_FEEDBACK_ONLY = "llm_feedback_only"
    LLM_OVERRIDE = "llm_override"


class LLMProviderType(str, Enum):
    """Available LLM providers."""
    MOCK = "mock"
    LOCAL = "local"  # LM Studio / Ollama at localhost
    OPENAI = "openai"
    # Future: AZURE = "azure", ANTHROPIC = "anthropic"


# =============================================================================
# Scoring Configuration
# =============================================================================

# Default scoring mode - static assessors only, no LLM integration yet
SCORING_MODE = ScoringMode.STATIC_ONLY

# Rubric version for evidence bundle tracking
RUBRIC_VERSION = "1.0"


# =============================================================================
# LLM Configuration (Phase 0 - Local Edition)
# =============================================================================

# Which LLM provider to use (default to local for demo)
LLM_PROVIDER = LLMProviderType.LOCAL

# Local LM Studio settings
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_MODEL_NAME = "llama-3.2-3b-instruct"  # Model name as shown in LM Studio
LLM_TIMEOUT = 120  # Seconds to wait for response (3B can be slow)

# OpenAI settings (for cloud fallback)
LLM_OPENAI_MODEL = "gpt-4o-mini"

# Daily budget limit in USD (circuit breaker for cloud)
LLM_DAILY_BUDGET_USD = 1.00

# Whether to cache LLM requests (speeds up demo re-runs)
LLM_CACHE_ENABLED = True

# Whether to sanitize PII before sending to LLM
LLM_SANITIZE_PII = True


__all__ = [
    "ScoringMode",
    "SCORING_MODE",
    "RUBRIC_VERSION",
    "LLMProviderType",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL_NAME",
    "LLM_TIMEOUT",
    "LLM_OPENAI_MODEL",
    "LLM_DAILY_BUDGET_USD",
    "LLM_CACHE_ENABLED",
    "LLM_SANITIZE_PII",
]


