# ruff: noqa: E501
"""Native NAT Gateway cleanup and redesign analysis."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from finops_pack.analysis.action_builders import build_grouped_action
from finops_pack.analysis.pricing import estimate_nat_gateway_monthly_cost

LOW_TRAFFIC_BYTES_14D = 5 * 1024 * 1024 * 1024


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


def build_nat_gateway_actions(inventory_snapshot: dict[str, Any] | None) -> list:
    """Build native NAT Gateway delete or redesign opportunities."""
    if not isinstance(inventory_snapshot, dict):
        return []
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    delete_candidates: list[dict[str, Any]] = []
    redesign_candidates: list[dict[str, Any]] = []
    gateways_by_vpc: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        nat_gateway_id = str(item.get("natGatewayId") or "")
        if not nat_gateway_id or str(item.get("state") or "").lower() not in {
            "available",
            "pending",
        }:
            continue
        bytes_total = float(item.get("avgBytesOut14d") or 0.0) + float(
            item.get("avgBytesIn14d") or 0.0
        )
        account_name = str(item.get("accountName") or item.get("accountId") or "Unknown")
        base_item = {
            "account_name": account_name,
            "account_id": str(item.get("accountId") or "Unknown"),
            "region": str(item.get("region") or ""),
            "resource_name": nat_gateway_id,
            "resource_id": nat_gateway_id,
            "detail": f"VPC {item.get('vpcId') or 'unknown'} · ~{bytes_total / (1024**3):.2f} GiB over 14d",
            "monthly_savings": estimate_nat_gateway_monthly_cost(),
        }
        gateways_by_vpc[(base_item["account_id"], str(item.get("vpcId") or ""))].append(base_item)
        if bytes_total <= LOW_TRAFFIC_BYTES_14D:
            delete_candidates.append(base_item)

    for (_account_id, vpc_id), gateways in gateways_by_vpc.items():
        if vpc_id and len(gateways) > 1:
            redesign_candidates.extend(gateways[1:])

    actions = []
    if delete_candidates:
        actions.append(
            build_grouped_action(
                bucket="Stop waste",
                lever_key="nat_gateway_cleanup",
                action_label=f"Delete {len(delete_candidates)} low-value NAT gateway{'s' if len(delete_candidates) != 1 else ''}",
                source_label="Native finops-pack",
                items=sorted(
                    delete_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="medium",
                confidence="medium",
                why_it_matters="NAT gateways keep charging an hourly rate even when recent traffic is almost negligible.",
                what_to_do_first="Confirm each NAT gateway still has a business need, then remove one low-traffic gateway after validating routing impact.",
                evidence_summary=f"{len(delete_candidates)} NAT gateway(s) showed very low traffic over the recent CloudWatch window.",
            )
        )

    if redesign_candidates:
        actions.append(
            build_grouped_action(
                bucket="Stop waste",
                lever_key="nat_gateway_cleanup",
                action_label=f"Consolidate or redesign {len(redesign_candidates)} NAT gateway{'s' if len(redesign_candidates) != 1 else ''}",
                source_label="Mixed / derived",
                items=sorted(
                    redesign_candidates,
                    key=lambda item: (-float(item["monthly_savings"]), item["resource_id"]),
                ),
                risk="medium",
                effort="high",
                confidence="medium",
                why_it_matters="Multiple NAT gateways in the same VPC can indicate duplicate spend that might be removed with a topology review.",
                what_to_do_first="Review route tables and endpoint options in one VPC before removing duplicated NAT capacity.",
                evidence_summary=f"{len(redesign_candidates)} NAT gateway(s) appear to be duplicate or redesign candidates inside VPCs with more than one gateway.",
            )
        )

    return actions
