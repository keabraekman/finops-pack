"""Helpers for enabling Cost Optimization Hub."""

from __future__ import annotations

from typing import Any, Literal, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

EnrollmentStatus = Literal["Active", "Inactive"]


def update_enrollment_status(
    session: boto3.Session,
    *,
    status: EnrollmentStatus,
    region_name: str = "us-east-1",
    include_member_accounts: bool = False,
) -> EnrollmentStatus:
    """Update the Cost Optimization Hub enrollment status for the current account."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    update_kwargs: dict[str, object] = {"status": status}
    if include_member_accounts:
        update_kwargs["includeMemberAccounts"] = True

    try:
        response = client.update_enrollment_status(**update_kwargs)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to update Cost Optimization Hub enrollment status to {status}: {exc}"
        ) from exc

    resolved_status = response.get("status")
    if resolved_status not in {"Active", "Inactive"}:
        raise RuntimeError("UpdateEnrollmentStatus response did not include a valid status.")

    return cast(EnrollmentStatus, resolved_status)


def enable_cost_optimization_hub(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
) -> EnrollmentStatus:
    """Enable Cost Optimization Hub for the current account."""
    return update_enrollment_status(session, status="Active", region_name=region_name)


def list_recommendation_summaries(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    filter_expression: dict[str, object] | None = None,
    group_by: str | None = None,
    metrics: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Collect paginated Cost Optimization Hub recommendation summaries."""
    client = session.client("cost-optimization-hub", region_name=region_name)
    paginator = client.get_paginator("list_recommendation_summaries")

    request_kwargs: dict[str, object] = {}
    if filter_expression is not None:
        request_kwargs["filter"] = filter_expression
    if group_by is not None:
        request_kwargs["groupBy"] = group_by
    if metrics is not None:
        request_kwargs["metrics"] = metrics

    try:
        pages = list(paginator.paginate(**request_kwargs))
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to list Cost Optimization Hub recommendation summaries: {exc}"
        ) from exc

    items: list[dict[str, Any]] = []
    deduped_savings_candidates: list[float] = []
    currency_code: str | None = None
    resolved_group_by: str | None = None
    resolved_metrics: dict[str, Any] | None = None

    for page in pages:
        items.extend(page.get("items", []))

        estimated_total_deduped_savings = page.get("estimatedTotalDedupedSavings")
        if isinstance(estimated_total_deduped_savings, (int, float)):
            deduped_savings_candidates.append(float(estimated_total_deduped_savings))

        if currency_code is None and isinstance(page.get("currencyCode"), str):
            currency_code = page["currencyCode"]
        if resolved_group_by is None and isinstance(page.get("groupBy"), str):
            resolved_group_by = page["groupBy"]
        if resolved_metrics is None and isinstance(page.get("metrics"), dict):
            resolved_metrics = page["metrics"]

    return {
        "operation": "ListRecommendationSummaries",
        "request": request_kwargs,
        "pages": pages,
        "items": items,
        "itemCount": len(items),
        "estimatedTotalDedupedSavings": (
            max(deduped_savings_candidates) if deduped_savings_candidates else None
        ),
        "currencyCode": currency_code,
        "groupBy": resolved_group_by,
        "metrics": resolved_metrics,
    }


def list_recommendations(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    filter_expression: dict[str, object] | None = None,
    order_by: dict[str, object] | None = None,
    include_all_recommendations: bool = True,
) -> dict[str, Any]:
    """Collect paginated Cost Optimization Hub recommendations."""
    client = session.client("cost-optimization-hub", region_name=region_name)
    paginator = client.get_paginator("list_recommendations")

    request_kwargs: dict[str, object] = {
        "includeAllRecommendations": include_all_recommendations,
    }
    if filter_expression is not None:
        request_kwargs["filter"] = filter_expression
    if order_by is not None:
        request_kwargs["orderBy"] = order_by

    try:
        pages = list(paginator.paginate(**request_kwargs))
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to list Cost Optimization Hub recommendations: {exc}") from exc

    items: list[dict[str, Any]] = []
    for page in pages:
        items.extend(page.get("items", []))

    return {
        "operation": "ListRecommendations",
        "request": request_kwargs,
        "pages": pages,
        "items": items,
        "itemCount": len(items),
    }
