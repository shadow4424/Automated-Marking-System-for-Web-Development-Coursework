#!/usr/bin/env python
"""Full System Demo - End-to-End Assessment Pipeline Test.

This script demonstrates the complete assessment pipeline, including:
- File discovery and parsing
- Static analysis
- LLM feedback generation
- Vision analysis (if enabled)
- Conflict resolution
- HTML report generation

The output is PERSISTENT - files remain after script exits for inspection.

Usage:
    python tools/demo_full_system.py [profile]
    
Example:
    python tools/demo_full_system.py frontend
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ams.core.pipeline import AssessmentPipeline
from ams.core.config import SCORING_MODE, ScoringMode

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_sample_submission(submission_dir: Path) -> None:
    """Create a minimal sample submission for testing."""
    
    # Create index.html
    (submission_dir / "index.html").write_text("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sample Portfolio</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <header>
        <nav>
            <ul>
                <li><a href="#home">Home</a></li>
                <li><a href="#about">About</a></li>
                <li><a href="#contact">Contact</a></li>
            </ul>
        </nav>
    </header>
    <main>
        <section id="home">
            <h1>Welcome to My Portfolio</h1>
            <p>This is a sample submission for testing.</p>
        </section>
        <section id="about">
            <h2>About Me</h2>
            <p>I am learning web development.</p>
        </section>
        <section id="contact">
            <h2>Contact</h2>
            <form id="contact-form">
                <label for="email">Email:</label>
                <input type="email" id="email" name="email" required>
                <button type="submit">Submit</button>
            </form>
        </section>
    </main>
    <footer>
        <p>&copy; 2024 Sample Portfolio</p>
    </footer>
    <script src="script.js"></script>
</body>
</html>
""", encoding="utf-8")
    
    # Create styles.css
    (submission_dir / "styles.css").write_text("""/* Sample CSS */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: Arial, sans-serif;
    line-height: 1.6;
    color: #333;
}

header {
    background: #333;
    color: white;
    padding: 1rem;
}

nav ul {
    list-style: none;
    display: flex;
    gap: 1rem;
}

nav a {
    color: white;
    text-decoration: none;
}

main {
    max-width: 1200px;
    margin: 0 auto;
    padding: 2rem;
}

section {
    margin-bottom: 2rem;
}

/* Responsive design */
@media (max-width: 768px) {
    nav ul {
        flex-direction: column;
    }
    
    main {
        padding: 1rem;
    }
}

footer {
    background: #333;
    color: white;
    text-align: center;
    padding: 1rem;
}
""", encoding="utf-8")
    
    # Create script.js
    (submission_dir / "script.js").write_text("""// Sample JavaScript
document.addEventListener('DOMContentLoaded', function() {
    console.log('Page loaded');
    
    // Form validation
    const form = document.getElementById('contact-form');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const email = document.getElementById('email').value;
            if (email) {
                alert('Thank you for your message!');
            }
        });
    }
});
""", encoding="utf-8")
    
    logger.info(f"Created sample submission with 3 files")


