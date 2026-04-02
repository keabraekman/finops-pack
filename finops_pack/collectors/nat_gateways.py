# ruff: noqa: E501
"""Best-effort NAT Gateway inventory collection across accounts and regions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.collectors.cloudwatch import collect_sum_metric
from finops_pack.collectors.ec2 import derive_account_role_arn
from finops_pack.models import AccountRecord


def _serialize_time(raw_value: Any) -> str | None:
    if isinstance(raw_value, datetime):
        return raw_value.astimezone(UTC).isoformat()
    return None


def _collect_region_nat_gateways(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
) -> list[dict[str, Any]]:
    client = session.client("ec2", region_name=region_name)
    paginator = client.get_paginator("describe_nat_gateways")
    collected: list[dict[str, Any]] = []
    for page in paginator.paginate():
        nat_gateways = page.get("NatGateways", [])
        if not isinstance(nat_gateways, list):
            continue
        for gateway in nat_gateways:
            if not isinstance(gateway, dict):
                continue
            nat_gateway_id = gateway.get("NatGatewayId")
            if not isinstance(nat_gateway_id, str) or not nat_gateway_id:
                continue
            dimensions = [{"Name": "NatGatewayId", "Value": nat_gateway_id}]
            bytes_out = collect_sum_metric(
                session,
                region_name=region_name,
                namespace="AWS/NATGateway",
                metric_name="BytesOutToDestination",
                dimensions=dimensions,
            )
            bytes_in = collect_sum_metric(
                session,
                region_name=region_name,
                namespace="AWS/NATGateway",
                metric_name="BytesInFromSource",
                dimensions=dimensions,
            )
            collected.append(
                {
                    "accountId": account_record.account_id,
                    "accountName": account_record.name,
                    "region": region_name,
                    "natGatewayId": nat_gateway_id,
                    "natGatewayArn": f"arn:aws:ec2:{region_name}:{account_record.account_id}:natgateway/{nat_gateway_id}",
                    "state": gateway.get("State", ""),
                    "vpcId": gateway.get("VpcId", ""),
                    "subnetId": gateway.get("SubnetId", ""),
                    "connectivityType": gateway.get("ConnectivityType", ""),
                    "createTime": _serialize_time(gateway.get("CreateTime")) or "",
                    "avgBytesOut14d": bytes_out,
                    "avgBytesIn14d": bytes_in,
                }
            )
    return collected


def collect_nat_gateway_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect NAT gateways across accounts and regions without failing the whole run."""
    normalized_regions = list(dict.fromkeys(region for region in regions if region))
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
                    _collect_region_nat_gateways(
                        account_session,
                        account_record=account_record,
                        region_name=region_name,
                    )
                )
            except (ClientError, BotoCoreError, RuntimeError) as exc:
                errors.append(
                    {
                        "scope": "region",
                        "accountId": account_record.account_id,
                        "accountName": account_record.name,
                        "region": region_name,
                        "error": str(exc),
                    }
                )

    items.sort(key=lambda item: (item["accountId"], item["region"], item["natGatewayId"]))
    return {
        "operation": "DescribeNatGateways",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
