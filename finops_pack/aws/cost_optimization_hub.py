"""Helpers for enabling Cost Optimization Hub."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Literal, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.models import NormalizedRecommendation, Recommendation, SavingsRange

EnrollmentStatus = Literal["Active", "Inactive"]
CohRecommendationCategory = Literal[
    "rightsizing / idle deletion",
    "commitment (SP/RI)",
    "storage/network/etc.",
]
COH_CATEGORY_RIGHTSIZING: CohRecommendationCategory = "rightsizing / idle deletion"
COH_CATEGORY_COMMITMENT: CohRecommendationCategory = "commitment (SP/RI)"
COH_CATEGORY_OTHER: CohRecommendationCategory = "storage/network/etc."
COH_DETAIL_TOP_N = 20
COMMITMENT_ACTION_TYPES = {"PurchaseSavingsPlans", "PurchaseReservedInstances"}
RIGHTSIZING_ACTION_TYPES = {"Delete", "MigrateToGraviton", "Rightsize", "ScaleIn", "Stop"}
COMMITMENT_TYPE_MARKERS = ("Reserved", "SavingsPlans")
RIGHTSIZING_TYPE_MARKERS = (
    "AutoScalingGroup",
    "Cluster",
    "Ec2Instance",
    "EcsService",
    "LambdaFunction",
    "RdsDbInstance",
)
CONDITIONAL_RIGHTSIZING_ACTION_TYPES = {"Modernize", "Replace", "Terminate", "Upgrade"}
THROTTLING_ERROR_CODES = {
    "RequestLimitExceeded",
    "SlowDown",
    "ThrottledException",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
}
DEFAULT_MAX_RETRY_ATTEMPTS = 4
SAFE_MODE_MAX_RETRY_ATTEMPTS = 6
DEFAULT_BACKOFF_SECONDS = 0.25
SAFE_MODE_BACKOFF_SECONDS = 0.5
SAFE_MODE_PAGE_SIZE = 20
SAFE_MODE_REQUEST_DELAY_SECONDS = 0.2


def _is_throttling_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return isinstance(code, str) and code in THROTTLING_ERROR_CODES


def _call_with_backoff(
    request: Callable[[], Any],
    *,
    rate_limit_safe_mode: bool,
) -> Any:
    """Retry throttled API calls with exponential backoff."""
    max_attempts = (
        SAFE_MODE_MAX_RETRY_ATTEMPTS if rate_limit_safe_mode else DEFAULT_MAX_RETRY_ATTEMPTS
    )
    base_delay = SAFE_MODE_BACKOFF_SECONDS if rate_limit_safe_mode else DEFAULT_BACKOFF_SECONDS

    for attempt in range(1, max_attempts + 1):
        try:
            return request()
        except ClientError as exc:
            if not _is_throttling_error(exc) or attempt == max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))

    raise RuntimeError("Retry loop exited unexpectedly.")


def _paginate(
    request_page: Callable[[dict[str, object]], dict[str, Any]],
    *,
    request_kwargs: dict[str, object],
    rate_limit_safe_mode: bool,
    operation_name: str,
) -> list[dict[str, Any]]:
    """Collect API pages while guarding against repeated nextToken values."""
    next_token: str | None = None
    pages: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()

    while True:
        page_request = dict(request_kwargs)
        if rate_limit_safe_mode and "maxResults" not in page_request:
            page_request["maxResults"] = SAFE_MODE_PAGE_SIZE
        if next_token is not None:
            page_request["nextToken"] = next_token

        page = cast(
            dict[str, Any],
            _call_with_backoff(
                lambda request_kwargs=page_request: request_page(request_kwargs),
                rate_limit_safe_mode=rate_limit_safe_mode,
            ),
        )
        pages.append(page)

        raw_next_token = page.get("nextToken")
        if not isinstance(raw_next_token, str) or not raw_next_token:
            break
        if raw_next_token in seen_tokens:
            raise RuntimeError(
                f"{operation_name} returned a repeated nextToken and pagination was aborted."
            )
        seen_tokens.add(raw_next_token)
        next_token = raw_next_token
        if rate_limit_safe_mode:
            time.sleep(SAFE_MODE_REQUEST_DELAY_SECONDS)

    return pages


def update_enrollment_status(
    session: boto3.Session,
    *,
    status: EnrollmentStatus,
    region_name: str = "us-east-1",
    include_member_accounts: bool = False,
    rate_limit_safe_mode: bool = False,
) -> EnrollmentStatus:
    """Update the Cost Optimization Hub enrollment status for the current account."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    update_kwargs: dict[str, object] = {"status": status}
    if include_member_accounts:
        update_kwargs["includeMemberAccounts"] = True

    try:
        response = cast(
            dict[str, Any],
            _call_with_backoff(
                lambda: client.update_enrollment_status(**update_kwargs),
                rate_limit_safe_mode=rate_limit_safe_mode,
            ),
        )
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
    rate_limit_safe_mode: bool = False,
) -> EnrollmentStatus:
    """Enable Cost Optimization Hub for the current account."""
    return update_enrollment_status(
        session,
        status="Active",
        region_name=region_name,
        rate_limit_safe_mode=rate_limit_safe_mode,
    )


