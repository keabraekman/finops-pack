"""Helpers for enabling Cost Optimization Hub."""

from __future__ import annotations

from typing import Literal, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

EnrollmentStatus = Literal["Active", "Inactive"]


def update_enrollment_status(
    session: boto3.Session,
    *,
    status: EnrollmentStatus,
    region_name: str = "us-east-1",
    include_member_accounts: bool = False,
) -> EnrollmentStatus:
    """Update the Cost Optimization Hub enrollment status for the current account."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    update_kwargs: dict[str, object] = {"status": status}
    if include_member_accounts:
        update_kwargs["includeMemberAccounts"] = True

    try:
        response = client.update_enrollment_status(**update_kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to update Cost Optimization Hub enrollment status to {status}: {exc}"
        ) from exc

    resolved_status = response.get("status")
    if resolved_status not in {"Active", "Inactive"}:
        raise RuntimeError("UpdateEnrollmentStatus response did not include a valid status.")

    return cast(EnrollmentStatus, resolved_status)


def enable_cost_optimization_hub(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
) -> EnrollmentStatus:
    """Enable Cost Optimization Hub for the current account."""
    return update_enrollment_status(session, status="Active", region_name=region_name)
