"""Native RDS non-prod schedule recommendations."""

from __future__ import annotations

from typing import Any

from finops_pack.analysis.pricing import MONTHLY_HOURS, estimate_rds_hourly_cost
from finops_pack.analysis.schedule_recommendations import calculate_off_hours_ratio
from finops_pack.domain.models import AccountMapEntry, ActionOpportunity
from finops_pack.orchestration.config import ScheduleConfig


def _is_stoppable_nonprod_db(
    item: dict[str, Any],
    *,
    account_lookup: dict[str, AccountMapEntry],
) -> tuple[bool, str]:
    account_id = str(item.get("accountId") or "")
    account_entry = account_lookup.get(account_id)
    if account_entry is None or account_entry.environment != "nonprod":
        return False, "Excluded because the account is not classified as non-prod."

    engine = str(item.get("engine") or "").lower()
    if engine.startswith("aurora"):
        return (
            False,
            "Excluded because Aurora clusters are not handled by the native stop/start path.",
        )

    status = str(item.get("status") or "").lower()
    if status != "available":
        return False, f"Excluded because DB instance status is {status or 'unknown'}."

    if bool(item.get("multiAz")):
        return False, "Excluded because Multi-AZ DB instances are not scheduled natively."

    if str(item.get("dbClusterIdentifier") or ""):
        return False, "Excluded because clustered RDS deployments are not scheduled natively."

    if str(item.get("readReplicaSourceDBInstanceIdentifier") or ""):
        return False, "Excluded because read replicas are not scheduled natively."

    read_replicas = item.get("readReplicaDBInstanceIdentifiers", [])
    if isinstance(read_replicas, list) and read_replicas:
        return False, "Excluded because primary instances with replicas are not scheduled natively."

    return True, "Single-AZ, standalone, available non-prod DB instance."


def build_rds_schedule_actions(
    inventory_snapshot: dict[str, Any] | None,
    *,
    account_map: list[AccountMapEntry],
    schedule: ScheduleConfig,
) -> list[ActionOpportunity]:
    """Build native RDS stop/start actions for non-prod DB instances."""
    if not isinstance(inventory_snapshot, dict):
        return []

    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    account_lookup = {entry.account_id: entry for entry in account_map}
    off_hours_ratio = calculate_off_hours_ratio(schedule)

    candidates: list[dict[str, Any]] = []
    confidence_levels: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        is_candidate, candidate_reason = _is_stoppable_nonprod_db(
            raw_item,
            account_lookup=account_lookup,
        )
        if not is_candidate:
            continue

        db_instance_class = str(raw_item.get("dbInstanceClass") or "")
        hourly_cost_estimate, confidence = estimate_rds_hourly_cost(db_instance_class)
        confidence_levels.add(confidence)
        monthly_savings = round(hourly_cost_estimate * MONTHLY_HOURS * off_hours_ratio, 2)
        account_name = str(raw_item.get("accountName") or raw_item.get("accountId") or "Unknown")
        candidates.append(
            {
                "account_name": account_name,
                "account_id": str(raw_item.get("accountId") or "Unknown"),
                "region": str(raw_item.get("region") or ""),
                "resource_name": str(raw_item.get("dbInstanceIdentifier") or ""),
                "resource_id": str(raw_item.get("dbInstanceIdentifier") or ""),
                "detail": (
                    f"{db_instance_class or 'db instance'} · {raw_item.get('engine') or 'engine'}"
                    f" · {candidate_reason}"
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

    confidence = "high" if confidence_levels == {"high"} else "medium"
    return [
        ActionOpportunity(
            bucket="Stop waste",
            lever_key="rds_nonprod_schedule",
            action_label=(
                f"Stop {len(candidates)} non-prod RDS "
                f"{'instance' if len(candidates) == 1 else 'instances'} off-hours"
            ),
            monthly_savings=round(
                sum(float(item["monthly_savings"]) for item in candidates),
                2,
            ),
            risk="medium",
            effort="low",
            confidence=confidence,
            source_label="Native finops-pack",
            why_it_matters=(
                "Non-production databases often run around the clock even though teams only use "
                "them during business hours."
            ),
            what_to_do_first=(
                "Confirm each database can tolerate daily stop/start behavior, then roll the "
                "schedule out to one dev database before applying it more broadly."
            ),
            evidence_summary=(
                f"{len(candidates)} stoppable non-prod DB instance(s) across "
                f"{len({item['account_id'] for item in candidates})} account(s). Savings are "
                "estimated from DB class heuristics and configured off-hours."
            ),
            opportunity_count=len(candidates),
            resource_count=len(candidates),
            account_count=len({item["account_id"] for item in candidates}),
            account_names=sorted({item["account_name"] for item in candidates}),
            supporting_items=[
                {
                    **item,
                    "monthly_savings_display": f"${float(item['monthly_savings']):,.2f}/mo",
                }
                for item in candidates[:5]
            ],
        )
    ]
