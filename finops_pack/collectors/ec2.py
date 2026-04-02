"""Best-effort EC2 inventory collection across accounts and regions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.collectors.cloudwatch import collect_single_stat_metric
from finops_pack.models import AccountRecord

ROLE_ARN_PATTERN = re.compile(
    r"^(?P<prefix>arn:(?P<partition>[^:]+):iam::)(?P<account_id>\d{12})(?P<suffix>:role/.+)$"
)
DEFAULT_PARTITION = "aws"


def derive_account_role_arn(role_arn: str, target_account_id: str) -> str:
    """Return the same role ARN shape pointed at a different AWS account."""
    match = ROLE_ARN_PATTERN.match(role_arn)
    if match is None:
        raise ValueError(f"role_arn is not a supported IAM role ARN: {role_arn}")
    return f"{match.group('prefix')}{target_account_id}{match.group('suffix')}"


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


def _serialize_launch_time(raw_launch_time: Any) -> str | None:
    if isinstance(raw_launch_time, datetime):
        return raw_launch_time.astimezone(UTC).isoformat()
    return None


def _normalize_instance(
    raw_instance: dict[str, Any],
    *,
    account_record: AccountRecord,
    region_name: str,
    partition: str,
) -> dict[str, Any] | None:
    instance_id = raw_instance.get("InstanceId")
    if not isinstance(instance_id, str) or not instance_id:
        return None

    tags = _normalize_tags(raw_instance.get("Tags"))
    state = raw_instance.get("State", {})
    placement = raw_instance.get("Placement", {})
    instance_arn = (
        f"arn:{partition}:ec2:{region_name}:{account_record.account_id}:instance/{instance_id}"
    )

    return {
        "accountId": account_record.account_id,
        "accountName": account_record.name,
        "region": region_name,
        "instanceId": instance_id,
        "instanceArn": instance_arn,
        "name": tags.get("Name", ""),
        "state": state.get("Name", "") if isinstance(state, dict) else "",
        "instanceType": raw_instance.get("InstanceType", ""),
        "platform": raw_instance.get("Platform", ""),
        "platformDetails": raw_instance.get("PlatformDetails", ""),
        "availabilityZone": (
            placement.get("AvailabilityZone", "") if isinstance(placement, dict) else ""
        ),
        "rootDeviceType": raw_instance.get("RootDeviceType", ""),
        "lifecycle": raw_instance.get("InstanceLifecycle", ""),
        "launchTime": _serialize_launch_time(raw_instance.get("LaunchTime")) or "",
        "privateIpAddress": raw_instance.get("PrivateIpAddress", ""),
        "publicIpAddress": raw_instance.get("PublicIpAddress", ""),
        "vpcId": raw_instance.get("VpcId", ""),
        "subnetId": raw_instance.get("SubnetId", ""),
        "tags": tags,
    }


def _collect_region_instances(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
    partition: str,
) -> list[dict[str, Any]]:
    client = session.client("ec2", region_name=region_name)
    paginator = client.get_paginator("describe_instances")
    collected: list[dict[str, Any]] = []

    try:
        for page in paginator.paginate():
            reservations = page.get("Reservations", [])
            if not isinstance(reservations, list):
                continue
            for reservation in reservations:
                if not isinstance(reservation, dict):
                    continue
                instances = reservation.get("Instances", [])
                if not isinstance(instances, list):
                    continue
                for raw_instance in instances:
                    if not isinstance(raw_instance, dict):
                        continue
                    normalized = _normalize_instance(
                        raw_instance,
                        account_record=account_record,
                        region_name=region_name,
                        partition=partition,
                    )
                    if normalized is not None:
                        if normalized.get("state") == "running":
                            instance_id = normalized["instanceId"]
                            dimensions = [{"Name": "InstanceId", "Value": instance_id}]
                            normalized["avgCpuUtilization14d"] = collect_single_stat_metric(
                                session,
                                region_name=region_name,
                                namespace="AWS/EC2",
                                metric_name="CPUUtilization",
                                dimensions=dimensions,
                                statistic="Average",
                            )
                            normalized["maxCpuUtilization14d"] = collect_single_stat_metric(
                                session,
                                region_name=region_name,
                                namespace="AWS/EC2",
                                metric_name="CPUUtilization",
                                dimensions=dimensions,
                                statistic="Maximum",
                            )
                        collected.append(normalized)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to describe EC2 instances in {account_record.account_id}/{region_name}: {exc}"
        ) from exc

    return collected


def collect_ec2_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect EC2 instances across accounts and regions without failing the whole run."""
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
                    _collect_region_instances(
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

    items.sort(key=lambda item: (item["accountId"], item["region"], item["instanceId"]))
    return {
        "operation": "DescribeInstances",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
