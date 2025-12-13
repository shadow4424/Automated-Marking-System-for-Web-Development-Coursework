"""Entry point for running the Automated Marking System pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from ams.pipeline import AssessmentPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic AMS assessment over a submission archive")
    parser.add_argument("submission", type=Path, help="Path to the submission zip archive")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("submissions"),
        help="Workspace directory for extracted submissions",
    )
    parser.add_argument(
        "--reports",
        type=Path,
        default=Path("reports"),
        help="Output directory for generated reports",
    )
    parser.add_argument(
        "--enable-playwright",
        action="store_true",
        help="Enable Playwright-backed browser automation if available (disabled by default for determinism)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pipeline = AssessmentPipeline(workspace=args.workspace, enable_playwright=args.enable_playwright)
    report = pipeline.assess_and_write_reports(args.submission, args.reports)
    print(pipeline.reporter.render_text(report))


if __name__ == "__main__":
    main()
