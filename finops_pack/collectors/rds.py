"""Best-effort RDS inventory collection across accounts and regions."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.collectors.ec2 import derive_account_role_arn
from finops_pack.models import AccountRecord

ROLE_ARN_PATTERN = re.compile(
    r"^(?P<prefix>arn:(?P<partition>[^:]+):iam::)(?P<account_id>\d{12})(?P<suffix>:role/.+)$"
)
DEFAULT_PARTITION = "aws"


def _extract_role_arn_details(role_arn: str) -> tuple[str, str | None]:
    match = ROLE_ARN_PATTERN.match(role_arn)
    if match is None:
        return DEFAULT_PARTITION, None
    return match.group("partition"), match.group("account_id")


def _serialize_time(raw_time: Any) -> str | None:
    if isinstance(raw_time, datetime):
        return raw_time.astimezone(UTC).isoformat()
    return None


def _normalize_instance(
    raw_instance: dict[str, Any],
    *,
    account_record: AccountRecord,
    region_name: str,
) -> dict[str, Any] | None:
    db_instance_identifier = raw_instance.get("DBInstanceIdentifier")
    if not isinstance(db_instance_identifier, str) or not db_instance_identifier:
        return None

    read_replicas = raw_instance.get("ReadReplicaDBInstanceIdentifiers", [])
    if not isinstance(read_replicas, list):
        read_replicas = []

    return {
        "accountId": account_record.account_id,
        "accountName": account_record.name,
        "region": region_name,
        "dbInstanceIdentifier": db_instance_identifier,
        "dbInstanceArn": raw_instance.get("DBInstanceArn", ""),
        "dbInstanceClass": raw_instance.get("DBInstanceClass", ""),
        "engine": raw_instance.get("Engine", ""),
        "engineVersion": raw_instance.get("EngineVersion", ""),
        "status": raw_instance.get("DBInstanceStatus", ""),
        "multiAz": bool(raw_instance.get("MultiAZ")),
        "storageType": raw_instance.get("StorageType", ""),
        "allocatedStorage": raw_instance.get("AllocatedStorage", 0),
        "iops": raw_instance.get("Iops", 0),
        "publiclyAccessible": bool(raw_instance.get("PubliclyAccessible")),
        "storageEncrypted": bool(raw_instance.get("StorageEncrypted")),
        "backupRetentionPeriod": raw_instance.get("BackupRetentionPeriod", 0),
        "dbClusterIdentifier": raw_instance.get("DBClusterIdentifier", ""),
        "readReplicaSourceDBInstanceIdentifier": raw_instance.get(
            "ReadReplicaSourceDBInstanceIdentifier",
            "",
        ),
        "readReplicaDBInstanceIdentifiers": [
            identifier for identifier in read_replicas if isinstance(identifier, str)
        ],
        "instanceCreateTime": _serialize_time(raw_instance.get("InstanceCreateTime")) or "",
    }


def _collect_region_instances(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
) -> list[dict[str, Any]]:
    client = session.client("rds", region_name=region_name)
    paginator = client.get_paginator("describe_db_instances")
    collected: list[dict[str, Any]] = []

    try:
        for page in paginator.paginate():
            raw_instances = page.get("DBInstances", [])
            if not isinstance(raw_instances, list):
                continue
            for raw_instance in raw_instances:
                if not isinstance(raw_instance, dict):
                    continue
                normalized = _normalize_instance(
                    raw_instance,
                    account_record=account_record,
                    region_name=region_name,
                )
                if normalized is not None:
                    collected.append(normalized)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to describe RDS instances in {account_record.account_id}/{region_name}: {exc}"
        ) from exc

    return collected


def collect_rds_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect RDS DB instances across accounts and regions without failing the whole run."""
    _, role_arn_account_id = _extract_role_arn_details(role_arn)
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

    items.sort(
        key=lambda item: (item["accountId"], item["region"], item["dbInstanceIdentifier"])
    )
    return {
        "operation": "DescribeDBInstances",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
