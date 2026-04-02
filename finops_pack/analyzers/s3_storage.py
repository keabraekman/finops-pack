# ruff: noqa: E501
"""Native S3 lifecycle and storage class optimization analysis."""

from __future__ import annotations

from typing import Any

from finops_pack.analyzers.action_builders import build_grouped_action
from finops_pack.analyzers.pricing import estimate_s3_transition_savings


def build_s3_storage_actions(inventory_snapshot: dict[str, Any] | None) -> list:
    """Build native S3 lifecycle and storage class optimization actions."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    candidates: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        bucket_name = str(item.get("bucketName") or "")
        if not bucket_name or bool(item.get("hasLifecycleRules")):
            continue
        standard_storage_gib = float(item.get("standardStorageGiB") or 0.0)
        if standard_storage_gib < 50:
            continue
        monthly_savings = estimate_s3_transition_savings(
            standard_storage_gib=standard_storage_gib,
            eligible_fraction=0.35,
            target="standard_ia",
        )
        if monthly_savings <= 1:
            continue
        candidates.append(
            {
                "account_name": str(item.get("accountName") or item.get("accountId") or "Unknown"),
                "account_id": str(item.get("accountId") or "Unknown"),
                "region": str(item.get("region") or "global"),
                "resource_name": bucket_name,
                "resource_id": bucket_name,
                "detail": f"~{standard_storage_gib:,.1f} GiB in standard storage with no lifecycle rules",
                "monthly_savings": monthly_savings,
            }
        )

    if not candidates:
        return []

    return [
        build_grouped_action(
            bucket="Storage cleanup",
            lever_key="s3_lifecycle_storage_class",
            action_label=f"Apply S3 lifecycle rules to {len(candidates)} bucket{'s' if len(candidates) != 1 else ''}",
            source_label="Native finops-pack",
            items=sorted(
                candidates, key=lambda item: (-float(item["monthly_savings"]), item["resource_id"])
            ),
            risk="low",
            effort="medium",
            confidence="medium",
            why_it_matters="Cold or infrequently used objects can often move out of standard storage without affecting daily operations.",
            what_to_do_first="Start with one bucket, add a conservative lifecycle transition rule, and confirm retention expectations with the owner.",
            evidence_summary=f"{len(candidates)} bucket(s) had enough standard storage and no lifecycle rules, using a conservative cold-data eligibility assumption.",
        )
    ]
