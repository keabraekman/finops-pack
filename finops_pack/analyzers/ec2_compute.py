# ruff: noqa: E501
"""Native EC2 rightsizing and Graviton migration analysis."""

from __future__ import annotations

from typing import Any

from finops_pack.analyzers.action_builders import build_grouped_action
from finops_pack.analyzers.pricing import MONTHLY_HOURS, estimate_ec2_hourly_cost
from finops_pack.models import NormalizedRecommendation

EC2_SIZES = [
    "nano",
    "micro",
    "small",
    "medium",
    "large",
    "xlarge",
    "2xlarge",
    "3xlarge",
    "4xlarge",
    "6xlarge",
    "8xlarge",
    "9xlarge",
    "10xlarge",
    "12xlarge",
    "16xlarge",
    "18xlarge",
    "24xlarge",
    "32xlarge",
]
GRAVITON_FAMILY_MAP = {
    "m5": "m7g",
    "m6i": "m7g",
    "m6a": "m7g",
    "c5": "c7g",
    "c6i": "c7g",
    "c6a": "c7g",
    "r5": "r7g",
    "r6i": "r7g",
    "r6a": "r7g",
    "t3": "t4g",
    "t3a": "t4g",
}


def _covered_resource_ids(
    recommendations: list[NormalizedRecommendation] | None,
    *,
    action_types: set[str],
) -> set[str]:
    return {
        str(rec.resource_id)
        for rec in recommendations or []
        if rec.resource_id and str(rec.action_type or "") in action_types
    }


def _downsize_instance_type(instance_type: str) -> str | None:
    family, _, size = instance_type.partition(".")
    if not family or not size:
        return None
    try:
        index = EC2_SIZES.index(size)
    except ValueError:
        return None
    if index <= 2:
        return None
    return f"{family}.{EC2_SIZES[index - 1]}"


def _graviton_target(instance_type: str) -> str | None:
    family, _, size = instance_type.partition(".")
    target_family = GRAVITON_FAMILY_MAP.get(family)
    if target_family is None or not size:
        return None
    return f"{target_family}.{size}"


def build_ec2_compute_actions(
    inventory_snapshot: dict[str, Any] | None,
    *,
    recommendations: list[NormalizedRecommendation] | None = None,
) -> list:
    """Build native EC2 rightsizing and Graviton action opportunities."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    covered_rightsize_ids = _covered_resource_ids(recommendations, action_types={"Rightsize"})
    covered_graviton_ids = _covered_resource_ids(
        recommendations,
        action_types={"MigrateToGraviton", "Modernize", "Upgrade"},
    )

    rightsize_candidates: list[dict[str, Any]] = []
    graviton_candidates: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        instance_id = str(item.get("instanceId") or "")
        if not instance_id or str(item.get("state") or "").lower() != "running":
            continue
        instance_type = str(item.get("instanceType") or "")
        avg_cpu = item.get("avgCpuUtilization14d")
        max_cpu = item.get("maxCpuUtilization14d")
        hourly_cost, confidence = estimate_ec2_hourly_cost(instance_type)
        monthly_cost = hourly_cost * MONTHLY_HOURS
        account_name = str(item.get("accountName") or item.get("accountId") or "Unknown")

        downsized_instance_type = _downsize_instance_type(instance_type)
        if (
            instance_id not in covered_rightsize_ids
            and downsized_instance_type is not None
            and isinstance(avg_cpu, (int, float))
            and float(avg_cpu) <= 20
            and (not isinstance(max_cpu, (int, float)) or float(max_cpu) <= 45)
        ):
            rightsize_candidates.append(
                {
                    "account_name": account_name,
                    "account_id": str(item.get("accountId") or "Unknown"),
                    "region": str(item.get("region") or ""),
                    "resource_name": str(item.get("name") or instance_id),
                    "resource_id": instance_id,
                    "detail": f"{instance_type} -> {downsized_instance_type} · avg CPU {float(avg_cpu):.1f}%",
                    "monthly_savings": round(monthly_cost * 0.25, 2),
                    "confidence": confidence,
                }
            )

        graviton_target = _graviton_target(instance_type)
        platform = str(item.get("platformDetails") or item.get("platform") or "Linux/UNIX").lower()
        if (
            instance_id not in covered_graviton_ids
            and graviton_target is not None
            and "windows" not in platform
            and isinstance(avg_cpu, (int, float))
            and float(avg_cpu) <= 65
        ):
            graviton_candidates.append(
                {
                    "account_name": account_name,
                    "account_id": str(item.get("accountId") or "Unknown"),
                    "region": str(item.get("region") or ""),
                    "resource_name": str(item.get("name") or instance_id),
                    "resource_id": instance_id,
                    "detail": f"{instance_type} -> {graviton_target} · avg CPU {float(avg_cpu):.1f}%",
                    "monthly_savings": round(monthly_cost * 0.18, 2),
                    "confidence": confidence,
                }
            )

    actions = []
    if rightsize_candidates:
        actions.append(
            build_grouped_action(
                bucket="Rightsize",
                lever_key="ec2_rightsizing",
                action_label=f"Rightsize {len(rightsize_candidates)} EC2 instance{'s' if len(rightsize_candidates) != 1 else ''}",
                source_label="Native finops-pack",
                items=sorted(
                    rightsize_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="medium",
                confidence=(
                    "high"
                    if {item["confidence"] for item in rightsize_candidates} == {"high"}
                    else "medium"
                ),
                why_it_matters="Low-utilization EC2 instances often keep running on shapes larger than their observed demand needs.",
                what_to_do_first="Resize one lower-risk instance first and validate CPU, memory, and network behavior after the change.",
                evidence_summary=f"{len(rightsize_candidates)} running EC2 instance(s) showed sustained low CPU utilization and a safe one-step downsizing target.",
            )
        )

    if graviton_candidates:
        actions.append(
            build_grouped_action(
                bucket="Rightsize",
                lever_key="graviton_migration",
                action_label=f"Migrate {len(graviton_candidates)} EC2 workload{'s' if len(graviton_candidates) != 1 else ''} to Graviton",
                source_label="Mixed / derived",
                items=sorted(
                    graviton_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="medium",
                confidence=(
                    "high"
                    if {item["confidence"] for item in graviton_candidates} == {"high"}
                    else "medium"
                ),
                why_it_matters="Compatible Linux workloads can often move to Graviton families and reduce monthly compute cost.",
                what_to_do_first="Validate AMI, architecture, and package compatibility on one representative workload before broader migration.",
                evidence_summary=f"{len(graviton_candidates)} Linux EC2 workload(s) matched a conservative Graviton migration rule.",
            )
        )

    return actions
