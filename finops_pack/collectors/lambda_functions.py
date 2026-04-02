"""Best-effort Lambda inventory collection across accounts and regions."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.collectors.cloudwatch import collect_single_stat_metric, collect_sum_metric
from finops_pack.collectors.ec2 import derive_account_role_arn
from finops_pack.models import AccountRecord


def _collect_region_functions(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
) -> list[dict[str, Any]]:
    client = session.client("lambda", region_name=region_name)
    paginator = client.get_paginator("list_functions")
    collected: list[dict[str, Any]] = []
    for page in paginator.paginate():
        functions = page.get("Functions", [])
        if not isinstance(functions, list):
            continue
        for function in functions:
            if not isinstance(function, dict):
                continue
            function_name = function.get("FunctionName")
            if not isinstance(function_name, str) or not function_name:
                continue
            dimensions = [{"Name": "FunctionName", "Value": function_name}]
            collected.append(
                {
                    "accountId": account_record.account_id,
                    "accountName": account_record.name,
                    "region": region_name,
                    "functionName": function_name,
                    "functionArn": function.get("FunctionArn", ""),
                    "runtime": function.get("Runtime", ""),
                    "architectures": function.get("Architectures", []),
                    "memorySize": int(function.get("MemorySize") or 0),
                    "timeout": int(function.get("Timeout") or 0),
                    "avgDurationMs14d": collect_single_stat_metric(
                        session,
                        region_name=region_name,
                        namespace="AWS/Lambda",
                        metric_name="Duration",
                        dimensions=dimensions,
                        statistic="Average",
                    ),
                    "monthlyInvocations14d": collect_sum_metric(
                        session,
                        region_name=region_name,
                        namespace="AWS/Lambda",
                        metric_name="Invocations",
                        dimensions=dimensions,
                    ),
                    "monthlyErrors14d": collect_sum_metric(
                        session,
                        region_name=region_name,
                        namespace="AWS/Lambda",
                        metric_name="Errors",
                        dimensions=dimensions,
                    ),
                }
            )
    return collected


def collect_lambda_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect Lambda functions across accounts and regions without failing the whole run."""
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
                    _collect_region_functions(
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

    items.sort(key=lambda item: (item["accountId"], item["region"], item["functionName"]))
    return {
        "operation": "ListFunctions",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
