"""SQLite-based Request Cache for LLM Responses.

Caches prompt/response pairs to speed up demo re-runs and reduce API calls.
Uses SHA256 hashing of prompts for cache keys.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class RequestCache:
    """SQLite-based cache for LLM requests.
    
    Hashes prompts and stores responses to avoid duplicate calls.
    """
    
    def __init__(self, db_path: str | Path | None = None):
        """Initialise the cache.
        
        Args:
            db_path: Path to SQLite database. Defaults to ams/cache.db
        """
        if db_path is None:
            # Default to ams/cache.db in the package directory
            db_path = Path(__file__).parent.parent / "cache.db"
        
        self._db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self):
        """Initialise the database schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    prompt_hash TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    system_prompt TEXT,
                    response TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at 
                ON llm_cache(created_at)
            """)
            conn.commit()
    
    @staticmethod
    def _hash_prompt(prompt: str, system_prompt: str, model: str) -> str:
        """Generate a unique hash for the request."""
        content = f"{model}|{system_prompt}|{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def get(self, prompt: str, system_prompt: str = "", model: str = "") -> dict | None:
        """Retrieve a cached response if available.
        
        Args:
            prompt: User prompt
            system_prompt: System prompt
            model: Model name
            
        Returns:
            Cached response dict or None if not found
        """
        prompt_hash = self._hash_prompt(prompt, system_prompt, model)
        
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                SELECT response, input_tokens, output_tokens, model 
                FROM llm_cache 
                WHERE prompt_hash = ?
                """,
                (prompt_hash,),
            )
            row = cursor.fetchone()
        
        if row:
            logger.debug(f"Cache HIT: {prompt_hash[:8]}...")
            return {
                "content": row[0],
                "input_tokens": row[1] or 0,
                "output_tokens": row[2] or 0,
                "model": row[3],
                "cached": True,
            }
        
        logger.debug(f"Cache MISS: {prompt_hash[:8]}...")
        return None
    
    def set(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        response: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ):
        """Store a response in the cache.
        
        Args:
            prompt: User prompt
            system_prompt: System prompt
            model: Model name
            response: Response content to cache
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        """
        prompt_hash = self._hash_prompt(prompt, system_prompt, model)
        
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO llm_cache 
                (prompt_hash, model, system_prompt, response, input_tokens, output_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt_hash,
                    model,
                    system_prompt,
                    response,
                    input_tokens,
                    output_tokens,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        
        logger.debug(f"Cached: {prompt_hash[:8]}...")
    
    def clear(self):
        """Clear all cached responses."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM llm_cache")
            conn.commit()
        logger.info("Cache cleared")
    
    def stats(self) -> dict:
        """Get cache statistics."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens) FROM llm_cache")
            row = cursor.fetchone()
            return {
                "entries": row[0] or 0,
                "total_input_tokens": row[1] or 0,
                "total_output_tokens": row[2] or 0,
            }


__all__ = ["RequestCache"]
