"""LLM Core Infrastructure for Phase 0.5.

This module provides the foundational components for LLM integration:
- Abstract LLMProvider interface
- Mock and OpenAI provider implementations
- SQLite-based request caching
- Budget guard circuit breaker
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# LLM Provider Abstraction
# ============================================================================


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached: bool = False
    cost_usd: float = 0.0
    latency_ms: int = 0
    raw_response: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached": self.cached,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }


class LLMProvider(ABC):
    """Abstract base class for LLM providers.
    
    This allows swapping between OpenAI, Azure, Anthropic, or local models
    without changing the rest of the codebase.
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
        """Generate a completion from the LLM.
        
        Args:
            prompt: The user prompt/question
            system_prompt: System instructions for the model
            temperature: Randomness (0.0 = deterministic)
            max_tokens: Maximum response length
            json_mode: If True, force JSON output format
            
        Returns:
            LLMResponse with the completion and metadata
        """
        pass
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier."""
        pass
    
    @property
    @abstractmethod
    def cost_per_1k_input_tokens(self) -> float:
        """Cost in USD per 1000 input tokens."""
        pass
    
    @property
    @abstractmethod
    def cost_per_1k_output_tokens(self) -> float:
        """Cost in USD per 1000 output tokens."""
        pass


class MockLLMProvider(LLMProvider):
    """Mock provider for testing without API calls.
    
    Returns predefined responses or echos the prompt.
    """
    
    def __init__(self, responses: dict[str, str] | None = None):
        """Initialize with optional canned responses.
        
        Args:
            responses: Dict mapping prompt substrings to responses
        """
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
        
        # Check for matching canned response
        for key, response in self._responses.items():
            if key in prompt:
                return LLMResponse(
                    content=response,
                    model="mock-model",
                    input_tokens=len(prompt) // 4,
                    output_tokens=len(response) // 4,
                    total_tokens=(len(prompt) + len(response)) // 4,
                    cached=False,
                    cost_usd=0.0,
                    latency_ms=10,
                )
        
        # Default: return a mock JSON response if json_mode
        if json_mode:
            content = '{"feedback": "Mock feedback for testing", "score_adjustment": 0.0}'
        else:
            content = f"Mock response to: {prompt[:100]}..."
        
        return LLMResponse(
            content=content,
            model="mock-model",
            input_tokens=len(prompt) // 4,
            output_tokens=len(content) // 4,
            total_tokens=(len(prompt) + len(content)) // 4,
            cached=False,
            cost_usd=0.0,
            latency_ms=10,
        )
    
    @property
    def model_name(self) -> str:
        return "mock-model"
    
    @property
    def cost_per_1k_input_tokens(self) -> float:
        return 0.0
    
    @property
    def cost_per_1k_output_tokens(self) -> float:
        return 0.0
    
    @property
    def call_count(self) -> int:
        return self._call_count


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider implementation.
    
    Requires OPENAI_API_KEY environment variable.
    """
    
    # Pricing as of 2024 (will need updates)
    _PRICING = {
        "gpt-4o": {"input": 0.0025, "output": 0.01},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    }
    
    def __init__(self, model: str = "gpt-4o-mini", api_key: str | None = None):
        """Initialize OpenAI provider.
        
        Args:
            model: Model identifier (e.g., "gpt-4o-mini")
            api_key: Optional API key (defaults to OPENAI_API_KEY env var)
        """
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None
    
    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self._api_key)
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai")
        return self._client
    
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        client = self._get_client()
        
        messages = []
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
        response = client.chat.completions.create(**kwargs)
        latency_ms = int((time.time() - start_time) * 1000)
        
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0
        
        # Calculate cost
        pricing = self._PRICING.get(self._model, {"input": 0.01, "output": 0.03})
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1000
        
        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached=False,
            cost_usd=cost,
            latency_ms=latency_ms,
            raw_response=response.model_dump(),
        )
    
    @property
    def model_name(self) -> str:
        return self._model
    
    @property
    def cost_per_1k_input_tokens(self) -> float:
        return self._PRICING.get(self._model, {"input": 0.01})["input"]
    
    @property
    def cost_per_1k_output_tokens(self) -> float:
        return self._PRICING.get(self._model, {"output": 0.03})["output"]