def list_recommendation_summaries(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    filter_expression: dict[str, object] | None = None,
    group_by: str | None = None,
    metrics: dict[str, object] | None = None,
    rate_limit_safe_mode: bool = False,
) -> dict[str, Any]:
    """Collect paginated Cost Optimization Hub recommendation summaries."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    request_kwargs: dict[str, object] = {}
    if filter_expression is not None:
        request_kwargs["filter"] = filter_expression
    if group_by is not None:
        request_kwargs["groupBy"] = group_by
    if metrics is not None:
        request_kwargs["metrics"] = metrics

    try:
        pages = _paginate(
            lambda page_kwargs: cast(
                dict[str, Any],
                client.list_recommendation_summaries(**page_kwargs),
            ),
            request_kwargs=request_kwargs,
            rate_limit_safe_mode=rate_limit_safe_mode,
            operation_name="ListRecommendationSummaries",
        )
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
    rate_limit_safe_mode: bool = False,
) -> dict[str, Any]:
    """Collect paginated Cost Optimization Hub recommendations."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    request_kwargs: dict[str, object] = {
        "includeAllRecommendations": include_all_recommendations,
    }
    if filter_expression is not None:
        request_kwargs["filter"] = filter_expression
    if order_by is not None:
        request_kwargs["orderBy"] = order_by

    try:
        pages = _paginate(
            lambda page_kwargs: cast(
                dict[str, Any],
                client.list_recommendations(**page_kwargs),
            ),
            request_kwargs=request_kwargs,
            rate_limit_safe_mode=rate_limit_safe_mode,
            operation_name="ListRecommendations",
        )
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


def get_recommendation(
    session: boto3.Session,
    *,
    recommendation_id: str,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
) -> dict[str, Any]:
    """Fetch a detailed Cost Optimization Hub recommendation."""
    client = session.client("cost-optimization-hub", region_name=region_name)

    try:
        response = cast(
            dict[str, Any],
            _call_with_backoff(
                lambda: client.get_recommendation(recommendationId=recommendation_id),
                rate_limit_safe_mode=rate_limit_safe_mode,
            ),
        )
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Failed to get Cost Optimization Hub recommendation {recommendation_id}: {exc}"
        ) from exc
    return cast(dict[str, Any], response)


