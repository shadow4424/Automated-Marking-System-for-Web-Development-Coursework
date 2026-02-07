from __future__ import annotations

from .config import (
    RUBRIC_VERSION,
    SCORING_MODE,
    ScoringMode,
    LLMProviderType,
    LLM_PROVIDER,
    LLM_DAILY_BUDGET_USD,
    LLM_CACHE_ENABLED,
    LLM_OPENAI_MODEL,
    LLM_SANITIZE_PII,
)
from .evidence_bundle import build_evidence_bundle, sanitize_pii, sanitize_student_files
# Redirect LLM imports to ams.llm package
from ams.llm import (
    LLMResponse,
    LLMProvider,
    MockProvider,
    OpenAIProvider,
    RequestCache,
    LocalLMStudioProvider,
)
from .models import Finding, RuleResult, Severity, SubmissionContext
from .profiles import (
    ProfileSpec,
    RequiredCSSRule,
    RequiredHTMLRule,
    RequiredJSRule,
    RequiredPHPRule,
    RequiredSQLRule,
    get_profile_spec,
    get_relevant_components,
)
from .scoring import ScoringEngine
from .pipeline import AssessmentPipeline

__all__ = [
    # Models
    "Finding",
    "RuleResult",
    "Severity",
    "SubmissionContext",
    # Profiles
    "ProfileSpec",
    "RequiredCSSRule",
    "RequiredHTMLRule",
    "RequiredJSRule",
    "RequiredPHPRule",
    "RequiredSQLRule",
    "get_profile_spec",
    "get_relevant_components",
    # Scoring
    "ScoringEngine",
    "AssessmentPipeline",
    # Config
    "ScoringMode",
    "SCORING_MODE",
    "RUBRIC_VERSION",
    "LLMProviderType",
    "LLM_PROVIDER",
    "LLM_DAILY_BUDGET_USD",
    "LLM_CACHE_ENABLED",
    "LLM_OPENAI_MODEL",
    "LLM_SANITIZE_PII",
    # Evidence
    "build_evidence_bundle",
    "sanitize_pii",
    "sanitize_student_files",
    # LLM (redirected from ams.llm)
    "LLMResponse",
    "LLMProvider",
    "MockProvider",
    "OpenAIProvider",
    "RequestCache",
    "LocalLMStudioProvider",
]
