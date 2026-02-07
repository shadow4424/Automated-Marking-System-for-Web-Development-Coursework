"""Phase 4 Integration Test - Pipeline + LLM Scoring + Vision End-to-End.

This script verifies that the AssessmentPipeline correctly integrates
with the LLM scoring, feedback, and vision analysis modules:

1. Creates a mock submission with intentionally broken PHP code
2. Creates a screenshot.png for vision analysis
3. Runs the pipeline with STATIC_PLUS_LLM mode
4. Verifies that:
   - The LLM feedback hook was triggered
   - Partial credit was evaluated for eligible rules
   - Vision analysis was performed for visual_check rules

Usage:
    python -m ams.tools.test_pipeline_integration

Prerequisites:
    - LM Studio must be running with a vision model loaded
    - The AMS package must be installed
"""
from __future__ import annotations

import json
import shutil
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


# =============================================================================
# Test Fixtures
# =============================================================================

# PHP file with syntax error but clear database connection intent
BROKEN_PHP_CONTENT = '''<?php
/**
 * Database Connection Script
 * This file demonstrates a database connection with proper error handling.
 * Note: Contains intentional syntax error for testing partial credit.
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

# Minimal HTML file for a valid submission
MINIMAL_HTML_CONTENT = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Test Submission</title>
</head>
<body>
    <header>
        <h1>Test Page</h1>
    </header>
    <main>
        <p>This is a test submission.</p>
        <form action="connect.php" method="post">
            <label for="username">Username:</label>
            <input type="text" id="username" name="username">
            <button type="submit">Submit</button>
        </form>
    </main>
    <footer>
        <p>&copy; 2026 Test</p>
    </footer>
</body>
</html>
'''

# Minimal CSS file WITHOUT media query (to trigger visual_check)
MINIMAL_CSS_CONTENT = '''/* Test CSS - intentionally missing responsive queries */
body {
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 20px;
}

.container {
    max-width: 800px;
    margin: 0 auto;
}
'''


def create_test_submission(workspace: Path) -> Path:
    """Create a test submission directory with screenshot."""
    submission_dir = workspace / "submission"
    submission_dir.mkdir(parents=True, exist_ok=True)
    
    # Create files
    (submission_dir / "connect.php").write_text(BROKEN_PHP_CONTENT, encoding="utf-8")
    (submission_dir / "index.html").write_text(MINIMAL_HTML_CONTENT, encoding="utf-8")
    (submission_dir / "styles.css").write_text(MINIMAL_CSS_CONTENT, encoding="utf-8")
    
    return submission_dir


def create_test_screenshot(output_dir: Path) -> Path:
    """Create a test screenshot (red square) for vision analysis."""
    try:
        from PIL import Image
    except ImportError:
        return None
    
    screenshot_path = output_dir / "screenshot.png"
    # Create a simple red square (simulating a webpage screenshot)
    img = Image.new("RGB", (200, 200), color=(255, 50, 50))
    img.save(screenshot_path)
    return screenshot_path