def _coerce_string(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _first_string(*values: object) -> str | None:
    for value in values:
        resolved = _coerce_string(value)
        if resolved is not None:
            return resolved
    return None


def _first_float(*values: object) -> float | None:
    for value in values:
        resolved = _coerce_float(value)
        if resolved is not None:
            return resolved
    return None


def _recommendation_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    estimated_monthly_savings = _coerce_float(item.get("estimatedMonthlySavings")) or 0.0
    recommendation_id = _coerce_string(item.get("recommendationId")) or ""
    return (-estimated_monthly_savings, recommendation_id)


def _build_recommendation_code(action_type: str | None, resource_type: str | None) -> str:
    raw_code = f"coh-{action_type or 'recommend'}-{resource_type or 'resource'}"
    return re.sub(r"[^a-z0-9]+", "-", raw_code.lower()).strip("-")


def _map_effort(implementation_effort: str | None) -> Literal["low", "medium", "high"]:
    if implementation_effort in {"VeryLow", "Low"}:
        return "low"
    if implementation_effort == "Medium":
        return "medium"
    if implementation_effort in {"High", "VeryHigh"}:
        return "high"
    return "low"


def _map_risk(detail: dict[str, Any]) -> Literal["low", "medium", "high"]:
    restart_needed = detail.get("restartNeeded")
    rollback_possible = detail.get("rollbackPossible")

    if restart_needed is True and rollback_possible is False:
        return "high"
    if restart_needed is True or rollback_possible is False:
        return "medium"
    return "low"


def categorize_recommendation(
    detail: dict[str, Any],
    *,
    list_item: dict[str, Any] | None = None,
) -> CohRecommendationCategory:
    """Map COH recommendations into the reporting buckets used by the dashboard."""
    action_type = _first_string(
        detail.get("actionType"),
        list_item.get("actionType") if list_item is not None else None,
    )
    current_resource_type = _first_string(
        detail.get("currentResourceType"),
        list_item.get("currentResourceType") if list_item is not None else None,
    )
    recommended_resource_type = _first_string(
        detail.get("recommendedResourceType"),
        list_item.get("recommendedResourceType") if list_item is not None else None,
    )

    if action_type in COMMITMENT_ACTION_TYPES:
        return COH_CATEGORY_COMMITMENT

    type_values = [value for value in (current_resource_type, recommended_resource_type) if value]
    if any(marker in value for marker in COMMITMENT_TYPE_MARKERS for value in type_values):
        return COH_CATEGORY_COMMITMENT

    if action_type in RIGHTSIZING_ACTION_TYPES:
        return COH_CATEGORY_RIGHTSIZING
    if action_type in CONDITIONAL_RIGHTSIZING_ACTION_TYPES and any(
        marker in value for marker in RIGHTSIZING_TYPE_MARKERS for value in type_values
    ):
        return COH_CATEGORY_RIGHTSIZING

    return COH_CATEGORY_OTHER


def _build_title(
    action_type: str | None,
    current_resource_type: str | None,
    recommended_resource_type: str | None,
) -> str:
    if (
        action_type
        and recommended_resource_type
        and recommended_resource_type != current_resource_type
    ):
        return f"{action_type} {current_resource_type or 'resource'} to {recommended_resource_type}"
    if action_type and current_resource_type:
        return f"{action_type} {current_resource_type}"
    if action_type:
        return action_type
    return "Cost Optimization Hub recommendation"


def _build_summary(detail: dict[str, Any], *, list_item: dict[str, Any] | None = None) -> str:
    current_summary = _first_string(
        list_item.get("currentResourceSummary") if list_item is not None else None
    )
    recommended_summary = _first_string(
        list_item.get("recommendedResourceSummary") if list_item is not None else None
    )
    action_type = _first_string(detail.get("actionType"))
    current_resource_type = _first_string(detail.get("currentResourceType"))

    summary_parts: list[str] = []
    if current_summary is not None:
        summary_parts.append(f"Current: {current_summary}.")
    if recommended_summary is not None:
        summary_parts.append(f"Recommended: {recommended_summary}.")
    if not summary_parts:
        recommendation_target = current_resource_type or "resource"
        action_phrase = action_type or "optimize"
        summary_parts.append(
            "AWS Cost Optimization Hub recommends "
            f"{action_phrase} for this {recommendation_target}."
        )
    return " ".join(summary_parts)


def _build_action(detail: dict[str, Any], *, list_item: dict[str, Any] | None = None) -> str:
    action_type = _first_string(detail.get("actionType"))
    current_resource_type = _first_string(detail.get("currentResourceType"))
    recommended_summary = _first_string(
        list_item.get("recommendedResourceSummary") if list_item is not None else None
    )

    default_action = "Apply the Cost Optimization Hub recommendation."
    action_map = {
        "Delete": (
            f"Delete the idle {current_resource_type or 'resource'} if it is no longer needed."
        ),
        "MigrateToGraviton": (
            f"Migrate the {current_resource_type or 'resource'} to a Graviton-backed option."
        ),
        "PurchaseReservedInstances": "Purchase Reserved Instances for eligible usage.",
        "PurchaseSavingsPlans": "Purchase Savings Plans coverage for eligible usage.",
        "Replace": f"Replace the {current_resource_type or 'resource'} as recommended.",
        "Rightsize": f"Rightsize the {current_resource_type or 'resource'}.",
        "ScaleIn": f"Scale in the {current_resource_type or 'resource'} where appropriate.",
        "Stop": f"Stop the idle {current_resource_type or 'resource'} when it is not required.",
        "Terminate": (
            f"Terminate the idle {current_resource_type or 'resource'} if it is no longer needed."
        ),
        "Upgrade": f"Upgrade the {current_resource_type or 'resource'} as recommended.",
    }
    if action_type is not None and action_type in action_map:
        action = action_map[action_type]
    else:
        action = default_action
    if recommended_summary is not None:
        action = f"{action} Target state: {recommended_summary}."
    return action


def normalize_recommendation(
    detail: dict[str, Any],
    *,
    list_item: dict[str, Any] | None = None,
) -> NormalizedRecommendation:
    """Normalize a detailed COH recommendation into the shared recommendation model."""
    recommendation_id = _first_string(
        detail.get("recommendationId"),
        list_item.get("recommendationId") if list_item is not None else None,
    )
    if recommendation_id is None:
        raise ValueError("COH recommendation payload did not include recommendationId.")

    action_type = _first_string(
        detail.get("actionType"),
        list_item.get("actionType") if list_item is not None else None,
    )
    current_resource_type = _first_string(
        detail.get("currentResourceType"),
        list_item.get("currentResourceType") if list_item is not None else None,
    )
    recommended_resource_type = _first_string(
        detail.get("recommendedResourceType"),
        list_item.get("recommendedResourceType") if list_item is not None else None,
    )
    estimated_monthly_savings = _first_float(
        detail.get("estimatedMonthlySavings"),
        list_item.get("estimatedMonthlySavings") if list_item is not None else None,
    )
    current_resource_summary = _first_string(
        list_item.get("currentResourceSummary") if list_item is not None else None
    )
    recommended_resource_summary = _first_string(
        list_item.get("recommendedResourceSummary") if list_item is not None else None
    )
    savings = None
    if estimated_monthly_savings is not None:
        savings = SavingsRange(
            monthly_low_usd=estimated_monthly_savings,
            monthly_high_usd=estimated_monthly_savings,
        )

    recommendation = Recommendation(
        code=_build_recommendation_code(action_type, current_resource_type),
        title=_build_title(action_type, current_resource_type, recommended_resource_type),
        summary=_build_summary(detail, list_item=list_item),
        action=_build_action(detail, list_item=list_item),
        effort=_map_effort(_coerce_string(detail.get("implementationEffort"))),
        risk=_map_risk(detail),
        savings=savings,
    )

    return NormalizedRecommendation(
        recommendation_id=recommendation_id,
        category=categorize_recommendation(detail, list_item=list_item),
        account_id=_first_string(detail.get("accountId")),
        region=_first_string(detail.get("region")),
        resource_id=_first_string(detail.get("resourceId")),
        resource_arn=_first_string(detail.get("resourceArn")),
        current_resource_type=current_resource_type,
        recommended_resource_type=recommended_resource_type,
        current_resource_summary=current_resource_summary,
        recommended_resource_summary=recommended_resource_summary,
        current_resource_details=cast(dict[str, Any] | None, detail.get("currentResourceDetails")),
        recommended_resource_details=cast(
            dict[str, Any] | None,
            detail.get("recommendedResourceDetails"),
        ),
        action_type=action_type,
        currency_code=_first_string(
            detail.get("currencyCode"),
            list_item.get("currencyCode") if list_item is not None else None,
        ),
        estimated_monthly_savings=estimated_monthly_savings,
        estimated_monthly_cost=_first_float(detail.get("estimatedMonthlyCost")),
        estimated_savings_percentage=_first_float(
            detail.get("estimatedSavingsPercentage"),
            list_item.get("estimatedSavingsPercentage") if list_item is not None else None,
        ),
        recommendation=recommendation,
    )


def collect_top_recommendation_details(
    session: boto3.Session,
    *,
    recommendations_snapshot: dict[str, Any],
    top_n: int = COH_DETAIL_TOP_N,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[str]]:
    """Fetch COH detail payloads for the top recommendations ranked by monthly savings."""
    if top_n <= 0:
        return [], []

    raw_items = recommendations_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return [], []

    selected_items = [
        item
        for item in raw_items
        if isinstance(item, dict) and _coerce_string(item.get("recommendationId")) is not None
    ]
    selected_items.sort(key=_recommendation_sort_key)

    detail_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    detail_errors: list[str] = []
    for index, item in enumerate(selected_items[:top_n]):
        recommendation_id = _coerce_string(item.get("recommendationId"))
        if recommendation_id is None:
            continue
        if rate_limit_safe_mode and index > 0:
            time.sleep(SAFE_MODE_REQUEST_DELAY_SECONDS)
        try:
            detail = get_recommendation(
                session,
                recommendation_id=recommendation_id,
                region_name=region_name,
                rate_limit_safe_mode=rate_limit_safe_mode,
            )
        except RuntimeError as exc:
            detail_errors.append(str(exc))
            continue
        detail_pairs.append((item, detail))

    return detail_pairs, detail_errors
