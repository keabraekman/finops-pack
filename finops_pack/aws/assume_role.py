"""Helpers for assuming an AWS IAM role."""

from __future__ import annotations

from typing import Any, TypedDict

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class AssumedRoleCredentials(TypedDict):
    """Temporary AWS credentials returned from STS AssumeRole."""

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str


def assume_role(
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    region_name: str = "us-east-1",
) -> AssumedRoleCredentials:
    """
    Assume an IAM role and return temporary credentials.

    Args:
        role_arn: ARN of the role to assume.
        external_id: Optional external ID required by the target account.
        session_name: STS session name.
        region_name: AWS region for the STS client.

    Returns:
        Temporary AWS credentials for the assumed role.

    Raises:
        RuntimeError: If STS AssumeRole fails or returns incomplete credentials.
    """
    sts = boto3.client("sts", region_name=region_name)

    assume_role_kwargs: dict[str, Any] = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
    }

    if external_id:
        assume_role_kwargs["ExternalId"] = external_id

    try:
        response = sts.assume_role(**assume_role_kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to assume role {role_arn}: {exc}") from exc

    credentials = response.get("Credentials")
    if not credentials:
        raise RuntimeError("AssumeRole response did not include credentials.")

    try:
        return {
            "aws_access_key_id": credentials["AccessKeyId"],
            "aws_secret_access_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }
    except KeyError as exc:
        raise RuntimeError("AssumeRole response did not include complete credentials.") from exc


def assume_role_session(
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    region_name: str = "us-east-1",
) -> boto3.Session:
    """
    Assume an IAM role and return a boto3 Session using temporary credentials.

    Args:
        role_arn: ARN of the role to assume.
        external_id: Optional external ID required by the target account.
        session_name: STS session name.
        region_name: AWS region for the returned session.

    Returns:
        A boto3 Session authenticated with temporary assumed-role credentials.

    Raises:
        RuntimeError: If STS AssumeRole fails or returns incomplete credentials.
    """
    credentials = assume_role(
        role_arn=role_arn,
        external_id=external_id,
        session_name=session_name,
        region_name=region_name,
    )

    return boto3.Session(region_name=region_name, **credentials)
