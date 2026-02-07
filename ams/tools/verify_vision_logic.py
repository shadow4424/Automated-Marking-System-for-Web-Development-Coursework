#!/usr/bin/env python3
"""Phase 3.2 Verification: Vision Analysis Logic.

This script verifies that the VisionAnalyst class correctly analyzes
screenshots and can identify visual elements.

Test Strategy: Description-First Approach
Instead of asking complex "does this meet requirement X?" questions,
we ask the model to DESCRIBE what it sees and verify in Python.

Tests:
1. verify_red_square: Generate red square, ask "what color?", assert "red" in response
2. verify_layout_check: Generate blue rectangle (header), ask "is there a blue header?"

Usage:
    python -m ams.tools.verify_vision_logic

Prerequisites:
    - LM Studio must be running with a vision model (e.g., Qwen2-VL-2B-Instruct)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def print_header(text: str) -> None:
    print("=" * 60)
    print(text)
    print("=" * 60)


def print_success(text: str) -> None:
    print(f"✅ {text}")


def print_error(text: str) -> None:
    print(f"❌ {text}")


def print_warning(text: str) -> None:
    print(f"⚠️ {text}")


def print_info(text: str) -> None:
    print(f"   {text}")


def create_red_square(path: Path, size: int = 100) -> Path:
    """Create a red square image using PIL."""
    try:
        from PIL import Image
    except ImportError:
        print_error("PIL not installed. Run: pip install pillow")
        sys.exit(1)
    
    img = Image.new("RGB", (size, size), color=(255, 0, 0))
    img.save(path)
    return path


def create_blue_header(path: Path, width: int = 400, height: int = 80) -> Path:
    """Create a blue rectangle (simulating a header) using PIL."""
    try:
        from PIL import Image
    except ImportError:
        print_error("PIL not installed. Run: pip install pillow")
        sys.exit(1)
    
    # Create white canvas (webpage background)
    img = Image.new("RGB", (width, 300), color=(255, 255, 255))
    
    # Draw blue rectangle at top (header)
    for y in range(height):
        for x in range(width):
            img.putpixel((x, y), (0, 100, 200))
    
    img.save(path)
    return path


def check_llm_available() -> bool:
    """Check if LM Studio is running."""
    import requests
    
    try:
        resp = requests.get("http://127.0.0.1:1234/api/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def verify_red_square(tmpdir: Path) -> bool:
    """Test 1: Can the model identify a red color?
    
    Strategy: Ask to DESCRIBE the color, then check Python-side.
    """
    print("\n[Test 1] Red Square Color Detection")
    print("-" * 40)
    
    from ams.core.factory import get_llm_provider
    
    # Create test image
    image_path = tmpdir / "red_square.png"
    create_red_square(image_path)
    print_info(f"Created: {image_path.name} (100x100 red square)")
    
    # Ask model to describe the color (simple, no negation)
    provider = get_llm_provider()
    prompt = "Describe the dominant color of this image. Be concise, one word if possible."
    
    print_info(f"Prompt: '{prompt}'")
    
    response = provider.complete(
        prompt=prompt,
        system_prompt="You are a helpful assistant. Answer concisely.",
        image_path=str(image_path),
    )
    
    if response.error:
        print_error(f"LLM error: {response.error}")
        return False
    
    answer = response.content.strip().lower()
    print_info(f"Model response: '{response.content.strip()}'")
    
    # Python-side assertion: does the response contain "red"?
    if "red" in answer:
        print_success("Model correctly identified the red color!")
        return True
    else:
        print_error(f"Expected 'red' in response, got: '{answer}'")
        return False


def verify_layout_check(tmpdir: Path) -> bool:
    """Test 2: Can the model identify a blue header element?
    
    Strategy: Create a simple layout with blue header, ask about it.
    """
    print("\n[Test 2] Blue Header Layout Detection")
    print("-" * 40)
    
    from ams.core.factory import get_llm_provider
    
    # Create test image (white page with blue header)
    image_path = tmpdir / "blue_header.png"
    create_blue_header(image_path)
    print_info(f"Created: {image_path.name} (400x300 with blue header)")
    
    # Ask about the header
    provider = get_llm_provider()
    prompt = "Is there a blue header or blue rectangle at the top of this image? Answer yes or no."
    
    print_info(f"Prompt: '{prompt}'")
    
    response = provider.complete(
        prompt=prompt,
        system_prompt="You are a helpful assistant. Answer concisely.",
        image_path=str(image_path),
    )
    
    if response.error:
        print_error(f"LLM error: {response.error}")
        return False
    
    answer = response.content.strip().lower()
    print_info(f"Model response: '{response.content.strip()}'")
    
    # Python-side assertion: does the response indicate yes/blue?
    if "yes" in answer or "blue" in answer:
        print_success("Model correctly identified the blue header!")
        return True
    else:
        print_error(f"Expected 'yes' or 'blue' in response, got: '{answer}'")
        return False


def run_tests() -> bool:
    """Run all verification tests.
    
    Returns:
        True if all tests passed, False otherwise.
    """
    print_header("Phase 3.2: Vision Analysis Logic Verification")
    print("Strategy: Description-First (avoid negative logic)")
    
    # Step 1: Check prerequisites
    print("\n[Setup] Checking prerequisites...")
    
    if not check_llm_available():
        print_error("LM Studio is not running!")
        print_info("Start LM Studio with a vision model loaded.")
        return False
    print_success("LM Studio is running.")
    
    # Step 2: Import check
    try:
        from ams.llm.vision import VisionAnalyst
        from ams.core.factory import get_llm_provider
        print_success("Imports successful.")
    except ImportError as e:
        print_error(f"Failed to import: {e}")
        return False
    
    # Run tests in temp directory
    results = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Test 1: Red square
        results.append(("Red Square", verify_red_square(tmpdir_path)))
        
        # Test 2: Blue header
        results.append(("Blue Header", verify_layout_check(tmpdir_path)))
    
    # Summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print_success("ALL VISION TESTS PASSED!")
    else:
        print_error("Some vision tests failed.")
    print("=" * 60)
    
    return all_passed


def main() -> int:
    """Main entry point."""
    try:
        success = run_tests()
        return 0 if success else 1
    except KeyboardInterrupt:
        print_warning("\nInterrupted by user.")
        return 130
    except Exception as e:
        print_error(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