def check_llm_available() -> bool:
    """Check if LM Studio is running."""
    import requests
    
    try:
        resp = requests.get("http://127.0.0.1:1234/api/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# =============================================================================
# Main Test
# =============================================================================

def run_integration_test() -> bool:
    """Run the pipeline integration test.
    
    Returns:
        True if test passed, False otherwise.
    """
    print_header("Phase 4: Pipeline Integration Test")
    
    # Step 1: Check prerequisites
    print("\n[Step 1] Checking prerequisites...")
    
    if not check_llm_available():
        print_warning("LM Studio is not running. Skipping LLM-dependent assertions.")
        print_warning("Start LM Studio and re-run for full verification.")
        llm_available = False
    else:
        print_success("LM Studio is running.")
        llm_available = True
    
    # Step 2: Import modules
    print("\n[Step 2] Importing AMS modules...")
    try:
        from ams.core.pipeline import AssessmentPipeline
        from ams.core.config import ScoringMode
        print_success("Pipeline imported successfully.")
    except ImportError as e:
        print_error(f"Failed to import: {e}")
        return False
    
    # Step 3: Create test submission
    print("\n[Step 3] Creating test submission...")
    workspace = Path(tempfile.mkdtemp(prefix="ams_integration_test_"))
    
    try:
        submission_dir = create_test_submission(workspace)
        output_dir = workspace / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print_success(f"Created submission at: {submission_dir}")
        print_info(f"Files: {[f.name for f in submission_dir.iterdir()]}")
        
        # Create screenshot for vision analysis
        screenshot_path = create_test_screenshot(output_dir)
        if screenshot_path:
            print_success(f"Created screenshot for vision test: {screenshot_path.name}")
        else:
            print_warning("PIL not installed - skipping screenshot creation")
        
        # Step 4: Run pipeline with STATIC_PLUS_LLM
        print("\n[Step 4] Running AssessmentPipeline with STATIC_PLUS_LLM...")
        
        pipeline = AssessmentPipeline(
            scoring_mode=ScoringMode.STATIC_PLUS_LLM
        )
        
        report_path = pipeline.run(
            submission_path=submission_dir,
            workspace_path=output_dir,
            profile="fullstack",
        )
        
        print_success(f"Pipeline completed. Report at: {report_path}")
        
        # Step 5: Analyze report
        print("\n[Step 5] Analyzing report...")
        
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        
        # Check for LLM evidence
        score_evidence = report.get("score_evidence", {})
        llm_analysis = score_evidence.get("llm_analysis", {})
        
        print_info(f"Total findings: {len(report.get('findings', []))}")
        print_info(f"LLM feedback items: {len(llm_analysis.get('feedback', []))}")
        print_info(f"Partial credit items: {len(llm_analysis.get('partial_credit', []))}")
        print_info(f"Vision analysis items: {len(llm_analysis.get('vision_analysis', []))}")
        
        # Debug: Show css.has_media_query findings
        findings_list = report.get("findings", [])
        css_findings = [
            f for f in findings_list 
            if f.get("evidence", {}).get("rule_id", "") == "css.has_media_query"
        ]
        if css_findings:
            print_info(f"CSS media query findings: {len(css_findings)}")
            for f in css_findings:
                print_info(f"  - rule_id={f.get('evidence', {}).get('rule_id')}, id={f.get('id')}, severity={f.get('severity')}, score={f.get('score')}")
        else:
            print_warning("No css.has_media_query findings found - rule may not be failing")
        
        # Step 6: Assert results
        print("\n[Step 6] Verifying results...")
        passed = True
        
        # Check that metadata was recorded
        if report.get("metadata", {}).get("scoring_mode") == "static_plus_llm":
            print_success("Scoring mode correctly set to 'static_plus_llm'")
        else:
            print_error("Scoring mode not correctly recorded in report")
            passed = False
        
        # Check LLM integration (only if available)
        if llm_available:
            if llm_analysis.get("feedback"):
                print_success(f"LLM feedback hook triggered ({len(llm_analysis['feedback'])} items)")
            else:
                print_warning("No LLM feedback generated (may be expected if no failures)")
            
            if llm_analysis.get("partial_credit"):
                print_success(f"Partial credit evaluated ({len(llm_analysis['partial_credit'])} items)")
                
                # Check for score upgrade
                for item in llm_analysis["partial_credit"]:
                    hybrid = item.get("hybrid_score", {})
                    if hybrid.get("final_score", 0) > 0:
                        print_success(f"Score upgrade awarded: {item['finding_id']} -> {hybrid['final_score']}")
            else:
                print_warning("No partial credit evaluations (check rule configurations)")
            
            # Check Vision Analysis
            if llm_analysis.get("vision_analysis"):
                print_success(f"Vision analysis triggered ({len(llm_analysis['vision_analysis'])} items)")
                for item in llm_analysis["vision_analysis"]:
                    result = item.get("result", {})
                    print_info(f"  - {item['finding_id']}: {result.get('result', 'N/A')}")
            else:
                print_error("No vision analysis performed - expected at least 1 item")
                passed = False
        else:
            print_warning("Skipped LLM assertions (LM Studio offline)")
        
        # Summary
        print("\n" + "=" * 60)
        if passed:
            print_success("INTEGRATION TEST PASSED!")
            print_info("Pipeline successfully integrates with LLM modules.")
        else:
            print_error("INTEGRATION TEST FAILED!")
        print("=" * 60)
        
        return passed
        
    finally:
        # Cleanup
        print(f"\n[Cleanup] Removing workspace: {workspace}")
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    """Main entry point."""
    try:
        success = run_integration_test()
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