# ============================================================================
# Request Caching
# ============================================================================


class RequestCache:
    """SQLite-based cache for LLM requests.
    
    Hashes prompts and stores responses to avoid duplicate API calls.
    """
    
    def __init__(self, db_path: str | Path | None = None):
        """Initialize the cache.
        
        Args:
            db_path: Path to SQLite database. Defaults to ~/.ams/llm_cache.db
        """
        if db_path is None:
            cache_dir = Path.home() / ".ams"
            cache_dir.mkdir(exist_ok=True)
            db_path = cache_dir / "llm_cache.db"
        
        self._db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    prompt_hash TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    response TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()
    
    @staticmethod
    def _hash_prompt(prompt: str, system_prompt: str, model: str) -> str:
        """Generate a unique hash for the request."""
        content = f"{model}:{system_prompt}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get(self, prompt: str, system_prompt: str, model: str) -> LLMResponse | None:
        """Retrieve a cached response if available.
        
        Args:
            prompt: User prompt
            system_prompt: System prompt
            model: Model name
            
        Returns:
            Cached LLMResponse or None if not found
        """
        prompt_hash = self._hash_prompt(prompt, system_prompt, model)
        
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT response, input_tokens, output_tokens FROM cache WHERE prompt_hash = ?",
                (prompt_hash,),
            )
            row = cursor.fetchone()
        
        if row:
            logger.debug(f"Cache hit for prompt hash: {prompt_hash[:8]}...")
            return LLMResponse(
                content=row[0],
                model=model,
                input_tokens=row[1] or 0,
                output_tokens=row[2] or 0,
                total_tokens=(row[1] or 0) + (row[2] or 0),
                cached=True,
                cost_usd=0.0,
                latency_ms=0,
            )
        
        return None
    
    def set(self, prompt: str, system_prompt: str, response: LLMResponse):
        """Store a response in the cache.
        
        Args:
            prompt: User prompt
            system_prompt: System prompt
            response: LLM response to cache
        """
        prompt_hash = self._hash_prompt(prompt, system_prompt, response.model)
        
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache 
                (prompt_hash, model, response, input_tokens, output_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_hash,
                    response.model,
                    response.content,
                    response.input_tokens,
                    response.output_tokens,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        
        logger.debug(f"Cached response for prompt hash: {prompt_hash[:8]}...")
    
    def clear(self):
        """Clear all cached responses."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()


# ============================================================================
# Budget Guard (Circuit Breaker)
# ============================================================================


class BudgetExceededError(Exception):
    """Raised when the daily LLM budget has been exceeded."""
    pass


class BudgetGuard:
    """Circuit breaker to prevent runaway LLM costs.
    
    Tracks daily spend and blocks requests when the limit is reached.
    Defaults to DENY if the limit is exceeded.
    """
    
    def __init__(
        self,
        daily_limit_usd: float = 1.0,
        db_path: str | Path | None = None,
    ):
        """Initialize the budget guard.
        
        Args:
            daily_limit_usd: Maximum daily spend in USD
            db_path: Path to SQLite database. Defaults to ~/.ams/llm_budget.db
        """
        self._daily_limit = daily_limit_usd
        
        if db_path is None:
            cache_dir = Path.home() / ".ams"
            cache_dir.mkdir(exist_ok=True)
            db_path = cache_dir / "llm_budget.db"
        
        self._db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spend (
                    date TEXT PRIMARY KEY,
                    total_usd REAL NOT NULL DEFAULT 0.0,
                    request_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
    
    def _today(self) -> str:
        """Get today's date as ISO string."""
        return date.today().isoformat()
    
    def get_daily_spend(self) -> float:
        """Get total spend for today in USD."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT total_usd FROM spend WHERE date = ?",
                (self._today(),),
            )
            row = cursor.fetchone()
            return row[0] if row else 0.0
    
    def get_remaining_budget(self) -> float:
        """Get remaining budget for today in USD."""
        return max(0.0, self._daily_limit - self.get_daily_spend())
    
    def can_spend(self, estimated_cost: float = 0.0) -> bool:
        """Check if we have budget for a request.
        
        Args:
            estimated_cost: Estimated cost of the request
            
        Returns:
            True if within budget, False otherwise
        """
        return (self.get_daily_spend() + estimated_cost) <= self._daily_limit
    
    def check_budget(self, estimated_cost: float = 0.0):
        """Check budget and raise if exceeded.
        
        Args:
            estimated_cost: Estimated cost of the request
            
        Raises:
            BudgetExceededError: If daily limit would be exceeded
        """
        if not self.can_spend(estimated_cost):
            raise BudgetExceededError(
                f"Daily LLM budget exceeded. "
                f"Limit: ${self._daily_limit:.2f}, "
                f"Spent: ${self.get_daily_spend():.2f}, "
                f"Requested: ${estimated_cost:.2f}"
            )
    
    def record_spend(self, amount_usd: float):
        """Record a spend against today's budget.
        
        Args:
            amount_usd: Amount spent in USD
        """
        today = self._today()
        
        with sqlite3.connect(self._db_path) as conn:
            # Try to update existing row
            cursor = conn.execute(
                "UPDATE spend SET total_usd = total_usd + ?, request_count = request_count + 1 WHERE date = ?",
                (amount_usd, today),
            )
            
            # If no row updated, insert new one
            if cursor.rowcount == 0:
                conn.execute(
                    "INSERT INTO spend (date, total_usd, request_count) VALUES (?, ?, 1)",
                    (today, amount_usd),
                )
            
            conn.commit()
        
        logger.info(f"Recorded LLM spend: ${amount_usd:.4f}. Daily total: ${self.get_daily_spend():.4f}")
    
    def reset_daily(self):
        """Reset today's spend (for testing)."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM spend WHERE date = ?", (self._today(),))
            conn.commit()


# ============================================================================
# Cached Provider Wrapper
# ============================================================================


class CachedLLMProvider(LLMProvider):
    """Wrapper that adds caching and budget control to any provider."""
    
    def __init__(
        self,
        provider: LLMProvider,
        cache: RequestCache | None = None,
        budget_guard: BudgetGuard | None = None,
        cache_enabled: bool = True,
    ):
        """Initialize the cached provider.
        
        Args:
            provider: Underlying LLM provider
            cache: Request cache (creates default if None)
            budget_guard: Budget guard (creates default if None)
            cache_enabled: Whether caching is enabled
        """
        self._provider = provider
        self._cache = cache or RequestCache()
        self._budget_guard = budget_guard or BudgetGuard()
        self._cache_enabled = cache_enabled
    
    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        # Check cache first
        if self._cache_enabled:
            cached = self._cache.get(prompt, system_prompt, self._provider.model_name)
            if cached:
                return cached
        
        # Estimate cost and check budget
        estimated_tokens = len(prompt) // 4 + max_tokens
        estimated_cost = (
            estimated_tokens * self._provider.cost_per_1k_input_tokens / 1000
        )
        self._budget_guard.check_budget(estimated_cost)
        
        # Make the actual request
        response = self._provider.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        
        # Record actual cost
        self._budget_guard.record_spend(response.cost_usd)
        
        # Cache the response
        if self._cache_enabled:
            self._cache.set(prompt, system_prompt, response)
        
        return response
    
    @property
    def model_name(self) -> str:
        return self._provider.model_name
    
    @property
    def cost_per_1k_input_tokens(self) -> float:
        return self._provider.cost_per_1k_input_tokens
    
    @property
    def cost_per_1k_output_tokens(self) -> float:
        return self._provider.cost_per_1k_output_tokens


__all__ = [
    "LLMResponse",
    "LLMProvider",
    "MockLLMProvider",
    "OpenAIProvider",
    "RequestCache",
    "BudgetGuard",
    "BudgetExceededError",
    "CachedLLMProvider",
]
