# ruff: noqa: E501
"""Shared action-opportunity translation and ranking helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from finops_pack.levers import BUCKET_ORDER, lever_owner_relevance, lever_summary
from finops_pack.models import (
    AccountMapEntry,
    ActionOpportunity,
    ActionPriority,
    ActionSourceLabel,
    LeverKey,
    NormalizedRecommendation,
)

MONTHLY_DAYS_EQUIVALENT = 730 / 24
PRIORITY_SCORE = {"high": 3, "medium": 2, "low": 1}


def _priority_rank(value: ActionPriority) -> int:
    return PRIORITY_SCORE[value]


def _max_priority(values: Sequence[ActionPriority]) -> ActionPriority:
    if not values:
        return "low"
    best = max(values, key=_priority_rank)
    return best


def _format_currency(amount: float) -> str:
    return f"${amount:,.2f}"


def _format_counted_label(template: str, count: int) -> str:
    return template.format(
        count=count,
        noun="" if count == 1 else "s",
    )


def _resource_kind_label(recommendation: NormalizedRecommendation) -> str:
    resource_type = recommendation.current_resource_type or ""
    if resource_type == "Ec2Instance":
        return "EC2 instance"
    if resource_type == "RdsDbInstance":
        return "RDS instance"
    if resource_type == "EcsService":
        return "ECS service"
    if resource_type == "LambdaFunction":
        return "Lambda function"
    if resource_type == "EbsVolume":
        return "EBS volume"
    if resource_type == "NatGateway":
        return "NAT Gateway"
    if resource_type in {"S3Bucket", "S3Storage"}:
        return "S3 bucket"
    return "resource"


def _coh_source_label(
    recommendation: NormalizedRecommendation,
    *,
    lever_key: LeverKey,
) -> ActionSourceLabel:
    action_type = recommendation.action_type or ""
    resource_type = recommendation.current_resource_type or ""
    if lever_key == "commitments":
        return "CE fallback" if recommendation.source != "cost_optimization_hub" else "AWS COH"
    if resource_type in {
        "Ec2Instance",
        "RdsDbInstance",
        "EcsService",
        "LambdaFunction",
        "EbsVolume",
    } and action_type in {
        "Delete",
        "MigrateToGraviton",
        "Rightsize",
        "ScaleIn",
        "Stop",
        "Upgrade",
        "Modernize",
    }:
        return "AWS Compute Optimizer"
    return "AWS COH"


def _coh_action_descriptor(
    recommendation: NormalizedRecommendation,
) -> dict[str, str]:
    action_type = recommendation.action_type or ""
    resource_type = recommendation.current_resource_type or ""

    if action_type == "PurchaseSavingsPlans":
        return {
            "group_key": "buy-compute-sp",
            "lever_key": "commitments",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} compute savings plan{noun}",
            "why": "Steady on-demand usage is likely costing more than committed coverage would.",
            "first_step": (
                "Validate the last 30 days of steady-state usage, then review the AWS recommendation "
                "before purchasing coverage."
            ),
        }
    if action_type == "PurchaseReservedInstances" and resource_type == "RdsDbInstance":
        return {
            "group_key": "buy-rds-ri",
            "lever_key": "commitments",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} reserved DB instance commitment{noun}",
            "why": "Recurring database usage may be cheaper on a reserved commitment than on-demand.",
            "first_step": "Confirm the DB class and engine are stable before buying reserved coverage.",
        }
    if action_type == "PurchaseReservedInstances":
        return {
            "group_key": "buy-ec2-ri",
            "lever_key": "commitments",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} reserved instance commitment{noun}",
            "why": "Recurring compute usage may be cheaper with reserved pricing than on-demand.",
            "first_step": (
                "Confirm the matching instance families are stable before buying reserved coverage."
            ),
        }
    if action_type == "MigrateToGraviton" and resource_type == "Ec2Instance":
        return {
            "group_key": "ec2-graviton",
            "lever_key": "graviton_migration",
            "bucket": "Rightsize",
            "label_template": "Migrate {count} EC2 workload{noun} to Graviton",
            "why": "Graviton often lowers EC2 spend while preserving or improving performance.",
            "first_step": "Test one representative workload on Graviton before rolling the change out.",
        }
    if resource_type == "Ec2Instance" and action_type in {"Delete", "Stop", "Terminate"}:
        return {
            "group_key": "ec2-stop",
            "lever_key": "ec2_rightsizing",
            "bucket": "Stop waste",
            "label_template": "Shut down {count} idle EC2 resource{noun}",
            "why": "Idle compute keeps billing even when workloads are not active.",
            "first_step": "Confirm each instance is idle or redundant before stopping or deleting it.",
        }
    if resource_type == "Ec2Instance":
        return {
            "group_key": "ec2-rightsize",
            "lever_key": "ec2_rightsizing",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} EC2 instance{noun}",
            "why": "Oversized EC2 instances are a common source of avoidable monthly spend.",
            "first_step": "Resize one lower-risk instance first and validate performance after the change.",
        }
    if resource_type == "RdsDbInstance" and action_type in {"Delete", "Stop", "Terminate"}:
        return {
            "group_key": "rds-stop",
            "lever_key": "rds_nonprod_schedule",
            "bucket": "Stop waste",
            "label_template": "Stop or remove {count} idle RDS resource{noun}",
            "why": "Idle databases can keep generating cost even when teams are not using them.",
            "first_step": "Confirm restore and rollback options before stopping or deleting the database.",
        }
    if resource_type == "RdsDbInstance" and "storage" in (
        recommendation.recommendation.summary.lower() if recommendation.recommendation else ""
    ):
        return {
            "group_key": "rds-storage",
            "lever_key": "rds_aurora_storage_tuning",
            "bucket": "Storage cleanup",
            "label_template": "Tune {count} RDS or Aurora storage configuration{noun}",
            "why": "Database storage classes and provisioned performance often have cheaper options.",
            "first_step": "Review current storage class, provisioned IOPS, and free space before changing the profile.",
        }
    if resource_type == "RdsDbInstance":
        return {
            "group_key": "rds-rightsize",
            "lever_key": "rds_rightsizing",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} RDS instance{noun}",
            "why": "Database instances often drift above the capacity they really need.",
            "first_step": "Compare recent utilization and test a smaller class in a non-critical environment first.",
        }
    if resource_type == "EcsService":
        return {
            "group_key": "ecs-rightsize",
            "lever_key": "ecs_fargate_rightsizing",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} ECS or Fargate service{noun}",
            "why": "Container reservations can quietly exceed real workload needs.",
            "first_step": "Review one ECS service's CPU and memory headroom before resizing.",
        }
    if resource_type == "LambdaFunction":
        return {
            "group_key": "lambda-rightsize",
            "lever_key": "lambda_memory_rightsizing",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} Lambda function{noun}",
            "why": "Over-allocated Lambda memory drives unnecessary cost on every invocation.",
            "first_step": "Check recent duration and memory headroom before reducing memory allocation.",
        }
    if resource_type == "EbsVolume":
        return {
            "group_key": "ebs-cleanup",
            "lever_key": "ebs_cleanup_tuning",
            "bucket": "Storage cleanup",
            "label_template": "Clean up or tune {count} EBS volume{noun}",
            "why": "Storage waste can linger because it is less visible than compute waste.",
            "first_step": "Review one recommended volume and confirm whether it should be deleted or tuned.",
        }
    if resource_type == "NatGateway":
        return {
            "group_key": "nat-cleanup",
            "lever_key": "nat_gateway_cleanup",
            "bucket": "Stop waste",
            "label_template": "Clean up or redesign {count} NAT gateway{noun}",
            "why": "NAT gateways keep generating hourly cost even when traffic is minimal.",
            "first_step": "Check whether traffic is low enough to remove a gateway or replace traffic with endpoints.",
        }
    if resource_type in {"S3Bucket", "S3Storage"}:
        return {
            "group_key": "s3-storage",
            "lever_key": "s3_lifecycle_storage_class",
            "bucket": "Storage cleanup",
            "label_template": "Apply lifecycle or storage-class tuning to {count} S3 bucket{noun}",
            "why": "Buckets without lifecycle policies often keep standard storage longer than necessary.",
            "first_step": "Review one bucket's object age profile and add a conservative lifecycle rule first.",
        }

    return {
        "group_key": f"generic-{recommendation.category}",
        "lever_key": "ec2_rightsizing",
        "bucket": "Rightsize",
        "label_template": "Act on {count} AWS optimization recommendation{noun}",
        "why": "AWS identified savings opportunities that are still sitting unclaimed.",
        "first_step": "Review the top AWS recommendation and confirm it matches current workload behavior.",
    }


def build_schedule_action_opportunities(
    schedule_recommendations: Sequence[dict[str, Any]] | None,
    *,
    account_map: Sequence[AccountMapEntry],
) -> list[ActionOpportunity]:
    """Convert native EC2 schedule rows into lead-magnet actions."""
    if not schedule_recommendations:
        return []

    account_lookup = {entry.account_id: entry for entry in account_map}
    candidates: list[dict[str, Any]] = []
    for row in schedule_recommendations:
        if not isinstance(row, dict):
            continue
        account_id = row.get("accountId")
        if not isinstance(account_id, str) or not account_id:
            continue
        account_entry = account_lookup.get(account_id)
        if account_entry is None or account_entry.environment != "nonprod":
            continue
        if row.get("estimationStatus") != "estimated":
            continue
        likely_daily = row.get("estimatedOffHoursDailySavings")
        if not isinstance(likely_daily, (int, float)):
            continue

        monthly_savings = round(float(likely_daily) * MONTHLY_DAYS_EQUIVALENT, 2)
        candidates.append(
            {
                "account_name": account_entry.name,
                "account_id": account_id,
                "region": str(row.get("region") or ""),
                "resource_name": str(row.get("name") or row.get("instanceId") or ""),
                "resource_id": str(row.get("instanceId") or ""),
                "detail": (
                    f"{row.get('instanceType') or 'instance'}"
                    + (
                        f" · {row.get('Resource cost (14d)')}"
                        if row.get("Resource cost (14d)")
                        else ""
                    )
                ),
                "monthly_savings": monthly_savings,
            }
        )

    if not candidates:
        return []

    candidates.sort(
        key=lambda item: (
            -float(item["monthly_savings"]),
            item["account_name"],
            item["resource_id"],
        )
    )
    account_names = sorted({item["account_name"] for item in candidates})
    return [
        ActionOpportunity(
            bucket="Stop waste",
            lever_key="nonprod_schedule",
            action_label=(
                f"Stop {len(candidates)} non-prod EC2 "
                f"{'instance' if len(candidates) == 1 else 'instances'} off-hours"
            ),
            monthly_savings=round(sum(float(item["monthly_savings"]) for item in candidates), 2),
            risk="low",
            effort="low",
            confidence="high",
            source_label="Native finops-pack",
            why_it_matters=(
                "Non-production compute often runs nights and weekends even though nobody is using it."
            ),
            what_to_do_first=(
                "Confirm each instance can be safely stopped on a schedule, then apply the "
                "schedule to one low-risk environment first."
            ),
            evidence_summary=(
                f"{len(candidates)} non-prod EC2 candidate(s) with Cost Explorer-backed off-hours "
                "savings estimates."
            ),
            opportunity_count=len(candidates),
            resource_count=len(candidates),
            account_count=len(account_names),
            account_names=account_names,
            supporting_items=[
                {
                    **item,
                    "monthly_savings_display": f"{_format_currency(float(item['monthly_savings']))}/mo",
                }
                for item in candidates[:5]
            ],
        )
    ]


def build_coh_action_opportunities(
    recommendations: Sequence[NormalizedRecommendation] | None,
    *,
    account_map: Sequence[AccountMapEntry],
) -> list[ActionOpportunity]:
    """Translate raw AWS recommendations into business-facing action opportunities."""
    if not recommendations:
        return []

    account_lookup = {entry.account_id: entry for entry in account_map}
    grouped: dict[tuple[str, str, str, str, str, str, str, str], dict[str, Any]] = {}

    for recommendation in recommendations:
        monthly_savings = recommendation.estimated_monthly_savings
        if monthly_savings is None or monthly_savings <= 0:
            continue

        descriptor = _coh_action_descriptor(recommendation)
        risk = (
            recommendation.recommendation.risk
            if recommendation.recommendation is not None
            else "medium"
        )
        effort = (
            recommendation.recommendation.effort
            if recommendation.recommendation is not None
            else "medium"
        )
        lever_key = descriptor["lever_key"]
        source_label = _coh_source_label(recommendation, lever_key=lever_key)  # type: ignore[arg-type]
        key = (
            descriptor["group_key"],
            descriptor["bucket"],
            lever_key,
            descriptor["label_template"],
            risk,
            effort,
            descriptor["why"],
            descriptor["first_step"],
            source_label,
        )
        bucket = grouped.setdefault(key, {"items": [], "account_names": set()})
        account_name = (
            account_lookup[recommendation.account_id].name
            if recommendation.account_id in account_lookup
            else recommendation.account_id or "Unknown account"
        )
        resource_label = recommendation.resource_id or _resource_kind_label(recommendation)
        detail = (
            recommendation.recommendation.summary
            if recommendation.recommendation is not None
            else recommendation.current_resource_summary or resource_label
        )
        bucket["items"].append(
            {
                "account_name": account_name,
                "account_id": recommendation.account_id or "Unknown",
                "region": recommendation.region or "",
                "resource_name": resource_label,
                "resource_id": recommendation.resource_id or resource_label,
                "detail": detail,
                "monthly_savings": float(monthly_savings),
            }
        )
        bucket["account_names"].add(account_name)

    actions: list[ActionOpportunity] = []
    for (
        _group_key,
        bucket_name,
        lever_key,
        label_template,
        risk,
        effort,
        why,
        first_step,
        source_label,
    ), payload in grouped.items():
        items = payload["items"]
        items.sort(
            key=lambda item: (
                -float(item["monthly_savings"]),
                item["account_name"],
                item["resource_id"],
            )
        )
        account_names = sorted(payload["account_names"])
        actions.append(
            ActionOpportunity(
                bucket=bucket_name,  # type: ignore[arg-type]
                lever_key=lever_key,  # type: ignore[arg-type]
                action_label=_format_counted_label(label_template, len(items)),
                monthly_savings=round(sum(float(item["monthly_savings"]) for item in items), 2),
                risk=risk,  # type: ignore[arg-type]
                effort=effort,  # type: ignore[arg-type]
                confidence="high",
                source_label=source_label,  # type: ignore[arg-type]
                why_it_matters=why,
                what_to_do_first=first_step,
                evidence_summary=(
                    f"{len(items)} AWS recommendation(s) across {len(account_names)} account(s)."
                ),
                opportunity_count=len(items),
                resource_count=len(items),
                account_count=len(account_names),
                account_names=account_names,
                supporting_items=[
                    {
                        **item,
                        "monthly_savings_display": f"{_format_currency(float(item['monthly_savings']))}/mo",
                    }
                    for item in items[:5]
                ],
            )
        )

    return actions


def build_action_opportunities(
    *,
    account_map: Sequence[AccountMapEntry],
    recommendations: Sequence[NormalizedRecommendation] | None = None,
    schedule_recommendations: Sequence[dict[str, Any]] | None = None,
    native_actions: Sequence[ActionOpportunity] | None = None,
) -> list[ActionOpportunity]:
    """Build and rank all owner-facing action opportunities."""
    actions: list[ActionOpportunity] = []
    actions.extend(
        build_schedule_action_opportunities(
            schedule_recommendations,
            account_map=account_map,
        )
    )
    actions.extend(build_coh_action_opportunities(recommendations, account_map=account_map))
    actions.extend(list(native_actions or []))
    return rank_action_opportunities(actions)


def action_priority_score(action: ActionOpportunity) -> float:
    """Rank actions by savings, confidence, low effort, low risk, and owner relevance."""
    # Keep big, credible savings near the top without letting very high values dominate outright.
    savings_score = min(action.monthly_savings, 5000.0) / 15.0
    confidence_score = _priority_rank(action.confidence) * 2.5
    effort_score = (4 - _priority_rank(action.effort)) * 2.0
    risk_score = (4 - _priority_rank(action.risk)) * 2.0
    owner_relevance = lever_owner_relevance(action.lever_key)
    return round(
        savings_score + confidence_score + effort_score + risk_score + owner_relevance,
        4,
    )


def rank_action_opportunities(actions: Sequence[ActionOpportunity]) -> list[ActionOpportunity]:
    """Return actions sorted by business priority."""
    return sorted(
        actions,
        key=lambda action: (
            -action_priority_score(action),
            -action.monthly_savings,
            action.action_label.lower(),
            action.action_id or "",
        ),
    )


def summarize_actions_by_bucket(actions: Sequence[ActionOpportunity]) -> list[dict[str, Any]]:
    """Aggregate owner-facing actions into the four lead-magnet buckets."""
    bucket_totals: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "bucket": "",
            "opportunity_count": 0,
            "resource_count": 0,
            "monthly_savings": 0.0,
            "top_action": None,
        }
    )
    for action in actions:
        bucket = bucket_totals[action.bucket]
        bucket["bucket"] = action.bucket
        bucket["opportunity_count"] += action.resource_count
        bucket["resource_count"] += action.resource_count
        bucket["monthly_savings"] += action.monthly_savings
        if bucket["top_action"] is None:
            bucket["top_action"] = action

    return [
        {
            "bucket": bucket_name,
            "monthly_savings": round(bucket_totals[bucket_name]["monthly_savings"], 2),
            "monthly_savings_display": _format_currency(
                round(bucket_totals[bucket_name]["monthly_savings"], 2)
            ),
            "opportunity_count": bucket_totals[bucket_name]["opportunity_count"],
            "summary": (
                bucket_totals[bucket_name]["top_action"].why_it_matters
                if bucket_totals[bucket_name]["top_action"] is not None
                else lever_summary("ec2_rightsizing")
            ),
        }
        for bucket_name in BUCKET_ORDER
        if bucket_totals[bucket_name]["opportunity_count"] > 0
    ]
