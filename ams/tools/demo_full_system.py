#!/usr/bin/env python3
"""Final User Acceptance Test - Full System Demo.

This script demonstrates the complete Hybrid Assessment System by:
1. Creating a realistic student submission directory
2. Running the full pipeline with LLM + Vision capabilities
3. Generating a human-readable "Report Card" to the console

Usage:
    python -m ams.tools.demo_full_system

Prerequisites:
    - LM Studio running with a vision-capable model (e.g., qwen2-vl-2b-instruct)
    - pip install rich (for pretty console output)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Try to import rich for pretty output, fall back to basic print
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None


# =============================================================================
# Demo Student Submission Data
# =============================================================================

DEMO_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My Portfolio Website</title>
    <link rel="stylesheet" href="style.css">
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
        <h1>Welcome to My Portfolio</h1>
    </header>
    
    <main>
        <section id="about">
            <h2>About Me</h2>
            <p>I am a web development student learning HTML, CSS, and JavaScript.</p>
        </section>
        
        <section id="projects">
            <h2>My Projects</h2>
            <article>
                <h3>Project 1</h3>
                <p>A simple calculator built with JavaScript.</p>
            </article>
        </section>
        
        <form id="contact" action="submit.php" method="post">
            <h2>Contact Me</h2>
            <label for="name">Name:</label>
            <input type="text" id="name" name="name" required>
            
            <label for="email">Email:</label>
            <input type="email" id="email" name="email" required>
            
            <label for="message">Message:</label>
            <textarea id="message" name="message" rows="4"></textarea>
            
            <button type="submit">Send</button>
        </form>
    </main>
    
    <footer>
        <p>&copy; 2026 Student Portfolio. All rights reserved.</p>
    </footer>
</body>
</html>
'''

# CSS intentionally MISSING @media queries to trigger vision analysis
DEMO_CSS = '''/* Student Portfolio Stylesheet */
/* Note: Missing responsive design - no media queries */

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    line-height: 1.6;
    color: #333;
    background-color: #f4f4f4;
}

header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 2rem;
    text-align: center;
}

nav ul {
    list-style: none;
    display: flex;
    justify-content: center;
    gap: 2rem;
    margin-bottom: 1rem;
}

nav a {
    color: white;
    text-decoration: none;
    font-weight: bold;
}

main {
    max-width: 1200px;
    margin: 2rem auto;
    padding: 0 1rem;
}

section {
    background: white;
    padding: 2rem;
    margin-bottom: 2rem;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

h2 {
    color: #667eea;
    margin-bottom: 1rem;
}

form {
    background: white;
    padding: 2rem;
    border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

label {
    display: block;
    margin-bottom: 0.5rem;
    font-weight: bold;
}

input, textarea {
    width: 100%;
    padding: 0.75rem;
    margin-bottom: 1rem;
    border: 1px solid #ddd;
    border-radius: 4px;
}

button {
    background: #667eea;
    color: white;
    padding: 0.75rem 2rem;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 1rem;
}

button:hover {
    background: #5a6fd6;
}

footer {
    text-align: center;
    padding: 2rem;
    background: #333;
    color: white;
}
'''


def create_demo_submission(workspace: Path) -> Path:
    """Create a demo student submission directory."""
    submission_dir = workspace / "demo_student_submission"
    submission_dir.mkdir(parents=True, exist_ok=True)
    
    # Write HTML file
    (submission_dir / "index.html").write_text(DEMO_HTML, encoding="utf-8")
    
    # Write CSS file (missing @media queries)
    (submission_dir / "style.css").write_text(DEMO_CSS, encoding="utf-8")
    
    # Create a dummy screenshot (red square to simulate webpage render)
    try:
        from PIL import Image
        img = Image.new("RGB", (800, 600), color=(180, 50, 50))
        # Add some variation to simulate a real screenshot
        for y in range(100):
            for x in range(800):
                img.putpixel((x, y), (102, 126, 234))  # Purple header
        img.save(submission_dir / "screenshot.png")
    except ImportError:
        # Fallback: create a minimal PNG manually
        print("⚠️  PIL not installed - using placeholder screenshot")
        # Write a minimal valid PNG (1x1 red pixel)
        png_data = bytes([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
            0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1
            0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
            0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
            0x00, 0x00, 0x03, 0x00, 0x01, 0x00, 0x05, 0xFE,
            0xDB, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
            0x44, 0xAE, 0x42, 0x60, 0x82
        ])
        (submission_dir / "screenshot.png").write_bytes(png_data)
    
    return submission_dir


def print_divider(title: str = "") -> None:
    """Print a section divider."""
    if RICH_AVAILABLE:
        console.rule(title, style="bold blue")
    else:
        print(f"\n{'='*60}")
        if title:
            print(f"  {title}")
        print("="*60)


def print_report_card(report: dict) -> None:
    """Print a human-readable report card from the JSON report."""
    
    if RICH_AVAILABLE:
        _print_rich_report_card(report)
    else:
        _print_basic_report_card(report)


def _print_rich_report_card(report: dict) -> None:
    """Print report card using Rich library."""
    
    # Header
    console.print()
    console.print(Panel.fit(
        "[bold white]🎓 STUDENT ASSESSMENT REPORT CARD[/bold white]",
        border_style="blue",
        padding=(1, 4),
    ))
    console.print()
    
    # Metadata
    metadata = report.get("metadata", {})
    console.print(f"[dim]Profile:[/dim] {metadata.get('profile', 'N/A')}")
    console.print(f"[dim]Scoring Mode:[/dim] {metadata.get('scoring_mode', 'N/A')}")
    console.print(f"[dim]Timestamp:[/dim] {metadata.get('timestamp', 'N/A')}")
    console.print()
    
    # Score Summary
    score_evidence = report.get("score_evidence", {})
    final_score = score_evidence.get("final_score", 0)
    max_score = score_evidence.get("max_score", 100)
    percentage = (final_score / max_score * 100) if max_score > 0 else 0
    
    grade_color = "green" if percentage >= 70 else "yellow" if percentage >= 50 else "red"
    
    console.print(Panel(
        f"[bold {grade_color}]{final_score:.1f} / {max_score:.1f}[/bold {grade_color}]\n"
        f"[{grade_color}]{percentage:.1f}%[/{grade_color}]",
        title="📊 Final Score",
        border_style=grade_color,
    ))
    console.print()
    
    # Component Breakdown
    component_scores = score_evidence.get("component_scores", {})
    if component_scores:
        table = Table(title="Component Breakdown", box=box.ROUNDED)
        table.add_column("Component", style="cyan")
        table.add_column("Score", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Weighted", justify="right", style="bold")
        
        for comp, data in component_scores.items():
            if isinstance(data, dict):
                score = data.get("score", 0)
                weight = data.get("weight", 1)
                weighted = score * weight
                table.add_row(
                    comp.upper(),
                    f"{score:.2f}",
                    f"{weight:.2f}",
                    f"{weighted:.2f}"
                )
        
        console.print(table)
        console.print()
    
    # LLM Analysis Section
    llm_analysis = score_evidence.get("llm_analysis", {})
    
    # AI Feedback
    feedback_items = llm_analysis.get("feedback", [])
    if feedback_items:
        console.print("[bold blue]🤖 AI Feedback (Top Issues)[/bold blue]")
        for i, item in enumerate(feedback_items[:3], 1):  # Show top 3
            console.print(Panel(
                f"[yellow]Rule:[/yellow] {item.get('finding_id', 'Unknown')}\n\n"
                f"{item.get('feedback', 'No feedback available')}",
                border_style="dim",
            ))
        console.print()
    
    # Vision Analysis
    vision_items = llm_analysis.get("vision_analysis", [])
    if vision_items:
        console.print("[bold magenta]👁️ Vision Analysis[/bold magenta]")
        for item in vision_items:
            result = item.get("result", {})
            status = result.get("result", "UNKNOWN")
            reason = result.get("reason", "No reason provided")
            finding_id = item.get("finding_id", "Unknown")
            
            status_color = "green" if status == "PASS" else "red" if status == "FAIL" else "yellow"
            
            console.print(f"  [bold]Vision Check for[/bold] [cyan]{finding_id}[/cyan]:")
            console.print(f"    Status: [{status_color}]{status}[/{status_color}]")
            console.print(f"    Reason: {reason}")
        console.print()
    else:
        console.print("[dim]No vision analysis performed.[/dim]\n")
    
    # Partial Credit
    partial_items = llm_analysis.get("partial_credit", [])
    if partial_items:
        console.print("[bold green]💡 Partial Credit Awards[/bold green]")
        for item in partial_items:
            hybrid = item.get("hybrid_score", {})
            console.print(f"  • {item.get('finding_id')}: "
                         f"[green]+{hybrid.get('final_score', 0):.2f}[/green] points")
        console.print()
    
    # Summary Message
    console.print(Panel(
        "[bold]Assessment Complete![/bold]\n\n"
        "This report was generated by the Hybrid Assessment System using:\n"
        "• Static code analysis\n"
        "• LLM-powered feedback generation\n"
        "• Vision-based visual grading",
        title="✅ Summary",
        border_style="green",
    ))


def _print_basic_report_card(report: dict) -> None:
    """Print report card using basic print statements."""
    
    print("\n" + "="*60)
    print("🎓 STUDENT ASSESSMENT REPORT CARD")
    print("="*60)
    
    # Metadata
    metadata = report.get("metadata", {})
    print(f"Profile: {metadata.get('profile', 'N/A')}")
    print(f"Scoring Mode: {metadata.get('scoring_mode', 'N/A')}")
    print(f"Timestamp: {metadata.get('timestamp', 'N/A')}")
    
    # Score
    score_evidence = report.get("score_evidence", {})
    final_score = score_evidence.get("final_score", 0)
    max_score = score_evidence.get("max_score", 100)
    percentage = (final_score / max_score * 100) if max_score > 0 else 0
    
    print("\n📊 FINAL SCORE")
    print("-"*30)
    print(f"  {final_score:.1f} / {max_score:.1f} ({percentage:.1f}%)")
    
    # LLM Analysis
    llm_analysis = score_evidence.get("llm_analysis", {})
    
    # AI Feedback
    feedback_items = llm_analysis.get("feedback", [])
    if feedback_items:
        print("\n🤖 AI FEEDBACK")
        print("-"*30)
        for item in feedback_items[:3]:
            finding_id = item.get('finding_id', 'Unknown')
            feedback_raw = item.get('feedback', 'No feedback')
            
            # Handle dict or string feedback
            if isinstance(feedback_raw, dict):
                evidence = feedback_raw.get('evidence', feedback_raw.get('reason', str(feedback_raw)))
                feedback_text = f"[{feedback_raw.get('result', 'CHECK')}] {evidence}"
            else:
                feedback_text = str(feedback_raw)
            
            if len(feedback_text) > 200:
                feedback_text = feedback_text[:200] + "..."
            
            print(f"  Rule: {finding_id}")
            print(f"  {feedback_text}")
            print()
    
    # Vision Analysis
    vision_items = llm_analysis.get("vision_analysis", [])
    if vision_items:
        print("\n👁️ VISION ANALYSIS")
        print("-"*30)
        for item in vision_items:
            result = item.get("result", {})
            status = result.get("result", "UNKNOWN")
            reason = result.get("reason", "No reason provided")
            finding_id = item.get("finding_id", "Unknown")
            print(f"  Vision Check for [{finding_id}]: {status}")
            print(f"    Reason: {reason}")
    else:
        print("\nNo vision analysis performed.")
    
    print("\n" + "="*60)
    print("✅ Assessment Complete!")
    print("="*60)


def run_demo() -> bool:
    """Run the full system demo."""
    
    print_divider("HYBRID ASSESSMENT SYSTEM - FULL DEMO")
    
    # Step 1: Check prerequisites
    print("\n[Step 1] Checking prerequisites...")
    
    try:
        from ams.core.pipeline import AssessmentPipeline
        from ams.core.config import ScoringMode
        print("  ✅ Pipeline imported successfully")
    except ImportError as e:
        print(f"  ❌ Failed to import pipeline: {e}")
        return False
    
    # Check LM Studio
    try:
        import requests
        resp = requests.get("http://127.0.0.1:1234/api/v1/models", timeout=5)
        if resp.status_code == 200:
            print("  ✅ LM Studio is running")
        else:
            print("  ⚠️  LM Studio may not be ready")
    except Exception:
        print("  ⚠️  LM Studio not detected - LLM features may be limited")
    
    # Step 2: Create demo submission
    print("\n[Step 2] Creating demo student submission...")
    workspace = Path(tempfile.mkdtemp(prefix="ams_demo_"))
    output_dir = workspace / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    submission_dir = create_demo_submission(workspace)
    print(f"  ✅ Created: {submission_dir}")
    print(f"  📁 Files: {[f.name for f in submission_dir.iterdir()]}")
    
    # Copy screenshot to output for vision analysis
    screenshot_src = submission_dir / "screenshot.png"
    screenshot_dst = output_dir / "screenshot.png"
    if screenshot_src.exists():
        shutil.copy(screenshot_src, screenshot_dst)
        print(f"  📷 Screenshot ready for vision analysis")
    
    try:
        # Step 3: Run pipeline
        print("\n[Step 3] Running AssessmentPipeline with STATIC_PLUS_LLM...")
        
        pipeline = AssessmentPipeline(scoring_mode=ScoringMode.STATIC_PLUS_LLM)
        
        report_path = pipeline.run(
            submission_path=submission_dir,
            workspace_path=output_dir,
            profile="frontend",  # Using frontend profile for this demo
        )
        
        print(f"  ✅ Pipeline completed!")
        print(f"  📄 Report: {report_path}")
        
        # Step 4: Load and display report
        print("\n[Step 4] Generating Report Card...")
        
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        
        print_report_card(report)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print(f"\n[Cleanup] Removing workspace: {workspace}")
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    """Main entry point."""
    try:
        success = run_demo()
        return 0 if success else 1
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user.")
        return 130
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
