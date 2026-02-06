#!/usr/bin/env python3
"""Phase 3 Vision Test - Verify multimodal image+text requests.

This script tests the vision capabilities by:
1. Auto-generating a simple colored image using PIL
2. Sending it to the LLM with a color identification prompt
3. Verifying the response contains the correct color

Usage:
    python -m ams.tools.test_vision
    
Prerequisites:
    - LM Studio must be running with Qwen2-VL-2B-Instruct loaded
    - Pillow package must be installed (pip install pillow)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Check for PIL availability
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


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


def create_color_square(color: tuple[int, int, int], size: int = 100) -> Path:
    """Create a solid color square image.
    
    Args:
        color: RGB tuple (e.g., (255, 0, 0) for red)
        size: Image dimensions in pixels
        
    Returns:
        Path to the temporary image file
    """
    if not HAS_PIL:
        raise ImportError("Pillow is required. Install with: pip install pillow")
    
    # Create image
    img = Image.new("RGB", (size, size), color)
    
    # Save to temp file
    temp_path = Path(tempfile.gettempdir()) / "ams_test_color_square.png"
    img.save(temp_path, format="PNG")
    
    return temp_path


def check_llm_available() -> bool:
    """Check if LM Studio is running."""
    import requests
    
    try:
        resp = requests.get("http://127.0.0.1:1234/api/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def run_vision_test() -> bool:
    """Run the vision capability test.
    
    Returns:
        True if test passed, False otherwise.
    """
    print_header("Phase 3: Vision Capability Test")
    
    # Step 1: Check prerequisites
    print("\n[Step 1] Checking prerequisites...")
    
    if not HAS_PIL:
        print_error("Pillow is not installed. Run: pip install pillow")
        return False
    print_success("Pillow is available.")
    
    if not check_llm_available():
        print_warning("LM Studio is not running. Skipping vision test.")
        print_warning("Start LM Studio with Qwen2-VL-2B-Instruct and re-run.")
        return False
    print_success("LM Studio is running.")
    
    # Step 2: Import provider
    print("\n[Step 2] Importing LLM provider...")
    try:
        from ams.llm.providers import LocalLMStudioProvider
        print_success("Provider imported successfully.")
    except ImportError as e:
        print_error(f"Failed to import provider: {e}")
        return False
    
    # Step 3: Create test image
    print("\n[Step 3] Creating test image...")
    test_color = (255, 0, 0)  # Red
    color_name = "red"
    
    try:
        image_path = create_color_square(test_color, size=100)
        print_success(f"Created {color_name} square at: {image_path}")
    except Exception as e:
        print_error(f"Failed to create image: {e}")
        return False
    
    # Step 4: Query the vision model
    print("\n[Step 4] Querying vision model...")
    print_info("Prompt: 'Describe the single dominant color in this image.'")
    
    provider = LocalLMStudioProvider()
    
    response = provider.complete(
        prompt="Describe the single dominant color in this image. Answer with one word.",
        system_prompt="You are a precise vision assistant. Analyze the pixels of the image provided.",
        image_path=str(image_path),
        max_tokens=50,
    )
    
    if response.error:
        print_error(f"LLM Error: {response.error}")
        return False
    
    print_success(f"Response received in {response.latency_ms}ms")
    print_info(f"LLM says: \"{response.content}\"")
    
    # Step 5: Verify response
    print("\n[Step 5] Verifying response...")
    
    response_lower = response.content.lower()
    
    if color_name in response_lower:
        print_success(f"PASSED: Response correctly identifies '{color_name}'")
        passed = True
    else:
        print_error(f"FAILED: Expected '{color_name}' in response")
        print_info(f"Raw response: {repr(response.content)}")
        passed = False
    
    # Cleanup
    try:
        image_path.unlink()
        print_info("Cleaned up temporary image.")
    except Exception:
        pass
    
    # Summary
    print("\n" + "=" * 60)
    if passed:
        print_success("VISION TEST PASSED!")
        print_info("The model correctly identified the color in the image.")
    else:
        print_error("VISION TEST FAILED!")
        print_info("The model could not identify the color correctly.")
    print("=" * 60)
    
    return passed


def main() -> int:
    """Main entry point."""
    try:
        success = run_vision_test()
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
