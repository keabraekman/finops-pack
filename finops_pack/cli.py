"""CLI entry point for finops_pack."""

from __future__ import annotations

import argparse
import json
from typing import Any

from finops_pack.aws.assume_role import assume_role_session


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="finops-pack",
        description="Starter CLI for the finops_pack project.",
    )

    parser.add_argument(
        "--role-arn",
        help="AWS IAM role ARN to assume.",
    )
    parser.add_argument(
        "--external-id",
        help="External ID to use when assuming the role.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region to use (default: us-east-1).",
    )
    parser.add_argument(
        "--session-name",
        default="finops-pack",
        help="STS session name (default: finops-pack).",
    )
    parser.add_argument(
        "--check-identity",
        action="store_true",
        help="Call STS GetCallerIdentity after assuming the role.",
    )

    return parser


def main() -> None:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.role_arn:
        print("finops-pack CLI is set up.")
        print("Pass --role-arn and --external-id to test assume-role.")
        return

    session = assume_role_session(
        role_arn=args.role_arn,
        external_id=args.external_id,
        session_name=args.session_name,
        region_name=args.region,
    )

    print("Successfully assumed role.")

    if args.check_identity:
        sts = session.client("sts")
        identity: dict[str, Any] = sts.get_caller_identity()
        print(json.dumps(identity, indent=2, default=str))


if __name__ == "__main__":
    main()