"""Native EBS cleanup and tuning recommendations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from finops_pack.models import ActionOpportunity

EBS_STORAGE_RATES_USD_PER_GIB_MONTH = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.025,
    "standard": 0.05,
}
GP3_BASELINE_IOPS = 3000
GP3_BASELINE_THROUGHPUT = 125
GP3_ADDITIONAL_IOPS_RATE = 0.005
GP3_ADDITIONAL_THROUGHPUT_RATE = 0.04
UNATTACHED_HIGH_CONFIDENCE_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


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


def _age_days(create_time: str | None) -> int | None:
    parsed = _parse_iso8601(create_time)
    if parsed is None:
        return None
    return max(0, (_now() - parsed).days)


def _pluralize(label: str, count: int) -> str:
    return label if count == 1 else f"{label}s"


def _sum_monthly_savings(items: list[dict[str, Any]]) -> float:
    return round(sum(float(item["monthly_savings"]) for item in items), 2)


def _format_volume_detail(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_name": item["account_name"],
        "account_id": item["account_id"],
        "region": item["region"],
        "resource_name": item.get("name") or item["volume_id"],
        "resource_id": item["volume_id"],
        "detail": item["detail"],
        "monthly_savings_display": f"${float(item['monthly_savings']):,.2f}/mo",
    }


def build_native_ebs_actions(inventory_snapshot: dict[str, Any] | None) -> list[ActionOpportunity]:
    """Build conservative native EBS cleanup and tuning actions."""
    if not isinstance(inventory_snapshot, dict):
        return []

    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    unattached: list[dict[str, Any]] = []
    gp2_to_gp3: list[dict[str, Any]] = []
    overprovisioned: list[dict[str, Any]] = []

    unattached_volume_ids: set[str] = set()

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        volume_id = raw_item.get("volumeId")
        if not isinstance(volume_id, str) or not volume_id:
            continue

        account_name = str(raw_item.get("accountName") or raw_item.get("accountId") or "Unknown")
        account_id = str(raw_item.get("accountId") or "Unknown")
        region = str(raw_item.get("region") or "")
        name = str(raw_item.get("name") or "")
        volume_type = str(raw_item.get("volumeType") or "").lower()
        size_gib = int(raw_item.get("sizeGiB") or 0)
        iops = int(raw_item.get("iops") or 0)
        throughput = int(raw_item.get("throughput") or 0)
        attachment_count = int(raw_item.get("attachmentCount") or 0)
        state = str(raw_item.get("state") or "").lower()
        age_in_days = _age_days(str(raw_item.get("createTime") or ""))

        if attachment_count == 0 and state == "available":
            rate = EBS_STORAGE_RATES_USD_PER_GIB_MONTH.get(volume_type, 0.08)
            monthly_savings = round(size_gib * rate, 2)
            unattached_volume_ids.add(volume_id)
            unattached.append(
                {
                    "account_name": account_name,
                    "account_id": account_id,
                    "region": region,
                    "name": name,
                    "volume_id": volume_id,
                    "monthly_savings": monthly_savings,
                    "detail": (
                        f"{size_gib} GiB {volume_type or 'volume'}"
                        + (
                            f" · unattached for at least {age_in_days} day(s)"
                            if age_in_days is not None
                            else ""
                        )
                    ),
                }
            )
            continue

        if volume_type == "gp2" and size_gib > 0:
            monthly_savings = round(size_gib * 0.02, 2)
            if monthly_savings > 0:
                gp2_to_gp3.append(
                    {
                        "account_name": account_name,
                        "account_id": account_id,
                        "region": region,
                        "name": name,
                        "volume_id": volume_id,
                        "monthly_savings": monthly_savings,
                        "detail": f"{size_gib} GiB gp2 volume",
                    }
                )

        if volume_type == "gp3":
            excess_iops = max(0, iops - GP3_BASELINE_IOPS)
            excess_throughput = max(0, throughput - GP3_BASELINE_THROUGHPUT)
            monthly_savings = round(
                (excess_iops * GP3_ADDITIONAL_IOPS_RATE)
                + (excess_throughput * GP3_ADDITIONAL_THROUGHPUT_RATE),
                2,
            )
            if monthly_savings > 0:
                details: list[str] = []
                if excess_iops:
                    details.append(f"{excess_iops} excess IOPS")
                if excess_throughput:
                    details.append(f"{excess_throughput} excess MB/s throughput")
                overprovisioned.append(
                    {
                        "account_name": account_name,
                        "account_id": account_id,
                        "region": region,
                        "name": name,
                        "volume_id": volume_id,
                        "monthly_savings": monthly_savings,
                        "detail": " · ".join(details),
                    }
                )

    actions: list[ActionOpportunity] = []

    if unattached:
        high_confidence_count = sum(
            1
            for item in unattached
            if (
                (
                    age := _age_days(
                        next(
                            (
                                str(raw_item.get("createTime") or "")
                                for raw_item in raw_items
                                if isinstance(raw_item, dict)
                                and raw_item.get("volumeId") == item["volume_id"]
                            ),
                            "",
                        )
                    )
                )
                is not None
                and age >= UNATTACHED_HIGH_CONFIDENCE_DAYS
            )
        )
        actions.append(
            ActionOpportunity(
                bucket="Storage cleanup",
                lever_key="ebs_cleanup_tuning",
                action_label=(
                    "Delete "
                    f"{len(unattached)} unattached EBS "
                    f"{_pluralize('volume', len(unattached))}"
                ),
                monthly_savings=_sum_monthly_savings(unattached),
                risk="medium",
                effort="low",
                confidence="high" if high_confidence_count == len(unattached) else "medium",
                source_label="Native finops-pack",
                why_it_matters=(
                    "Unattached EBS volumes keep billing every month even when nothing is using "
                    "them."
                ),
                what_to_do_first=(
                    "Confirm each volume is no longer needed, snapshot anything important, then "
                    "delete the orphaned volume."
                ),
                evidence_summary=(
                    f"{len(unattached)} unattached volume(s) across "
                    f"{len({item['account_id'] for item in unattached})} account(s)."
                ),
                opportunity_count=len(unattached),
                resource_count=len(unattached),
                account_count=len({item["account_id"] for item in unattached}),
                account_names=sorted({item["account_name"] for item in unattached}),
                supporting_items=[_format_volume_detail(item) for item in unattached[:5]],
            )
        )

    attached_gp2_to_gp3 = [
        item for item in gp2_to_gp3 if item["volume_id"] not in unattached_volume_ids
    ]
    if attached_gp2_to_gp3:
        actions.append(
            ActionOpportunity(
                bucket="Storage cleanup",
                lever_key="ebs_cleanup_tuning",
                action_label=(
                    f"Migrate {len(attached_gp2_to_gp3)} gp2 EBS "
                    f"{_pluralize('volume', len(attached_gp2_to_gp3))} to gp3"
                ),
                monthly_savings=_sum_monthly_savings(attached_gp2_to_gp3),
                risk="low",
                effort="low",
                confidence="high",
                source_label="Native finops-pack",
                why_it_matters=(
                    "gp3 usually delivers the same baseline storage value at a lower monthly "
                    "price than gp2."
                ),
                what_to_do_first=(
                    "Change the volume type from gp2 to gp3 during a normal maintenance window "
                    "and confirm workload performance stays healthy."
                ),
                evidence_summary=(
                    f"{len(attached_gp2_to_gp3)} gp2 volume(s) can move to gp3 using current "
                    "default storage pricing assumptions."
                ),
                opportunity_count=len(attached_gp2_to_gp3),
                resource_count=len(attached_gp2_to_gp3),
                account_count=len({item["account_id"] for item in attached_gp2_to_gp3}),
                account_names=sorted({item["account_name"] for item in attached_gp2_to_gp3}),
                supporting_items=[_format_volume_detail(item) for item in attached_gp2_to_gp3[:5]],
            )
        )

    attached_overprovisioned = [
        item for item in overprovisioned if item["volume_id"] not in unattached_volume_ids
    ]
    if attached_overprovisioned:
        actions.append(
            ActionOpportunity(
                bucket="Storage cleanup",
                lever_key="ebs_cleanup_tuning",
                action_label=(
                    "Reduce provisioned performance on "
                    f"{len(attached_overprovisioned)} gp3 EBS "
                    f"{_pluralize('volume', len(attached_overprovisioned))}"
                ),
                monthly_savings=_sum_monthly_savings(attached_overprovisioned),
                risk="medium",
                effort="medium",
                confidence="medium",
                source_label="Native finops-pack",
                why_it_matters=(
                    "Some gp3 volumes are paying for provisioned IOPS or throughput above the "
                    "default baseline."
                ),
                what_to_do_first=(
                    "Check recent performance before lowering gp3 IOPS or throughput toward "
                    "baseline levels."
                ),
                evidence_summary=(
                    f"{len(attached_overprovisioned)} gp3 volume(s) are paying above baseline "
                    "for IOPS or throughput."
                ),
                opportunity_count=len(attached_overprovisioned),
                resource_count=len(attached_overprovisioned),
                account_count=len({item["account_id"] for item in attached_overprovisioned}),
                account_names=sorted({item["account_name"] for item in attached_overprovisioned}),
                supporting_items=[
                    _format_volume_detail(item) for item in attached_overprovisioned[:5]
                ],
            )
        )

    return actions
