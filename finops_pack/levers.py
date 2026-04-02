# ruff: noqa: E501
"""First-class savings lever metadata used across analyzers and rendering."""

from __future__ import annotations

from typing import Any

from finops_pack.models import ActionBucket, LeverKey

LEVER_ORDER: list[LeverKey] = [
    "nonprod_schedule",
    "commitments",
    "ec2_rightsizing",
    "graviton_migration",
    "rds_rightsizing",
    "rds_nonprod_schedule",
    "ecs_fargate_rightsizing",
    "nat_gateway_cleanup",
    "ebs_cleanup_tuning",
    "lambda_memory_rightsizing",
    "rds_aurora_storage_tuning",
    "s3_lifecycle_storage_class",
]

LEVER_DEFINITIONS: dict[LeverKey, dict[str, Any]] = {
    "nonprod_schedule": {
        "label": "Turn off non-prod on a schedule",
        "bucket": "Stop waste",
        "owner_relevance": 3.0,
        "summary": "Off-hours stop/start schedules remove avoidable spend from dev and test compute.",
    },
    "commitments": {
        "label": "Savings Plans / Reserved commitments",
        "bucket": "Buy discounts",
        "owner_relevance": 2.7,
        "summary": "Steady-state usage is often cheaper under commitments than on-demand rates.",
    },
    "ec2_rightsizing": {
        "label": "EC2 rightsizing",
        "bucket": "Rightsize",
        "owner_relevance": 2.9,
        "summary": "Oversized EC2 instances can often be moved to smaller shapes with limited risk.",
    },
    "graviton_migration": {
        "label": "Graviton migration",
        "bucket": "Rightsize",
        "owner_relevance": 2.6,
        "summary": "Modern Graviton families can lower compute cost for compatible Linux workloads.",
    },
    "rds_rightsizing": {
        "label": "RDS rightsizing",
        "bucket": "Rightsize",
        "owner_relevance": 2.6,
        "summary": "Database classes frequently drift above the capacity their current demand requires.",
    },
    "rds_nonprod_schedule": {
        "label": "RDS non-prod stop/start schedule",
        "bucket": "Stop waste",
        "owner_relevance": 2.8,
        "summary": "Non-prod databases often run around the clock even when teams only use them in working hours.",
    },
    "ecs_fargate_rightsizing": {
        "label": "ECS / Fargate rightsizing",
        "bucket": "Rightsize",
        "owner_relevance": 2.5,
        "summary": "Container CPU and memory reservations can exceed actual service demand for long periods.",
    },
    "nat_gateway_cleanup": {
        "label": "NAT Gateway cleanup",
        "bucket": "Stop waste",
        "owner_relevance": 2.4,
        "summary": "Low-value NAT gateways keep accruing hourly charges even with very little traffic.",
    },
    "ebs_cleanup_tuning": {
        "label": "EBS cleanup and tuning",
        "bucket": "Storage cleanup",
        "owner_relevance": 2.4,
        "summary": "Unattached or over-provisioned EBS volumes create steady storage waste every month.",
    },
    "lambda_memory_rightsizing": {
        "label": "Lambda memory rightsizing",
        "bucket": "Rightsize",
        "owner_relevance": 2.2,
        "summary": "Over-allocated Lambda memory increases compute cost on every invocation.",
    },
    "rds_aurora_storage_tuning": {
        "label": "RDS / Aurora storage tuning",
        "bucket": "Storage cleanup",
        "owner_relevance": 2.1,
        "summary": "Database storage classes and provisioned performance often have cheaper equivalents.",
    },
    "s3_lifecycle_storage_class": {
        "label": "S3 lifecycle / storage class optimization",
        "bucket": "Storage cleanup",
        "owner_relevance": 2.0,
        "summary": "Buckets without lifecycle rules can retain standard storage for data that no longer needs it.",
    },
}

BUCKET_ORDER: list[ActionBucket] = [
    "Stop waste",
    "Rightsize",
    "Buy discounts",
    "Storage cleanup",
]


def lever_bucket(lever_key: LeverKey) -> ActionBucket:
    """Return the dashboard bucket for a first-class savings lever."""
    return LEVER_DEFINITIONS[lever_key]["bucket"]


def lever_label(lever_key: LeverKey) -> str:
    """Return the human label for a first-class savings lever."""
    return str(LEVER_DEFINITIONS[lever_key]["label"])


def lever_owner_relevance(lever_key: LeverKey) -> float:
    """Return a stable owner-relevance score used by action ranking."""
    return float(LEVER_DEFINITIONS[lever_key]["owner_relevance"])


def lever_summary(lever_key: LeverKey) -> str:
    """Return the default owner-facing summary for a lever."""
    return str(LEVER_DEFINITIONS[lever_key]["summary"])