def run_demo(profile: str = "frontend", scoring_mode: str = "static_only") -> Path:
    """Run the full assessment pipeline demo.
    
    Returns:
        Path to the workspace directory containing all output.
    """
    # Create persistent output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workspace_base = PROJECT_ROOT / "demo_output" / f"demo_{timestamp}"
    workspace_base.mkdir(parents=True, exist_ok=True)
    
    submission_dir = workspace_base / "submission"
    submission_dir.mkdir(exist_ok=True)
    
    workspace_dir = workspace_base / "workspace"
    workspace_dir.mkdir(exist_ok=True)
    
    print("\n" + "="*70)
    print(" FULL SYSTEM DEMO - Automated Marking System")
    print("="*70)
    print(f"\n  Profile: {profile}")
    print(f"  Scoring Mode: {scoring_mode}")
    print(f"  Output Directory: {workspace_base}")
    print()
    
    # Create sample submission
    print("📁 Creating sample submission...")
    create_sample_submission(submission_dir)
    
    # Copy submission to workspace (simulating extraction)
    import shutil
    for f in submission_dir.iterdir():
        shutil.copy(f, workspace_dir / f.name)
    
    # Run assessment pipeline
    print("\n🔍 Running assessment pipeline...")
    
    # Map string mode to Enum
    try:
        mode_enum = ScoringMode(scoring_mode)
    except ValueError:
        logger.warning(f"Invalid scoring mode '{scoring_mode}', defaulting to static_only")
        mode_enum = ScoringMode.STATIC_ONLY

    pipeline = AssessmentPipeline(scoring_mode=mode_enum)
    
    try:
        report_path = pipeline.run(
            submission_path=submission_dir,
            workspace_path=workspace_dir,
            profile=profile,
            metadata={
                "demo": True,
                "timestamp": timestamp,
                "student_id": "DEMO_STUDENT",
                "mode": scoring_mode,
            },
        )
        
        # Load and display results
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        
        print("\n" + "="*70)
        print(" ASSESSMENT RESULTS")
        print("="*70)
        
        # Display scores
        scores = report.get("scores", {})
        print("\n📊 Scores:")
        for component, score in scores.items():
            # Handle both float scores and dict scores
            try:
                if isinstance(score, dict):
                    value = float(score.get("score", score.get("percentage", 0)))
                elif isinstance(score, (int, float)):
                    value = float(score)
                else:
                    continue  # Skip non-numeric values like timestamps
                bar = "█" * int(value / 10) + "░" * (10 - int(value / 10))
                print(f"    {component:12s}: {bar} {value:5.1f}%")
            except (ValueError, TypeError):
                continue  # Skip values that can't be converted
        
        # Display finding summary
        findings = report.get("findings", [])
        fail_count = sum(1 for f in findings if f.get("severity") == "FAIL")
        warn_count = sum(1 for f in findings if f.get("severity") == "WARN")
        pass_count = sum(1 for f in findings if f.get("severity") == "INFO")
        
        print(f"\n📋 Findings Summary:")
        print(f"    ✓ Pass: {pass_count}")
        print(f"    ⚠ Warn: {warn_count}")
        print(f"    ✗ Fail: {fail_count}")

        # Display AI Feedback if present
        llm_findings = [f for f in findings if f.get("evidence", {}).get("llm_feedback")]
        
        if llm_findings:
            print("\n🤖 AI Feedback Generated:")
            for f in llm_findings[:3]: # Show first 3
                feedback = f.get("evidence", {}).get("llm_feedback")
                # Handle dictionary or string feedback
                text = feedback.get("summary", "") if isinstance(feedback, dict) else str(feedback)
                if not text and isinstance(feedback, dict) and feedback.get("items"):
                     text = feedback["items"][0].get("message", "")
                
                print(f"    - {f.get('id')}: {text[:100]}...")
            
            if len(llm_findings) > 3:
                print(f"    ... and {len(llm_findings) - 3} more")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    
    return workspace_base


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Full System Demo - End-to-End Assessment Pipeline")
    parser.add_argument("profile", nargs="?", default="frontend", help="Assessment profile (default: frontend)")
    parser.add_argument("--mode", default="static_plus_llm", help="Scoring mode (static_only, static_plus_llm, etc.)")
    parser.add_argument("--no-llm", action="store_true", help="Force static_only mode (disable LLM)")
    
    args = parser.parse_args()
    
    # Handle --no-llm override
    mode = "static_only" if args.no_llm else args.mode
    
    try:
        workspace = run_demo(args.profile, scoring_mode=mode)
        
        # Find generated files
        json_report = workspace / "workspace" / "report.json"
        html_report = workspace / "workspace" / "report.html"
        
        print("\n" + "="*70)
        print(" OUTPUT FILES")
        print("="*70)
        print(f"\n  📄 JSON Report: {json_report}")
        
        if html_report.exists():
            print(f"  🌐 HTML Report: {html_report}")
            print("\n" + "="*70)
            print(" ✅ SUCCESS - Files preserved for inspection!")
            print("="*70)
            print(f"\n  👉 Open this file in your browser:")
            print(f"     file:///{html_report.as_posix()}")
            print()
        else:
            print("\n  ⚠️  HTML Report was not generated")
            print(f"     Check logs for errors")
        
        return 0
        
    except Exception as e:
        logger.error(f"Demo failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
