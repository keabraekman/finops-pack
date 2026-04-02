"""Shared action-opportunity translation and ranking helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from finops_pack.models import (
    AccountMapEntry,
    ActionOpportunity,
    ActionPriority,
    NormalizedRecommendation,
)

MONTHLY_DAYS_EQUIVALENT = 730 / 24
PRIORITY_SCORE = {"high": 3, "medium": 2, "low": 1}
OWNER_RELEVANCE_SCORE = {
    "Stop waste": 3.0,
    "Rightsize": 2.7,
    "Buy discounts": 2.5,
    "Storage cleanup": 2.3,
}


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
    return "resource"


def _coh_action_descriptor(
    recommendation: NormalizedRecommendation,
) -> dict[str, str]:
    action_type = recommendation.action_type or ""
    resource_type = recommendation.current_resource_type or ""

    if action_type == "PurchaseSavingsPlans":
        return {
            "group_key": "buy-compute-sp",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} compute savings plan{noun}",
            "why": "Steady on-demand usage is likely costing more than committed coverage would.",
            "first_step": (
                "Validate the last 30 days of steady-state usage, then review the AWS COH "
                "Savings Plans recommendation before purchasing coverage."
            ),
        }
    if action_type == "PurchaseReservedInstances" and resource_type == "RdsDbInstance":
        return {
            "group_key": "buy-rds-ri",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} reserved DB instance commitment{noun}",
            "why": (
                "Recurring database usage may be cheaper on a reserved commitment "
                "than on-demand."
            ),
            "first_step": (
                "Confirm the DB class and engine are stable before buying reserved "
                "coverage."
            ),
        }
    if action_type == "PurchaseReservedInstances":
        return {
            "group_key": "buy-ec2-ri",
            "bucket": "Buy discounts",
            "label_template": "Buy {count} reserved instance commitment{noun}",
            "why": "Recurring compute usage may be cheaper with reserved pricing than on-demand.",
            "first_step": (
                "Confirm the matching instance families are stable before buying "
                "reserved coverage."
            ),
        }
    if action_type == "MigrateToGraviton" and resource_type == "Ec2Instance":
        return {
            "group_key": "ec2-graviton",
            "bucket": "Rightsize",
            "label_template": "Migrate {count} EC2 instance{noun} to Graviton",
            "why": "Graviton often lowers EC2 spend while preserving or improving performance.",
            "first_step": (
                "Test one representative workload on Graviton before rolling the "
                "change out."
            ),
        }
    if resource_type == "Ec2Instance" and action_type in {
        "Delete",
        "Stop",
        "Terminate",
    }:
        return {
            "group_key": "ec2-stop",
            "bucket": "Stop waste",
            "label_template": "Shut down {count} idle EC2 resource{noun}",
            "why": "Idle compute keeps billing even when workloads are not active.",
            "first_step": (
                "Confirm each instance is idle or redundant before stopping or "
                "deleting it."
            ),
        }
    if resource_type == "Ec2Instance":
        return {
            "group_key": "ec2-rightsize",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} EC2 instance{noun}",
            "why": "Oversized EC2 instances are a common source of avoidable monthly spend.",
            "first_step": (
                "Resize one lower-risk instance first and validate performance after "
                "the change."
            ),
        }
    if resource_type == "RdsDbInstance" and action_type in {
        "Delete",
        "Stop",
        "Terminate",
    }:
        return {
            "group_key": "rds-stop",
            "bucket": "Stop waste",
            "label_template": "Stop or remove {count} idle RDS resource{noun}",
            "why": "Idle databases can keep generating cost even when teams are not using them.",
            "first_step": (
                "Confirm restore and rollback options before stopping or deleting "
                "the database."
            ),
        }
    if resource_type == "RdsDbInstance":
        return {
            "group_key": "rds-rightsize",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} RDS instance{noun}",
            "why": "Database instances often drift above the capacity they really need.",
            "first_step": (
                "Compare recent utilization and test a smaller class in a non-critical "
                "environment first."
            ),
        }
    if resource_type == "EcsService":
        return {
            "group_key": "ecs-rightsize",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} ECS service{noun}",
            "why": "Container reservations can quietly exceed real workload needs.",
            "first_step": "Review one ECS service's CPU and memory headroom before resizing.",
        }
    if resource_type == "LambdaFunction":
        return {
            "group_key": "lambda-rightsize",
            "bucket": "Rightsize",
            "label_template": "Rightsize {count} Lambda function{noun}",
            "why": "Over-allocated Lambda memory drives unnecessary cost on every invocation.",
            "first_step": (
                "Check recent duration and memory headroom before reducing memory "
                "allocation."
            ),
        }
    if resource_type == "EbsVolume":
        return {
            "group_key": "ebs-cleanup",
            "bucket": "Storage cleanup",
            "label_template": "Clean up or tune {count} EBS volume{noun}",
            "why": "Storage waste can linger because it is less visible than compute waste.",
            "first_step": (
                "Review one recommended volume and confirm whether it should be "
                "deleted or tuned."
            ),
        }

    return {
        "group_key": f"generic-{recommendation.category}",
        "bucket": (
            "Buy discounts"
            if recommendation.category == "commitment (SP/RI)"
            else "Storage cleanup"
            if recommendation.category == "storage/network/etc."
            else "Rightsize"
        ),
        "label_template": "Act on {count} AWS optimization recommendation{noun}",
        "why": "AWS identified savings opportunities that are still sitting unclaimed.",
        "first_step": (
            "Review the top AWS recommendation and confirm it matches current "
            "workload behavior."
        ),
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
    return [
        ActionOpportunity(
            bucket="Stop waste",
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
                "Non-production compute often runs nights and weekends even though nobody is "
                "using it."
            ),
            what_to_do_first=(
                "Confirm each instance can be safely stopped on a schedule, then apply the "
                "schedule to one low-risk environment first."
            ),
            evidence_summary=(
                f"{len(candidates)} non-prod EC2 candidate(s) with Cost Explorer-backed "
                "off-hours savings estimates."
            ),
            opportunity_count=len(candidates),
            account_names=sorted({item["account_name"] for item in candidates}),
            supporting_items=[
                {
                    **item,
                    "monthly_savings_display": (
                        f"{_format_currency(float(item['monthly_savings']))}/mo"
                    ),
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
    """Translate raw COH findings into business-facing action opportunities."""
    if not recommendations:
        return []

    account_lookup = {entry.account_id: entry for entry in account_map}
    grouped: dict[
        tuple[str, str, str, str, str, str, str],
        dict[str, Any],
    ] = {}

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
        key = (
            descriptor["group_key"],
            descriptor["bucket"],
            descriptor["label_template"],
            risk,
            effort,
            descriptor["why"],
            descriptor["first_step"],
        )
        bucket = grouped.setdefault(
            key,
            {
                "items": [],
                "account_names": set(),
            },
        )
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
        label_template,
        risk,
        effort,
        why,
        first_step,
    ), payload in grouped.items():
        items = payload["items"]
        items.sort(
            key=lambda item: (
                -float(item["monthly_savings"]),
                item["account_name"],
                item["resource_id"],
            )
        )
        actions.append(
            ActionOpportunity(
                bucket=bucket_name,  # type: ignore[arg-type]
                action_label=_format_counted_label(label_template, len(items)),
                monthly_savings=round(sum(float(item["monthly_savings"]) for item in items), 2),
                risk=risk,  # type: ignore[arg-type]
                effort=effort,  # type: ignore[arg-type]
                confidence="high",
                source_label="AWS COH",
                why_it_matters=why,
                what_to_do_first=first_step,
                evidence_summary=(
                    f"{len(items)} AWS Cost Optimization Hub recommendation(s) across "
                    f"{len(payload['account_names'])} account(s)."
                ),
                opportunity_count=len(items),
                account_names=sorted(payload["account_names"]),
                supporting_items=[
                    {
                        **item,
                        "monthly_savings_display": (
                            f"{_format_currency(float(item['monthly_savings']))}/mo"
                        ),
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
    savings_score = min(action.monthly_savings, 10000.0) / 100.0
    confidence_score = _priority_rank(action.confidence) * 2.5
    effort_score = (4 - _priority_rank(action.effort)) * 2.0
    risk_score = (4 - _priority_rank(action.risk)) * 2.0
    owner_relevance = OWNER_RELEVANCE_SCORE[action.bucket]
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


def summarize_actions_by_bucket(
    actions: Sequence[ActionOpportunity],
) -> list[dict[str, Any]]:
    """Aggregate owner-facing actions into the four lead-magnet buckets."""
    bucket_totals: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "bucket": "",
            "opportunity_count": 0,
            "monthly_savings": 0.0,
            "top_action": None,
        }
    )
    for action in actions:
        bucket = bucket_totals[action.bucket]
        bucket["bucket"] = action.bucket
        bucket["opportunity_count"] += action.opportunity_count
        bucket["monthly_savings"] += action.monthly_savings
        if bucket["top_action"] is None:
            bucket["top_action"] = action

    ordered_buckets = ["Stop waste", "Rightsize", "Buy discounts", "Storage cleanup"]
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
                else "No opportunities detected in this bucket for this run."
            ),
        }
        for bucket_name in ordered_buckets
        if bucket_totals[bucket_name]["opportunity_count"] > 0
    ]
