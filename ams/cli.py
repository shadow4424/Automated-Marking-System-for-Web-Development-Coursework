from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from ams.core.pipeline import AssessmentPipeline
from ams.core.profiles import get_visible_profile_specs


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
    from ams.core.profiles import list_profile_names
    profile_choices = list_profile_names(include_aliases=True, visible_only=True) + ["custom_profile"]
    parser = argparse.ArgumentParser(prog="ams", description="Automated Marking System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mark_parser = subparsers.add_parser("mark", help="Run assessment on a submission")
    mark_parser.add_argument("submission_path", type=Path, help="Path to submission directory")
    mark_parser.add_argument("--workspace", "-w", type=Path, help="Path to workspace directory (persistent)")
    mark_parser.add_argument("--out", "-o", type=Path, help="Path to write final report.json to")
    mark_parser.add_argument(
        "--profile",
        choices=profile_choices,
        default="frontend_interactive",
        help="Profile to score against",
    )
    mark_parser.add_argument(
        "--profile-config",
        type=Path,
        help="Path to a custom profile JSON file when using --profile custom_profile",
    )

    batch_parser = subparsers.add_parser(
        "batch",
        help="Run marking over a folder of submissions and produce batch reports",
    )
    batch_parser.add_argument("submissions_dir", type=Path, help="Folder containing submission dirs or .zip files")
    batch_parser.add_argument("--profile", choices=profile_choices, required=True)
    batch_parser.add_argument(
        "--profile-config",
        type=Path,
        help="Path to a custom profile JSON file when using --profile custom_profile",
    )
    batch_parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="Output directory (default ams_batch_runs/<timestamp>)",
    )

    subparsers.add_parser("demo", help="Build and run a full demo assessment")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _create_parser()
    args = parser.parse_args(argv)
    if getattr(args, "profile", None) == "custom_profile" and not getattr(args, "profile_config", None):
        parser.error("--profile custom_profile requires --profile-config")

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
        metadata = {}
        if args.profile_config:
            metadata["profile_config_path"] = str(args.profile_config)
        report_path = pipeline.run(
            submission_path=submission_path,
            workspace_path=workspace_path,
            profile=args.profile,
            metadata=metadata or None,
        )

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
            profile_config_path=str(args.profile_config) if args.profile_config else None,
            keep_individual_runs=True,
        )
        failed = sum(
            1
            for record in result.get("records", [])
            if str(record.get("status") or "").lower() in {"error", "failed"}
        )
        raise SystemExit(0 if failed == 0 else 1)
    elif args.command == "demo":
        from .tools.demo_full_system import run_demo

        success = run_demo()
        raise SystemExit(0 if success else 1)
    else:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
