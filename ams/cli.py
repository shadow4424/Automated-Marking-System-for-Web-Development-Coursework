from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from datetime import datetime, timezone

from .pipeline import AssessmentPipeline


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ams", description="Automated Marking System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mark_parser = subparsers.add_parser("mark", help="Run assessment on a submission")
    mark_parser.add_argument("submission_path", type=Path, help="Path to submission directory")
    mark_parser.add_argument("--workspace", "-w", type=Path, help="Path to workspace directory (persistent)")
    mark_parser.add_argument("--out", "-o", type=Path, help="Path to write final report.json to")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _create_parser()
    args = parser.parse_args(argv)

    if args.command == "mark":
        submission_path: Path = args.submission_path
        # Determine workspace directory: user-specified or persistent default
        if args.workspace:
            workspace_path = Path(args.workspace)
            workspace_path.mkdir(parents=True, exist_ok=True)
        else:
            base = Path.cwd() / "ams_runs"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            workspace_path = base / timestamp
            workspace_path.mkdir(parents=True, exist_ok=True)

        pipeline = AssessmentPipeline()
        report_path = pipeline.run(submission_path=submission_path, workspace_path=workspace_path)

        # If an explicit output path is requested, copy report there
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(report_path.read_bytes())
            report_path = out_path

        print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
