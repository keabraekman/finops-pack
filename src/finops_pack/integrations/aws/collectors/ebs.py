"""Best-effort EBS inventory collection across accounts and regions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.domain.models import AccountRecord
from finops_pack.integrations.aws.assume_role import assume_role_session
from finops_pack.integrations.aws.collectors.ec2 import derive_account_role_arn

ROLE_ARN_PATTERN = re.compile(
    r"^(?P<prefix>arn:(?P<partition>[^:]+):iam::)(?P<account_id>\d{12})(?P<suffix>:role/.+)$"
)
DEFAULT_PARTITION = "aws"


def _extract_role_arn_details(role_arn: str) -> tuple[str, str | None]:
    match = ROLE_ARN_PATTERN.match(role_arn)
    if match is None:
        return DEFAULT_PARTITION, None
    return match.group("partition"), match.group("account_id")


def _normalize_tags(raw_tags: Any) -> dict[str, str]:
    tags: dict[str, str] = {}
    if not isinstance(raw_tags, list):
        return tags

    for raw_tag in raw_tags:
        if not isinstance(raw_tag, dict):
            continue
        key = raw_tag.get("Key")
        value = raw_tag.get("Value")
        if not isinstance(key, str) or not key.strip():
            continue
        tags[key] = value if isinstance(value, str) else ""

    return tags


def _serialize_time(raw_time: Any) -> str | None:
    if isinstance(raw_time, datetime):
        return raw_time.astimezone(UTC).isoformat()
    return None


def _normalize_volume(
    raw_volume: dict[str, Any],
    *,
    account_record: AccountRecord,
    region_name: str,
    partition: str,
) -> dict[str, Any] | None:
    volume_id = raw_volume.get("VolumeId")
    if not isinstance(volume_id, str) or not volume_id:
        return None

    attachments = raw_volume.get("Attachments", [])
    if not isinstance(attachments, list):
        attachments = []
    attached_instance_ids = [
        attachment["InstanceId"]
        for attachment in attachments
        if isinstance(attachment, dict) and isinstance(attachment.get("InstanceId"), str)
    ]
    availability_zone = raw_volume.get("AvailabilityZone")
    volume_arn = f"arn:{partition}:ec2:{region_name}:{account_record.account_id}:volume/{volume_id}"
    tags = _normalize_tags(raw_volume.get("Tags"))

    return {
        "accountId": account_record.account_id,
        "accountName": account_record.name,
        "region": region_name,
        "availabilityZone": availability_zone if isinstance(availability_zone, str) else "",
        "volumeId": volume_id,
        "volumeArn": volume_arn,
        "name": tags.get("Name", ""),
        "state": raw_volume.get("State", ""),
        "volumeType": raw_volume.get("VolumeType", ""),
        "sizeGiB": raw_volume.get("Size", 0),
        "iops": raw_volume.get("Iops", 0),
        "throughput": raw_volume.get("Throughput", 0),
        "encrypted": bool(raw_volume.get("Encrypted")),
        "createTime": _serialize_time(raw_volume.get("CreateTime")) or "",
        "attachmentCount": len(attached_instance_ids),
        "attachedInstanceIds": attached_instance_ids,
        "tags": tags,
    }


def _collect_region_volumes(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
    partition: str,
) -> list[dict[str, Any]]:
    client = session.client("ec2", region_name=region_name)
    paginator = client.get_paginator("describe_volumes")
    collected: list[dict[str, Any]] = []

    try:
        for page in paginator.paginate():
            raw_volumes = page.get("Volumes", [])
            if not isinstance(raw_volumes, list):
                continue
            for raw_volume in raw_volumes:
                if not isinstance(raw_volume, dict):
                    continue
                normalized = _normalize_volume(
                    raw_volume,
                    account_record=account_record,
                    region_name=region_name,
                    partition=partition,
                )
                if normalized is not None:
                    collected.append(normalized)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to describe EBS volumes in {account_record.account_id}/{region_name}: {exc}"
        ) from exc

    return collected


def collect_ebs_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect EBS volumes across accounts and regions without failing the whole run."""
    partition, role_arn_account_id = _extract_role_arn_details(role_arn)
    normalized_regions = list(dict.fromkeys(region for region in regions if region))
    reusable_account_ids = {
        account_id for account_id in (current_account_id, role_arn_account_id) if account_id
    }

    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for account_record in account_records:
        if account_record.status.upper() != "ACTIVE":
            errors.append(
                {
                    "scope": "account",
                    "accountId": account_record.account_id,
                    "accountName": account_record.name,
                    "region": "",
                    "error": f"Skipped because account status is {account_record.status}.",
                }
            )
            continue

        account_session = session
        if account_record.account_id not in reusable_account_ids:
            try:
                account_session = assume_role_session(
                    role_arn=derive_account_role_arn(role_arn, account_record.account_id),
                    external_id=external_id,
                    session_name=session_name,
                    region_name=normalized_regions[0] if normalized_regions else "us-east-1",
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

        for region_name in normalized_regions:
            try:
                items.extend(
                    _collect_region_volumes(
                        account_session,
                        account_record=account_record,
                        region_name=region_name,
                        partition=partition,
                    )
                )
            except RuntimeError as exc:
                errors.append(
                    {
                        "scope": "region",
                        "accountId": account_record.account_id,
                        "accountName": account_record.name,
                        "region": region_name,
                        "error": str(exc),
                    }
                )

    items.sort(key=lambda item: (item["accountId"], item["region"], item["volumeId"]))
    return {
        "operation": "DescribeVolumes",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
