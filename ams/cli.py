from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline


def _print_sandbox_banner() -> None:
    """Print the sandbox status to stderr so it's always visible."""
    from ams.sandbox.config import get_sandbox_status, get_sandbox_config, SandboxMode

    status = get_sandbox_status()
    cfg = get_sandbox_config()

    if status["enforced"]:
        print(f"\033[32m[Sandbox] ACTIVE — {status['message']}\033[0m", file=sys.stderr)
    elif cfg.mode == SandboxMode.DOCKER:
        # Docker required but not available — fatal
        print(
            f"\033[31m[Sandbox] ERROR — {status['message']}\033[0m",
            file=sys.stderr,
        )
        print(
            "\033[31mCannot run without Docker sandbox. "
            "Start Docker and build the image (docker/build.sh), "
            "or set AMS_SANDBOX_MODE=subprocess to bypass.\033[0m",
            file=sys.stderr,
        )
        raise SystemExit(1)
    else:
        # Explicit subprocess mode
        print(
            f"\033[33m[Sandbox] WARNING — {status['message']}\033[0m",
            file=sys.stderr,
        )


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

    export_parser = subparsers.add_parser("export-figures", help="Export figures/tables from a batch run")
    export_parser.add_argument("--run-id", required=True, help="Run ID of batch analytics")
    export_parser.add_argument("--runs-root", type=Path, default=Path("ams_batch_runs"), help="Root directory containing batch runs")
    export_parser.add_argument("--out", "-o", type=Path, default=Path("figures"), help="Output directory for figures/tables")

    subparsers.add_parser("demo", help="Build and run a full demo (batch, analytics, figures, evaluation)")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _create_parser()
    args = parser.parse_args(argv)

    # ── Sandbox enforcement — always check before any pipeline work ──
    _print_sandbox_banner()

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
    elif args.command == "batch":
        from ams.tools.batch import run_batch

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
    elif args.command == "export-figures":
        from .tools.export_figures import export_figures

        export_figures(run_id=args.run_id, runs_root=Path(args.runs_root), out_dir=Path(args.out))
        print(f"Figures exported to {args.out}")
        return
    elif args.command == "demo":
        from .tools.demo_full_system import run_demo

        success = run_demo()
        raise SystemExit(0 if success else 1)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
