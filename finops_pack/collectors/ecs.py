"""Best-effort ECS service inventory collection across accounts and regions."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.aws.assume_role import assume_role_session
from finops_pack.collectors.cloudwatch import collect_single_stat_metric
from finops_pack.collectors.ec2 import derive_account_role_arn
from finops_pack.models import AccountRecord


def _collect_region_services(
    session: boto3.Session,
    *,
    account_record: AccountRecord,
    region_name: str,
) -> list[dict[str, Any]]:
    client = session.client("ecs", region_name=region_name)
    cluster_arns: list[str] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_clusters(**kwargs)
        cluster_arns.extend(
            item for item in response.get("clusterArns", []) if isinstance(item, str)
        )
        raw_next_token = response.get("nextToken")
        if not isinstance(raw_next_token, str) or not raw_next_token:
            break
        next_token = raw_next_token

    collected: list[dict[str, Any]] = []
    task_definition_cache: dict[str, dict[str, Any]] = {}
    for cluster_arn in cluster_arns:
        cluster_name = cluster_arn.rsplit("/", 1)[-1]
        service_arns: list[str] = []
        next_token = None
        while True:
            kwargs = {"cluster": cluster_arn}
            if next_token:
                kwargs["nextToken"] = next_token
            response = client.list_services(**kwargs)
            service_arns.extend(
                item for item in response.get("serviceArns", []) if isinstance(item, str)
            )
            raw_next_token = response.get("nextToken")
            if not isinstance(raw_next_token, str) or not raw_next_token:
                break
            next_token = raw_next_token

        for start in range(0, len(service_arns), 10):
            chunk = service_arns[start : start + 10]
            if not chunk:
                continue
            response = client.describe_services(cluster=cluster_arn, services=chunk)
            for service in response.get("services", []):
                if not isinstance(service, dict):
                    continue
                service_name = str(service.get("serviceName") or "")
                task_definition_arn = str(service.get("taskDefinition") or "")
                if task_definition_arn and task_definition_arn not in task_definition_cache:
                    task_definition_cache[task_definition_arn] = client.describe_task_definition(
                        taskDefinition=task_definition_arn
                    ).get("taskDefinition", {})
                task_definition = task_definition_cache.get(task_definition_arn, {})
                cpu_units = int(task_definition.get("cpu") or service.get("cpu") or 0)
                memory_mib = int(task_definition.get("memory") or service.get("memory") or 0)
                dimensions = [
                    {"Name": "ClusterName", "Value": cluster_name},
                    {"Name": "ServiceName", "Value": service_name},
                ]
                launch_type = str(service.get("launchType") or "")
                if not launch_type:
                    capacity_providers = service.get("capacityProviderStrategy", [])
                    if isinstance(capacity_providers, list) and any(
                        isinstance(item, dict)
                        and str(item.get("capacityProvider")).startswith("FARGATE")
                        for item in capacity_providers
                    ):
                        launch_type = "FARGATE"

                collected.append(
                    {
                        "accountId": account_record.account_id,
                        "accountName": account_record.name,
                        "region": region_name,
                        "clusterArn": cluster_arn,
                        "clusterName": cluster_name,
                        "serviceArn": service.get("serviceArn", ""),
                        "serviceName": service_name,
                        "taskDefinitionArn": task_definition_arn,
                        "launchType": launch_type,
                        "desiredCount": int(service.get("desiredCount") or 0),
                        "runningCount": int(service.get("runningCount") or 0),
                        "cpuUnits": cpu_units,
                        "memoryMiB": memory_mib,
                        "avgCpuUtilization14d": collect_single_stat_metric(
                            session,
                            region_name=region_name,
                            namespace="AWS/ECS",
                            metric_name="CPUUtilization",
                            dimensions=dimensions,
                            statistic="Average",
                        ),
                        "avgMemoryUtilization14d": collect_single_stat_metric(
                            session,
                            region_name=region_name,
                            namespace="AWS/ECS",
                            metric_name="MemoryUtilization",
                            dimensions=dimensions,
                            statistic="Average",
                        ),
                    }
                )

    return collected


def collect_ecs_inventory(
    session: boto3.Session,
    *,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None = None,
    session_name: str = "finops-pack",
    current_account_id: str | None = None,
) -> dict[str, Any]:
    """Collect ECS services across accounts and regions without failing the whole run."""
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
                    _collect_region_services(
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

    items.sort(
        key=lambda item: (
            item["accountId"],
            item["region"],
            item["clusterName"],
            item["serviceName"],
        )
    )
    return {
        "operation": "DescribeServices",
        "regions": normalized_regions,
        "accountCount": len(account_records),
        "itemCount": len(items),
        "errorCount": len(errors),
        "items": items,
        "errors": errors,
    }
