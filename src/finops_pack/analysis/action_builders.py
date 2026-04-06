"""Shared helpers for building normalized action opportunities."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from finops_pack.domain.models import (
    ActionBucket,
    ActionOpportunity,
    ActionPriority,
    ActionSourceLabel,
    LeverKey,
)


def format_monthly_savings_display(monthly_savings: float) -> str:
    """Render a stable monthly savings display string."""
    return f"${monthly_savings:,.2f}/mo"


def build_grouped_action(
    *,
    bucket: ActionBucket,
    lever_key: LeverKey,
    action_label: str,
    source_label: ActionSourceLabel,
    items: Sequence[dict[str, Any]],
    risk: ActionPriority,
    effort: ActionPriority,
    confidence: ActionPriority,
    why_it_matters: str,
    what_to_do_first: str,
    evidence_summary: str,
) -> ActionOpportunity:
    """Create a normalized action from grouped resource items."""
    normalized_items = list(items)
    account_names = sorted(
        {
            str(item["account_name"])
            for item in normalized_items
            if isinstance(item.get("account_name"), str) and item.get("account_name")
        }
    )
    return ActionOpportunity(
        bucket=bucket,
        lever_key=lever_key,
        action_label=action_label,
        monthly_savings=round(
            sum(float(item.get("monthly_savings") or 0.0) for item in normalized_items),
            2,
        ),
        risk=risk,
        effort=effort,
        confidence=confidence,
        source_label=source_label,
        why_it_matters=why_it_matters,
        what_to_do_first=what_to_do_first,
        evidence_summary=evidence_summary,
        opportunity_count=len(normalized_items),
        resource_count=len(normalized_items),
        account_count=len(account_names) or 1,
        account_names=account_names,
        supporting_items=[
            {
                **item,
                "monthly_savings_display": format_monthly_savings_display(
                    float(item.get("monthly_savings") or 0.0)
                ),
            }
            for item in normalized_items[:5]
        ],
    )
