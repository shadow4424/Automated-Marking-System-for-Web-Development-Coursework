"""Configuration for AMS including LLM integration settings (Phase 0-3)."""
from __future__ import annotations

from enum import Enum


class ScoringMode(str, Enum):
    """Scoring mode for the AMS marking pipeline.
    
    This controls how static assessors and LLM feedback interact.
    Currently defaults to STATIC_ONLY - no LLM integration yet.
    """
    STATIC_ONLY = "static_only"
    STATIC_PLUS_LLM = "static_plus_llm"


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

# Default scoring weights for findings
FINDING_SCORE_PASS = 1.0
FINDING_SCORE_FAIL = 0.0
FINDING_SCORE_WARN = 0.5

# Pass threshold for overall assessment (40%)
PASS_THRESHOLD = 0.40


# =============================================================================
# LLM Configuration (Phase 0-3)
# =============================================================================

# Which LLM provider to use (default to local for demo)
LLM_PROVIDER = LLMProviderType.LOCAL

# Local LM Studio settings
LLM_BASE_URL = "http://localhost:1234/v1"
LLM_MODEL_NAME = "qwen2.5-vl-7b-instruct"  # Phase 3: Vision model for multimodal
LLM_TIMEOUT = 120  # Seconds to wait for response

# Phase 3: Vision Capabilities
VISION_ENABLED = True  # Enable multimodal image+text requests
VISION_MAX_IMAGE_SIZE = 1024  # Max dimension for image resizing (pixels)
VISION_MAX_TOKENS = 2048  # Min context window for vision responses
VISION_TIMEOUT = 180  # Seconds to wait for vision response (longer than text)

# OpenAI settings (for cloud fallback)
LLM_OPENAI_MODEL = "gpt-4o-mini"

# Daily budget limit in USD (circuit breaker for cloud)
LLM_DAILY_BUDGET_USD = 1.00

# Whether to cache LLM requests (speeds up demo re-runs)
LLM_CACHE_ENABLED = True

# Whether to sanitize PII before sending to LLM
LLM_SANITIZE_PII = True


# =============================================================================
# Path Configuration
# =============================================================================

from pathlib import Path as _Path

# Package root directory
PACKAGE_ROOT = _Path(__file__).parent.parent

# Default workspace root for runs
WORKSPACE_ROOT = PACKAGE_ROOT.parent / "ams_web_runs"

# Template and static directories
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

# Cache directory for LLM responses
CACHE_DIR = PACKAGE_ROOT / "cache"

# Maximum age for workspace cleanup (hours)
WORKSPACE_MAX_AGE_HOURS = 24


# =============================================================================
# Sandbox Configuration (imported from ams.sandbox.config for convenience)
# =============================================================================
# Sandbox behaviour is controlled entirely via environment variables:
#   AMS_SANDBOX_MODE=docker|subprocess   (default: docker)
#   AMS_SANDBOX_IMAGE=ams-sandbox:latest
#   AMS_SANDBOX_CPU_LIMIT=1.0
#   AMS_SANDBOX_MEMORY_LIMIT=512m
#   AMS_SANDBOX_PIDS_LIMIT=64
#   AMS_SANDBOX_NETWORK_MODE=none
# See ams.sandbox.config.SandboxConfig for full listing.


__all__ = [
    # Scoring
    "ScoringMode",
    "SCORING_MODE",
    "RUBRIC_VERSION",
    "FINDING_SCORE_PASS",
    "FINDING_SCORE_FAIL",
    "FINDING_SCORE_WARN",
    "PASS_THRESHOLD",
    # LLM
    "LLMProviderType",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_MODEL_NAME",
    "LLM_TIMEOUT",
    "LLM_OPENAI_MODEL",
    "LLM_DAILY_BUDGET_USD",
    "LLM_CACHE_ENABLED",
    "LLM_SANITIZE_PII",
    # Vision
    "VISION_ENABLED",
    "VISION_MAX_IMAGE_SIZE",
    "VISION_MAX_TOKENS",
    "VISION_TIMEOUT",
    # Paths
    "PACKAGE_ROOT",
    "WORKSPACE_ROOT",
    "TEMPLATE_DIR",
    "STATIC_DIR",
    "CACHE_DIR",
    "WORKSPACE_MAX_AGE_HOURS",
    # Sandbox (re-exported for convenience)
    "SANDBOX_DOCS",
]

# Short doc-string constant so other modules can reference the env-vars.
SANDBOX_DOCS = (
    "Set AMS_SANDBOX_MODE=docker to enable Docker sandboxing. "
    "See ams.sandbox.config for full configuration."
)
