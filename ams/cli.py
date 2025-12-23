from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import AssessmentPipeline


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ams", description="Automated Marking System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mark_parser = subparsers.add_parser("mark", help="Run assessment on a submission")
    mark_parser.add_argument("submission_path", type=Path, help="Path to submission directory")
    mark_parser.add_argument("--workspace", "-w", type=Path, help="Path to workspace directory (persistent)")
    mark_parser.add_argument("--out", "-o", type=Path, help="Path to write final report.json to")
    mark_parser.add_argument(
        "--profile",
        choices=["frontend", "fullstack"],
        default="frontend",
        help="Profile to score against",
    )

    eval_parser = subparsers.add_parser("eval", help="Run evaluation harness across fixtures")
    eval_parser.add_argument("--fixtures", type=Path, default=Path("fixtures"), help="Path to fixtures root")
    eval_parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="Path to write evaluation outputs to (default ams_eval_runs/<timestamp>)",
    )
    eval_parser.add_argument(
        "--profile",
        choices=["frontend", "fullstack", "all"],
        default="all",
        help="Limit evaluation to a profile",
    )

    batch_parser = subparsers.add_parser(
        "batch",
        help="Run marking over a folder of submissions and produce cohort analytics",
    )
    batch_parser.add_argument("submissions_dir", type=Path, help="Folder containing submission dirs or .zip files")
    batch_parser.add_argument("--profile", choices=["frontend", "fullstack"], required=True)
    batch_parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="Output directory (default ams_batch_runs/<timestamp>)",
    )

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
        report_path = pipeline.run(submission_path=submission_path, workspace_path=workspace_path, profile=args.profile)

        # If an explicit output path is requested, copy report there
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(report_path.read_bytes())
            report_path = out_path

        print(f"Report written to {report_path}")
        return
    elif args.command == "eval":
        from .evaluation import evaluate

        if args.out:
            out_root = Path(args.out)
            out_root.mkdir(parents=True, exist_ok=True)
        else:
            base = Path.cwd() / "ams_eval_runs"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_root = base / timestamp
            out_root.mkdir(parents=True, exist_ok=True)

        summary = evaluate(fixtures_root=Path(args.fixtures), out_root=out_root, profile=args.profile)
        total, passed, failed = summary["total"], summary["passed"], summary["failed"]
        print(f"Evaluation complete. Total: {total}, Passed: {passed}, Failed: {failed}")
        if failed:
            print("Failing cases:")
            for entry in summary["failing_cases"]:
                reasons = "; ".join(entry["reasons"])
                print(f"- {entry['profile']}/{entry['case']}: {reasons}")
        raise SystemExit(0 if failed == 0 else 1)
    elif args.command == "batch":
        from .batch import run_batch

        if args.out:
            out_root = Path(args.out)
            out_root.mkdir(parents=True, exist_ok=True)
        else:
            base = Path.cwd() / "ams_batch_runs"
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_root = base / timestamp
            out_root.mkdir(parents=True, exist_ok=True)

        result = run_batch(
            submissions_dir=Path(args.submissions_dir),
            out_root=out_root,
            profile=args.profile,
            keep_individual_runs=True,
        )
        failed = result["summary"]["failed"]
        raise SystemExit(0 if failed == 0 else 1)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
