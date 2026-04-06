# ruff: noqa: E501
"""Native commitment analysis for steady-state EC2 and RDS usage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from finops_pack.analysis.action_builders import build_grouped_action
from finops_pack.analysis.pricing import (
    MONTHLY_HOURS,
    estimate_ec2_hourly_cost,
    estimate_rds_hourly_cost,
)
from finops_pack.domain.models import AccountMapEntry, NormalizedRecommendation

COMPUTE_SP_DISCOUNT = 0.22
EC2_RI_DISCOUNT = 0.28
RDS_RI_DISCOUNT = 0.26
STEADY_STATE_MIN_DAYS = 21


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _age_days(timestamp: str | None) -> int | None:
    parsed = _parse_iso8601(timestamp)
    if parsed is None:
        return None
    return max(0, (datetime.now(UTC) - parsed).days)


def _is_prod_account(account_id: str, account_lookup: dict[str, AccountMapEntry]) -> bool:
    account_entry = account_lookup.get(account_id)
    return account_entry is not None and account_entry.environment == "prod"


def _extract_ce_compute_sp_items(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    items = snapshot.get("items", [])
    if not isinstance(items, list):
        return []

    extracted: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        estimated_savings = item.get("EstimatedMonthlySavingsAmount") or item.get(
            "EstimatedMonthlySavings"
        )
        try:
            monthly_savings = float(estimated_savings)
        except (TypeError, ValueError):
            continue
        detail_id = str(item.get("RecommendationDetailId") or "compute-savings-plan")
        extracted.append(
            {
                "account_name": "Payer / shared coverage",
                "account_id": "shared",
                "region": "us-east-1",
                "resource_name": detail_id,
                "resource_id": detail_id,
                "detail": (
                    f"Payment option {item.get('PaymentOption') or 'NO_UPFRONT'} · "
                    f"term {item.get('TermInYears') or 'ONE_YEAR'}"
                ),
                "monthly_savings": round(monthly_savings, 2),
            }
        )
    return extracted


def build_commitment_actions(
    *,
    account_map: list[AccountMapEntry],
    ec2_inventory_snapshot: dict[str, Any] | None,
    rds_inventory_snapshot: dict[str, Any] | None,
    ce_savings_plan_snapshot: dict[str, Any] | None = None,
    recommendations: list[NormalizedRecommendation] | None = None,
) -> list:
    """Build native and fallback commitment actions."""
    account_lookup = {entry.account_id: entry for entry in account_map}
    recommendation_list = recommendations or []
    has_compute_sp = any(rec.action_type == "PurchaseSavingsPlans" for rec in recommendation_list)
    has_reserved_db = any(
        rec.action_type == "PurchaseReservedInstances"
        and rec.current_resource_type == "RdsDbInstance"
        for rec in recommendation_list
    )
    has_reserved_ec2 = any(
        rec.action_type == "PurchaseReservedInstances"
        and rec.current_resource_type != "RdsDbInstance"
        for rec in recommendation_list
    )

    actions = []

    if not has_compute_sp:
        ce_items = _extract_ce_compute_sp_items(ce_savings_plan_snapshot)
        if ce_items:
            actions.append(
                build_grouped_action(
                    bucket="Buy discounts",
                    lever_key="commitments",
                    action_label=f"Buy {len(ce_items)} compute savings plan{'s' if len(ce_items) != 1 else ''}",
                    source_label="CE fallback",
                    items=ce_items,
                    risk="low",
                    effort="low",
                    confidence="high",
                    why_it_matters="Cost Explorer found steady compute usage that is likely cheaper under Savings Plans coverage.",
                    what_to_do_first="Review the recommended commitment payment option and term before purchasing shared compute coverage.",
                    evidence_summary=f"{len(ce_items)} Cost Explorer Savings Plans recommendation detail(s) were returned for this run.",
                )
            )

    ec2_items = (
        ec2_inventory_snapshot.get("items", [])
        if isinstance(ec2_inventory_snapshot, dict)
        and isinstance(ec2_inventory_snapshot.get("items"), list)
        else []
    )
    if not has_reserved_ec2:
        grouped_by_family: dict[str, list[dict[str, Any]]] = {}
        for item in ec2_items:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("accountId") or "")
            if not _is_prod_account(account_id, account_lookup):
                continue
            if str(item.get("state") or "").lower() != "running":
                continue
            age_in_days = _age_days(str(item.get("launchTime") or ""))
            if age_in_days is not None and age_in_days < STEADY_STATE_MIN_DAYS:
                continue
            instance_type = str(item.get("instanceType") or "")
            family = instance_type.partition(".")[0]
            hourly_cost, _ = estimate_ec2_hourly_cost(instance_type)
            monthly_savings = round(hourly_cost * MONTHLY_HOURS * EC2_RI_DISCOUNT, 2)
            if monthly_savings <= 15:
                continue
            grouped_by_family.setdefault(family, []).append(
                {
                    "account_name": str(item.get("accountName") or account_id or "Unknown"),
                    "account_id": account_id,
                    "region": str(item.get("region") or ""),
                    "resource_name": str(
                        item.get("name") or item.get("instanceId") or instance_type
                    ),
                    "resource_id": str(item.get("instanceId") or instance_type),
                    "detail": f"{instance_type} · running steadily in a prod account",
                    "monthly_savings": monthly_savings,
                }
            )

        grouped_by_family = {
            family: items for family, items in grouped_by_family.items() if len(items) >= 2
        }
        if grouped_by_family:
            best_family, best_items = max(
                grouped_by_family.items(),
                key=lambda entry: sum(float(item["monthly_savings"]) for item in entry[1]),
            )
            actions.append(
                build_grouped_action(
                    bucket="Buy discounts",
                    lever_key="commitments",
                    action_label=f"Buy {len(best_items)} reserved instance commitment{'s' if len(best_items) != 1 else ''}",
                    source_label="Mixed / derived",
                    items=best_items,
                    risk="low",
                    effort="medium",
                    confidence="medium",
                    why_it_matters="Steady EC2 usage in the same family often qualifies for cheaper reserved pricing than on-demand rates.",
                    what_to_do_first="Validate that these instances are likely to remain on the same family for the next year before buying reserved coverage.",
                    evidence_summary=f"{len(best_items)} steady prod EC2 instance(s) in the {best_family} family cleared the commitment threshold using conservative pricing heuristics.",
                )
            )

    rds_items = (
        rds_inventory_snapshot.get("items", [])
        if isinstance(rds_inventory_snapshot, dict)
        and isinstance(rds_inventory_snapshot.get("items"), list)
        else []
    )
    if not has_reserved_db:
        reserved_db_candidates: list[dict[str, Any]] = []
        for item in rds_items:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("accountId") or "")
            if not _is_prod_account(account_id, account_lookup):
                continue
            if str(item.get("status") or "").lower() != "available":
                continue
            if str(item.get("dbClusterIdentifier") or ""):
                continue
            db_instance_class = str(item.get("dbInstanceClass") or "")
            hourly_cost, _ = estimate_rds_hourly_cost(db_instance_class)
            monthly_savings = round(hourly_cost * MONTHLY_HOURS * RDS_RI_DISCOUNT, 2)
            if monthly_savings <= 20:
                continue
            reserved_db_candidates.append(
                {
                    "account_name": str(item.get("accountName") or account_id or "Unknown"),
                    "account_id": account_id,
                    "region": str(item.get("region") or ""),
                    "resource_name": str(item.get("dbInstanceIdentifier") or db_instance_class),
                    "resource_id": str(item.get("dbInstanceIdentifier") or db_instance_class),
                    "detail": f"{db_instance_class} · {item.get('engine') or 'engine'}",
                    "monthly_savings": monthly_savings,
                }
            )

        if reserved_db_candidates:
            actions.append(
                build_grouped_action(
                    bucket="Buy discounts",
                    lever_key="commitments",
                    action_label=(
                        f"Buy {len(reserved_db_candidates)} reserved DB instance commitment"
                        f"{'s' if len(reserved_db_candidates) != 1 else ''}"
                    ),
                    source_label="Mixed / derived",
                    items=reserved_db_candidates,
                    risk="low",
                    effort="medium",
                    confidence="medium",
                    why_it_matters="Steady production database workloads are often cheaper on reserved DB pricing than on-demand rates.",
                    what_to_do_first="Confirm the database engine and class are expected to stay stable before buying reserved DB coverage.",
                    evidence_summary=f"{len(reserved_db_candidates)} steady prod RDS instance(s) met the conservative reserved DB threshold.",
                )
            )

    return actions
