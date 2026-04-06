"""AWS session factory helpers."""

from __future__ import annotations

from typing import Any

from finops_pack.integrations.aws.assume_role import assume_role_session


def build_assumed_role_session(
    *,
    role_arn: str,
    external_id: str,
    session_name: str,
    region_name: str = "us-east-1",
) -> Any:
    """Return a boto3 session for a customer-provided cross-account role."""
    return assume_role_session(
        role_arn=role_arn,
        external_id=external_id,
        session_name=session_name,
        region_name=region_name,
    )

