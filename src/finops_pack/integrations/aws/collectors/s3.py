"""Best-effort S3 inventory collection across accounts."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.domain.models import AccountRecord
from finops_pack.integrations.aws.assume_role import assume_role_session
from finops_pack.integrations.aws.collectors.cloudwatch import (
    S3_STORAGE_WINDOW_DAYS,
    collect_single_stat_metric,
)
from finops_pack.integrations.aws.collectors.ec2 import derive_account_role_arn

S3_CLOUDWATCH_REGION = "us-east-1"


def _bucket_region(s3_client: Any, bucket_name: str) -> str:
    try:
        response = s3_client.get_bucket_location(Bucket=bucket_name)
    except (ClientError, BotoCoreError):
        return "us-east-1"
    location = response.get("LocationConstraint")
    if location in (None, "", "EU"):
        return "us-east-1" if location in (None, "") else "eu-west-1"
    return str(location)


def _bucket_lifecycle_summary(s3_client: Any, bucket_name: str) -> tuple[bool, int]:
    try:
        response = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "NoSuchLifecycleConfiguration",
            "NoSuchBucket",
            "AccessDenied",
        }:
            return False, 0
        return False, 0
    except BotoCoreError:
        return False, 0

    rules = response.get("Rules", [])
    if not isinstance(rules, list):
        return False, 0
    return bool(rules), len(rules)


def _collect_account_buckets(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
) -> list[dict[str, Any]]:
    s3_client = session.client("s3")
    response = s3_client.list_buckets()
    buckets = response.get("Buckets", [])
    if not isinstance(buckets, list):
        return []

    collected: list[dict[str, Any]] = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        bucket_name = bucket.get("Name")
        if not isinstance(bucket_name, str) or not bucket_name:
            continue
        region_name = _bucket_region(s3_client, bucket_name)
        has_lifecycle, lifecycle_rule_count = _bucket_lifecycle_summary(s3_client, bucket_name)
        dimensions = [
            {"Name": "BucketName", "Value": bucket_name},
            {"Name": "StorageType", "Value": "StandardStorage"},
        ]
        standard_storage_bytes = collect_single_stat_metric(
            session,
            region_name=S3_CLOUDWATCH_REGION,
            namespace="AWS/S3",
            metric_name="BucketSizeBytes",
            dimensions=dimensions,
            statistic="Average",
            period=86400,
            days=S3_STORAGE_WINDOW_DAYS,
        )
        collected.append(
            {
                "accountId": account_record.account_id,
                "accountName": account_record.name,
                "region": region_name,
                "bucketName": bucket_name,
                "bucketArn": f"arn:aws:s3:::{bucket_name}",
                "hasLifecycleRules": has_lifecycle,
                "lifecycleRuleCount": lifecycle_rule_count,
                "standardStorageGiB": round((standard_storage_bytes or 0.0) / (1024**3), 2),
            }
        )
    return collected


def collect_s3_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect S3 buckets across accounts without failing the whole run."""
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    reusable_account_ids = {account_id for account_id in (current_account_id,) if account_id}

    for account_record in account_records:
        if account_record.status.upper() != "ACTIVE":
            continue
        account_session = session
        if account_record.account_id not in reusable_account_ids:
            try:
                account_session = assume_role_session(
                    role_arn=derive_account_role_arn(role_arn, account_record.account_id),
                    external_id=external_id,
                    session_name=session_name,
                    region_name="us-east-1",
                )
            except (RuntimeError, ValueError) as exc:
                errors.append(
                    {
                        "scope": "account",
                        "accountId": account_record.account_id,
                        "accountName": account_record.name,
                        "region": "",
                        "error": str(exc),
                    }
                )
                continue
        try:
            items.extend(_collect_account_buckets(account_session, account_record=account_record))
        except (ClientError, BotoCoreError, RuntimeError) as exc:
            errors.append(
                {
                    "scope": "account",
                    "accountId": account_record.account_id,
                    "accountName": account_record.name,
                    "region": "",
                    "error": str(exc),
                }
            )

    items.sort(key=lambda item: (item["accountId"], item["bucketName"]))
    return {
        "operation": "ListBuckets",
        "regions": ["global"],
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
