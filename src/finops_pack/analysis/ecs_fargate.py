# ruff: noqa: E501
"""Native ECS and Fargate rightsizing analysis."""

from __future__ import annotations

from typing import Any

from finops_pack.analysis.action_builders import build_grouped_action
from finops_pack.analysis.pricing import estimate_fargate_monthly_cost
from finops_pack.domain.models import NormalizedRecommendation


def _covered_resource_ids(recommendations: list[NormalizedRecommendation] | None) -> set[str]:
    return {
        str(rec.resource_id)
        for rec in recommendations or []
        if rec.resource_id and rec.current_resource_type == "EcsService"
    }


def build_ecs_fargate_actions(
    inventory_snapshot: dict[str, Any] | None,
    *,
    recommendations: list[NormalizedRecommendation] | None = None,
) -> list:
    """Build native ECS/Fargate rightsizing and idle-service actions."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    covered_ids = _covered_resource_ids(recommendations)
    idle_candidates: list[dict[str, Any]] = []
    rightsize_candidates: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        service_arn = str(item.get("serviceArn") or "")
        if not service_arn or service_arn in covered_ids:
            continue
        launch_type = str(item.get("launchType") or "")
        if launch_type != "FARGATE":
            continue
        desired_count = int(item.get("desiredCount") or 0)
        running_count = int(item.get("runningCount") or 0)
        cpu_units = int(item.get("cpuUnits") or 0)
        memory_mib = int(item.get("memoryMiB") or 0)
        if desired_count <= 0 or cpu_units <= 0 or memory_mib <= 0:
            continue

        monthly_cost = estimate_fargate_monthly_cost(
            cpu_units=cpu_units,
            memory_mib=memory_mib,
            desired_count=desired_count,
        )
        avg_cpu = item.get("avgCpuUtilization14d")
        avg_memory = item.get("avgMemoryUtilization14d")
        account_name = str(item.get("accountName") or item.get("accountId") or "Unknown")
        base_item = {
            "account_name": account_name,
            "account_id": str(item.get("accountId") or "Unknown"),
            "region": str(item.get("region") or ""),
            "resource_name": str(item.get("serviceName") or service_arn),
            "resource_id": service_arn,
        }

        if (
            running_count > 0
            and isinstance(avg_cpu, (int, float))
            and isinstance(avg_memory, (int, float))
            and float(avg_cpu) <= 5
            and float(avg_memory) <= 10
        ):
            idle_candidates.append(
                {
                    **base_item,
                    "detail": f"{desired_count} task(s) · avg CPU {float(avg_cpu):.1f}% · avg memory {float(avg_memory):.1f}%",
                    "monthly_savings": round(monthly_cost * 0.8, 2),
                }
            )
            continue

        if (
            isinstance(avg_cpu, (int, float))
            and isinstance(avg_memory, (int, float))
            and float(avg_cpu) <= 35
            and float(avg_memory) <= 50
        ):
            rightsize_candidates.append(
                {
                    **base_item,
                    "detail": f"{cpu_units} CPU units / {memory_mib} MiB · avg CPU {float(avg_cpu):.1f}% · avg memory {float(avg_memory):.1f}%",
                    "monthly_savings": round(monthly_cost * 0.25, 2),
                }
            )

    actions = []
    if idle_candidates:
        actions.append(
            build_grouped_action(
                bucket="Stop waste",
                lever_key="ecs_fargate_rightsizing",
                action_label=f"Stop or scale down {len(idle_candidates)} idle ECS or Fargate service{'s' if len(idle_candidates) != 1 else ''}",
                source_label="Native finops-pack",
                items=sorted(
                    idle_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="medium",
                confidence="medium",
                why_it_matters="Idle container services can continue paying for reserved task CPU and memory even when hardly anyone is using them.",
                what_to_do_first="Confirm service demand and autoscaling behavior, then scale one low-risk service down before removing broader waste.",
                evidence_summary=f"{len(idle_candidates)} Fargate service(s) showed very low CPU and memory utilization over the recent CloudWatch window.",
            )
        )

    if rightsize_candidates:
        actions.append(
            build_grouped_action(
                bucket="Rightsize",
                lever_key="ecs_fargate_rightsizing",
                action_label=f"Rightsize {len(rightsize_candidates)} ECS or Fargate service{'s' if len(rightsize_candidates) != 1 else ''}",
                source_label="Native finops-pack",
                items=sorted(
                    rightsize_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="medium",
                confidence="medium",
                why_it_matters="Container reservations often drift above actual workload demand, especially in long-lived services.",
                what_to_do_first="Review one service's reservation and deployment settings before trimming CPU or memory on the task definition.",
                evidence_summary=f"{len(rightsize_candidates)} ECS/Fargate service(s) cleared conservative low-utilization rightsizing rules.",
            )
        )

    return actions
