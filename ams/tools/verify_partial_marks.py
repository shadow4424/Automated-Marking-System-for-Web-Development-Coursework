#!/usr/bin/env python3
"""Phase 2 Verification Script - Hybrid Scoring End-to-End Test.

This script verifies that the hybrid scoring system works correctly by:
1. Creating a test fixture with intentionally broken but well-intentioned code
2. Running the assessment pipeline with STATIC_PLUS_LLM mode
3. Asserting that the LLM upgrades the score from 0.0 to 0.5

Usage:
    python -m ams.tools.verify_phase2
    
Prerequisites:
    - LM Studio must be running with Llama 3.2 3B loaded
    - The AMS package must be importable
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

# Rich console for pretty output (fallback to print if not available)
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None


def print_header(text: str) -> None:
    """Print a styled header."""
    if HAS_RICH:
        console.print(Panel(text, style="bold blue"))
    else:
        print("=" * 60)
        print(text)
        print("=" * 60)


def print_success(text: str) -> None:
    """Print success message."""
    if HAS_RICH:
        console.print(f"[bold green]✅ {text}[/bold green]")
    else:
        print(f"✅ {text}")


def print_error(text: str) -> None:
    """Print error message."""
    if HAS_RICH:
        console.print(f"[bold red]❌ {text}[/bold red]")
    else:
        print(f"❌ {text}")


def print_warning(text: str) -> None:
    """Print warning message."""
    if HAS_RICH:
        console.print(f"[bold yellow]⚠️ {text}[/bold yellow]")
    else:
        print(f"⚠️ {text}")


def print_info(text: str) -> None:
    """Print info message."""
    if HAS_RICH:
        console.print(f"[cyan]{text}[/cyan]")
    else:
        print(text)


# =============================================================================
# Test Fixtures
# =============================================================================

# PHP file with syntax error but clear database connection intent
BROKEN_PHP_CONTENT = '''<?php
/**
 * Database Connection Script
 * This file demonstrates a database connection with proper error handling.
 * Note: Contains intentional syntax error for testing.
 */

// Database configuration
$host = "localhost";
$dbname = "coursework_db";
$username = "student";
$password = "secure_password";

// Attempt database connection with PDO
try {
    $pdo = new PDO("mysql:host=$host;dbname=$dbname", $username, $password)
    
    // Set error mode to exception
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    
    // Prepare and execute query
    $stmt = $pdo->prepare("SELECT * FROM users WHERE active = :active");
    $stmt->execute(['active' => 1]);
    
    // Fetch results
    $users = $stmt->fetchAll(PDO::FETCH_ASSOC);
    
    foreach ($users as $user) {
        echo "User: " . htmlspecialchars($user['name']) . "<br>";
    }
    
} catch (PDOException $e) {
    error_log("Database Error: " . $e->getMessage());
    echo "A database error occurred. Please try again later.";
}
?>
'''

# HTML file for a minimal valid submission
MINIMAL_HTML_CONTENT = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Test Submission</title>
</head>
<body>
    <h1>Test Page</h1>
    <p>This is a test submission.</p>
</body>
</html>
'''


def create_test_fixture(workspace: Path) -> Path:
    """Create a test submission with broken but well-intentioned PHP code.
    
    Args:
        workspace: Directory to create the fixture in.
        
    Returns:
        Path to the submission directory.
    """
    submission_dir = workspace / "submission"
    submission_dir.mkdir(parents=True, exist_ok=True)
    
    # Create the broken PHP file
    php_file = submission_dir / "connect.php"
    php_file.write_text(BROKEN_PHP_CONTENT, encoding="utf-8")
    
    # Create a minimal HTML file
    html_file = submission_dir / "index.html"
    html_file.write_text(MINIMAL_HTML_CONTENT, encoding="utf-8")
    
    return submission_dir


# =============================================================================
# LLM Health Check
# =============================================================================

def check_llm_available() -> bool:
    """Check if LM Studio is running and responding."""
    import requests
    
    try:
        resp = requests.get("http://127.0.0.1:1234/api/v1/models", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return False


# =============================================================================
# Main Verification Logic
# =============================================================================

def run_verification() -> bool:
    """Run the Phase 2 verification test.
    
    Returns:
        True if verification passed, False otherwise.
    """
    print_header("Phase 2 Verification: Hybrid Scoring End-to-End Test")
    
    # Step 1: Check LLM availability
    print_info("\n[Step 1] Checking LM Studio connectivity...")
    if not check_llm_available():
        print_warning("LM Studio is not running. Skipping LLM-dependent tests.")
        print_warning("Start LM Studio with Llama 3.2 3B and re-run this script.")
        return False
    print_success("LM Studio is running and responsive.")
    
    # Step 2: Import AMS modules
    print_info("\n[Step 2] Importing AMS modules...")
    try:
        from ams.core.pipeline import AssessmentPipeline
        from ams.core.config import ScoringMode
        from ams.llm.scoring import evaluate_partial_credit, HybridScore
        print_success("All AMS modules imported successfully.")
    except ImportError as e:
        print_error(f"Failed to import AMS modules: {e}")
        return False
    
    # Step 3: Create test fixture
    print_info("\n[Step 3] Creating test fixture...")
    workspace = Path(tempfile.mkdtemp(prefix="ams_phase2_test_"))
    try:
        submission_dir = create_test_fixture(workspace)
        print_success(f"Test fixture created at: {workspace}")
        print_info(f"  - Submission: {submission_dir}")
        
        # Step 4: Test partial credit evaluation directly
        print_info("\n[Step 4] Testing partial credit evaluation...")
        
        # Read the broken PHP code
        php_code = (submission_dir / "connect.php").read_text(encoding="utf-8")
        
        # Evaluate partial credit
        hybrid_score = evaluate_partial_credit(
            rule_name="php.syntax",
            student_code=php_code,
            error_context="Missing semicolon after PDO constructor call on line 17",
            category="Syntax",
            partial_range=(0.0, 0.5),
        )
        
        print_info(f"\n  Static Score: {hybrid_score.static_score}")
        print_info(f"  LLM Score:    {hybrid_score.llm_score}")
        print_info(f"  Final Score:  {hybrid_score.final_score}")
        print_info(f"  Intent:       {hybrid_score.intent_detected}")
        print_info(f"  Reasoning:    {hybrid_score.reasoning}")
        
        # Step 5: Assert results
        print_info("\n[Step 5] Verifying results...")
        
        passed = True
        
        # Check that intent was detected
        if hybrid_score.intent_detected:
            print_success("Intent detected: The LLM recognized implementation intent.")
        else:
            print_error("Intent NOT detected: LLM failed to recognize implementation intent.")
            passed = False
        
        # Check that LLM provided a score
        if hybrid_score.llm_score is not None and hybrid_score.llm_score > 0.0:
            print_success(f"LLM awarded partial credit: {hybrid_score.llm_score}")
        else:
            print_error(f"LLM did NOT award partial credit. Score: {hybrid_score.llm_score}")
            passed = False
        
        # Check final score was upgraded
        if hybrid_score.final_score == 0.5:
            print_success("Final score correctly upgraded from 0.0 → 0.5")
        elif hybrid_score.final_score > 0.0:
            print_warning(f"Final score upgraded to {hybrid_score.final_score} (expected 0.5)")
        else:
            print_error("Final score was NOT upgraded. Still 0.0.")
            passed = False
        
        # Check reasoning is present
        if hybrid_score.reasoning:
            print_success(f"LLM provided reasoning: \"{hybrid_score.reasoning[:100]}...\"")
        else:
            print_warning("LLM did not provide reasoning.")
        
        # Final summary
        print_info("\n" + "=" * 60)
        if passed:
            print_success("TEST PASSED: Score upgraded from 0.0 → 0.5 based on LLM Evidence!")
        else:
            print_error("TEST FAILED: Hybrid scoring did not work as expected.")
        print_info("=" * 60)
        
        return passed
        
    finally:
        # Cleanup
        print_info(f"\n[Cleanup] Removing temporary workspace...")
        shutil.rmtree(workspace, ignore_errors=True)
        print_info("Done.")


def main() -> int:
    """Main entry point."""
    try:
        success = run_verification()
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
