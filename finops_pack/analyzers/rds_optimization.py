# ruff: noqa: E501
"""Native RDS rightsizing and storage tuning analysis."""

from __future__ import annotations

from typing import Any

from finops_pack.analyzers.action_builders import build_grouped_action
from finops_pack.analyzers.pricing import MONTHLY_HOURS, estimate_rds_hourly_cost
from finops_pack.models import NormalizedRecommendation

RDS_SIZES = [
    "micro",
    "small",
    "medium",
    "large",
    "xlarge",
    "2xlarge",
    "4xlarge",
    "8xlarge",
    "12xlarge",
    "16xlarge",
    "24xlarge",
    "32xlarge",
]


def _covered_resource_ids(
    recommendations: list[NormalizedRecommendation] | None,
    *,
    resource_type: str,
    action_types: set[str],
) -> set[str]:
    return {
        str(rec.resource_id)
        for rec in recommendations or []
        if rec.resource_id
        and rec.current_resource_type == resource_type
        and str(rec.action_type or "") in action_types
    }


def _downsize_db_class(db_instance_class: str) -> str | None:
    normalized = db_instance_class.removeprefix("db.")
    family, _, size = normalized.partition(".")
    if not family or not size:
        return None
    try:
        index = RDS_SIZES.index(size)
    except ValueError:
        return None
    if index <= 1:
        return None
    return f"db.{family}.{RDS_SIZES[index - 1]}"


def build_rds_optimization_actions(
    inventory_snapshot: dict[str, Any] | None,
    *,
    recommendations: list[NormalizedRecommendation] | None = None,
) -> list:
    """Build native RDS rightsizing and storage actions."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    cluster_items = inventory_snapshot.get("clusters", [])
    if not isinstance(raw_items, list):
        raw_items = []
    if not isinstance(cluster_items, list):
        cluster_items = []

    covered_rightsize_ids = _covered_resource_ids(
        recommendations,
        resource_type="RdsDbInstance",
        action_types={"Rightsize", "ScaleIn"},
    )
    covered_storage_ids = _covered_resource_ids(
        recommendations,
        resource_type="RdsDbInstance",
        action_types={"Upgrade", "Modernize", "Rightsize"},
    )

    rightsize_candidates: list[dict[str, Any]] = []
    storage_candidates: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        resource_id = str(item.get("dbInstanceIdentifier") or "")
        if not resource_id or str(item.get("status") or "").lower() != "available":
            continue
        db_instance_class = str(item.get("dbInstanceClass") or "")
        avg_cpu = item.get("avgCpuUtilization14d")
        downsized_class = _downsize_db_class(db_instance_class)
        hourly_cost, confidence = estimate_rds_hourly_cost(db_instance_class)
        monthly_cost = hourly_cost * MONTHLY_HOURS
        account_name = str(item.get("accountName") or item.get("accountId") or "Unknown")

        if (
            resource_id not in covered_rightsize_ids
            and downsized_class is not None
            and isinstance(avg_cpu, (int, float))
            and float(avg_cpu) <= 25
        ):
            rightsize_candidates.append(
                {
                    "account_name": account_name,
                    "account_id": str(item.get("accountId") or "Unknown"),
                    "region": str(item.get("region") or ""),
                    "resource_name": resource_id,
                    "resource_id": resource_id,
                    "detail": f"{db_instance_class} -> {downsized_class} · avg CPU {float(avg_cpu):.1f}%",
                    "monthly_savings": round(monthly_cost * 0.22, 2),
                    "confidence": confidence,
                }
            )

        storage_type = str(item.get("storageType") or "").lower()
        allocated_storage = float(item.get("allocatedStorage") or 0)
        free_storage_bytes = item.get("avgFreeStorageBytes14d")
        free_storage_gib = (
            float(free_storage_bytes) / (1024**3)
            if isinstance(free_storage_bytes, (int, float))
            else None
        )
        if resource_id not in covered_storage_ids and allocated_storage > 0:
            if storage_type == "gp2":
                storage_candidates.append(
                    {
                        "account_name": account_name,
                        "account_id": str(item.get("accountId") or "Unknown"),
                        "region": str(item.get("region") or ""),
                        "resource_name": resource_id,
                        "resource_id": resource_id,
                        "detail": f"{int(allocated_storage)} GiB gp2 storage -> gp3",
                        "monthly_savings": round(allocated_storage * 0.02, 2),
                    }
                )
            elif free_storage_gib is not None and free_storage_gib >= allocated_storage * 0.4:
                storage_candidates.append(
                    {
                        "account_name": account_name,
                        "account_id": str(item.get("accountId") or "Unknown"),
                        "region": str(item.get("region") or ""),
                        "resource_name": resource_id,
                        "resource_id": resource_id,
                        "detail": f"{int(allocated_storage)} GiB allocated · ~{free_storage_gib:.0f} GiB free",
                        "monthly_savings": round((allocated_storage * 0.08) * 0.15, 2),
                    }
                )

    for cluster in cluster_items:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("dbClusterIdentifier") or "")
        storage_type = str(cluster.get("storageType") or "").lower()
        allocated_storage = float(cluster.get("allocatedStorage") or 0)
        if not cluster_id or not storage_type:
            continue
        if "aurora-iopt" in storage_type or "iopt" in storage_type:
            storage_candidates.append(
                {
                    "account_name": str(
                        cluster.get("accountName") or cluster.get("accountId") or "Unknown"
                    ),
                    "account_id": str(cluster.get("accountId") or "Unknown"),
                    "region": str(cluster.get("region") or ""),
                    "resource_name": cluster_id,
                    "resource_id": cluster_id,
                    "detail": f"Aurora storage type {storage_type}",
                    "monthly_savings": round(max(allocated_storage, 100.0) * 0.01, 2),
                }
            )

    actions = []
    if rightsize_candidates:
        actions.append(
            build_grouped_action(
                bucket="Rightsize",
                lever_key="rds_rightsizing",
                action_label=f"Rightsize {len(rightsize_candidates)} RDS instance{'s' if len(rightsize_candidates) != 1 else ''}",
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
                why_it_matters="Low-utilization database instances often keep running on classes larger than their observed workload needs.",
                what_to_do_first="Review CPU, connections, and maintenance windows before testing a smaller DB class in a lower-risk environment.",
                evidence_summary=f"{len(rightsize_candidates)} RDS instance(s) showed sustained low CPU and a safe one-step downsizing target.",
            )
        )

    if storage_candidates:
        actions.append(
            build_grouped_action(
                bucket="Storage cleanup",
                lever_key="rds_aurora_storage_tuning",
                action_label=f"Tune {len(storage_candidates)} RDS or Aurora storage configuration{'s' if len(storage_candidates) != 1 else ''}",
                source_label="Native finops-pack",
                items=sorted(
                    storage_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="low",
                effort="medium",
                confidence="medium",
                why_it_matters="Database storage classes and provisioned profiles can often be tuned down without changing workload behavior.",
                what_to_do_first="Review storage class, free space, and performance needs before moving one lower-risk database to a cheaper storage profile.",
                evidence_summary=f"{len(storage_candidates)} RDS or Aurora storage candidate(s) cleared conservative tuning rules.",
            )
        )

    return actions
