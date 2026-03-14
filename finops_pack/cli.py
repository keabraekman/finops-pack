"""CLI entry point for finops_pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.aws.cost_optimization_hub import enable_cost_optimization_hub
from finops_pack.config import load_config, merge_run_config


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="finops-pack",
        description="Starter CLI for the finops_pack project.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run finops-pack against AWS.",
    )
    run_parser.add_argument(
        "--role-arn",
        help="AWS IAM role ARN to assume.",
    )
    run_parser.add_argument(
        "--external-id",
        help="External ID to use when assuming the role.",
    )
    run_parser.add_argument(
        "--region",
        help="AWS region to use (default: us-east-1).",
    )
    run_parser.add_argument(
        "--session-name",
        help="STS session name (default: finops-pack).",
    )
    run_parser.add_argument(
        "--check-identity",
        action="store_true",
        help="Call STS GetCallerIdentity after assuming the role.",
    )
    run_parser.add_argument(
        "--config",
        help="Optional path to config.yaml.",
    )
    run_parser.add_argument(
        "--enable-coh",
        action="store_true",
        help=(
            "Enable Cost Optimization Hub in the target account. "
            "Requires extra IAM permissions."
        ),
    )

    demo_parser = subparsers.add_parser(
        "demo",
        help="Run finops-pack in demo mode using fixture data.",
    )
    demo_parser.add_argument(
        "--config",
        help="Optional path to config.yaml.",
    )

    return parser


def handle_run(args: argparse.Namespace) -> int:
    """Handle the run subcommand."""
    file_config = load_config(args.config)
    resolved = merge_run_config(
        file_config,
        role_arn=args.role_arn,
        external_id=args.external_id,
        region=args.region,
        session_name=args.session_name,
        check_identity=args.check_identity,
        enable_coh=args.enable_coh,
    )
    if resolved.role_arn is None:
        raise RuntimeError("role_arn is required after config resolution.")

    session = assume_role_session(
        role_arn=resolved.role_arn,
        external_id=resolved.external_id,
        session_name=resolved.session_name,
        region_name=resolved.region,
    )

    print("Running finops-pack in AWS mode")
    print(f"role_arn={resolved.role_arn}")
    print(f"external_id={resolved.external_id}")
    print(f"region={resolved.region}")
    print(f"session_name={resolved.session_name}")
    print(f"enable_coh={resolved.enable_coh}")

    if resolved.check_identity:
        sts = session.client("sts")
        identity: dict[str, Any] = sts.get_caller_identity()
        print(json.dumps(identity, indent=2, default=str))

    if resolved.enable_coh:
        status = enable_cost_optimization_hub(session, region_name=resolved.region)
        print(f"cost_optimization_hub_status={status}")

    return 0


def handle_demo(args: argparse.Namespace) -> int:
    """Handle the demo subcommand."""
    file_config = load_config(args.config)
    fixture_dir = Path(file_config.demo_fixture_dir)

    print("Running finops-pack in demo mode")
    print(f"fixture_dir={fixture_dir}")

    return 0


def main() -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "run":
            return handle_run(args)
        if args.command == "demo":
            return handle_demo(args)

        parser.error(f"Unknown command: {args.command}")
        return 2
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
