#!/usr/bin/env python3
"""LLM Health Check Script for Demo Day.

This script verifies that LM Studio is running and responsive before
starting a batch assessment. Run this as a sanity check.

Usage:
    python -m ams.tools.check_llm
    python tools/check_llm.py
"""
from __future__ import annotations

import sys


def check_llm_health() -> bool:
    """Check if the local LLM is running and responsive.
    
    Returns:
        True if healthy, False otherwise
    """
    # Import here to avoid circular imports
    from ams.core.config import LLM_BASE_URL, LLM_MODEL_NAME, LLM_TIMEOUT
    from ams.llm.providers import LocalLMStudioProvider
    
    print("=" * 60)
    print("🔍 AMS LLM Health Check")
    print("=" * 60)
    print(f"   Target: {LLM_BASE_URL}")
    print(f"   Model:  {LLM_MODEL_NAME}")
    print(f"   Timeout: {LLM_TIMEOUT}s")
    print("-" * 60)
    
    provider = LocalLMStudioProvider(
        base_url=LLM_BASE_URL,
        model=LLM_MODEL_NAME,
        timeout=30,  # Quick timeout for health check
    )
    
    is_healthy, message = provider.health_check()
    
    if is_healthy:
        print(f"✅ {message}")
        print("-" * 60)
        
        # Test JSON mode
        print("🧪 Testing JSON generation...")
        response = provider.complete(
            prompt='Generate a JSON object with keys "status" and "message".',
            json_mode=True,
            max_tokens=100,
        )
        
        if response.success:
            print(f"✅ JSON Mode OK: {response.content[:100]}")
            print(f"   Latency: {response.latency_ms}ms")
            print(f"   Tokens: {response.total_tokens}")
        else:
            print(f"⚠️  JSON Mode Warning: {response.error}")
        
        print("=" * 60)
        print("🎉 LLM is ready for assessment!")
        print("=" * 60)
        return True
    else:
        print(f"❌ {message}")
        print("-" * 60)
        print("💡 Troubleshooting:")
        print("   1. Start LM Studio")
        print("   2. Load a model (e.g., Llama 3.2 3B Instruct)")
        print("   3. Click 'Start Server' in LM Studio")
        print(f"   4. Ensure server is at {LLM_BASE_URL}")
        print("=" * 60)
        return False


def main():
    """CLI entry point."""
    try:
        success = check_llm_health()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⚠️  Cancelled")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
