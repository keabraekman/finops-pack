"""Optional Cost Explorer recommendation collectors used as fallback modules."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.integrations.aws.cost_explorer import THROTTLING_ERROR_CODES

DEFAULT_RIGHTSIZING_PAGE_SIZE = 20
DEFAULT_SP_PAGE_SIZE = 20
DEFAULT_SP_DETAIL_LIMIT = 5
DEFAULT_SP_REQUEST: dict[str, object] = {
    "SavingsPlansType": "COMPUTE_SP",
    "TermInYears": "ONE_YEAR",
    "PaymentOption": "NO_UPFRONT",
    "AccountScope": "PAYER",
    "LookbackPeriodInDays": "THIRTY_DAYS",
    "PageSize": DEFAULT_SP_PAGE_SIZE,
}


def _is_throttling_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return isinstance(code, str) and code in THROTTLING_ERROR_CODES


def _call_with_backoff(
    request: Callable[[], Any],
    *,
    rate_limit_safe_mode: bool,
) -> Any:
    max_attempts = 6 if rate_limit_safe_mode else 4
    base_delay = 0.5 if rate_limit_safe_mode else 0.25

    for attempt in range(1, max_attempts + 1):
        try:
            return request()
        except ClientError as exc:
            if not _is_throttling_error(exc) or attempt == max_attempts:
                raise
            time.sleep(base_delay * (2 ** (attempt - 1)))

    raise RuntimeError("Retry loop exited unexpectedly.")


def collect_rightsizing_recommendations(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
) -> dict[str, Any]:
    """Collect paginated EC2 rightsizing recommendations from Cost Explorer."""
    client = session.client("ce", region_name=region_name)
    request_kwargs: dict[str, object] = {
        "Service": "AmazonEC2",
        "Configuration": {
            "RecommendationTarget": "SAME_INSTANCE_FAMILY",
            "BenefitsConsidered": True,
        },
        "PageSize": DEFAULT_RIGHTSIZING_PAGE_SIZE,
    }

    pages: list[dict[str, Any]] = []
    flattened: list[dict[str, Any]] = []
    next_page_token: str | None = None
    seen_tokens: set[str] = set()

    try:
        while True:
            page_request = dict(request_kwargs)
            if next_page_token is not None:
                page_request["NextPageToken"] = next_page_token

            page = cast(
                dict[str, Any],
                _call_with_backoff(
                    lambda page_request=page_request: client.get_rightsizing_recommendation(
                        **page_request
                    ),
                    rate_limit_safe_mode=rate_limit_safe_mode,
                ),
            )
            pages.append(page)
            raw_recommendations = page.get("RightsizingRecommendations", [])
            if isinstance(raw_recommendations, list):
                flattened.extend(item for item in raw_recommendations if isinstance(item, dict))

            raw_next_page_token = page.get("NextPageToken")
            if not isinstance(raw_next_page_token, str) or not raw_next_page_token:
                break
            if raw_next_page_token in seen_tokens:
                raise RuntimeError(
                    "GetRightsizingRecommendation returned a repeated NextPageToken."
                )
            seen_tokens.add(raw_next_page_token)
            next_page_token = raw_next_page_token
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to collect CE rightsizing recommendations: {exc}") from exc

    return {
        "operation": "GetRightsizingRecommendation",
        "request": request_kwargs,
        "pages": pages,
        "items": flattened,
        "recommendationCount": len(flattened),
    }


def collect_savings_plans_purchase_recommendations(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
    detail_limit: int = DEFAULT_SP_DETAIL_LIMIT,
) -> dict[str, Any]:
    """Start and fetch Savings Plans purchase recommendations from Cost Explorer."""
    client = session.client("ce", region_name=region_name)
    start_response: dict[str, Any] | None = None
    start_error: str | None = None

    try:
        start_response = cast(
            dict[str, Any],
            _call_with_backoff(
                client.start_savings_plans_purchase_recommendation_generation,
                rate_limit_safe_mode=rate_limit_safe_mode,
            ),
        )
    except (ClientError, BotoCoreError) as exc:
        start_error = str(exc)

    pages: list[dict[str, Any]] = []
    flattened: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    next_page_token: str | None = None
    seen_tokens: set[str] = set()

    try:
        while True:
            page_request = dict(DEFAULT_SP_REQUEST)
            if next_page_token is not None:
                page_request["NextPageToken"] = next_page_token

            page = cast(
                dict[str, Any],
                _call_with_backoff(
                    lambda page_request=page_request: (
                        client.get_savings_plans_purchase_recommendation(**page_request)
                    ),
                    rate_limit_safe_mode=rate_limit_safe_mode,
                ),
            )
            pages.append(page)
            recommendation = page.get("SavingsPlansPurchaseRecommendation", {})
            if isinstance(recommendation, dict):
                raw_details = recommendation.get("SavingsPlansPurchaseRecommendationDetails", [])
                if isinstance(raw_details, list):
                    flattened.extend(item for item in raw_details if isinstance(item, dict))

            raw_next_page_token = page.get("NextPageToken")
            if not isinstance(raw_next_page_token, str) or not raw_next_page_token:
                break
            if raw_next_page_token in seen_tokens:
                raise RuntimeError(
                    "GetSavingsPlansPurchaseRecommendation returned a repeated NextPageToken."
                )
            seen_tokens.add(raw_next_page_token)
            next_page_token = raw_next_page_token
    except (ClientError, BotoCoreError) as exc:
        start_prefix = f" StartGeneration={start_error}." if start_error else ""
        raise RuntimeError(
            f"Failed to collect CE Savings Plans purchase recommendations: {exc}.{start_prefix}"
        ) from exc

    for recommendation in flattened[:detail_limit]:
        recommendation_detail_id = recommendation.get("RecommendationDetailId")
        if not isinstance(recommendation_detail_id, str) or not recommendation_detail_id:
            continue
        try:
            detail = cast(
                dict[str, Any],
                _call_with_backoff(
                    lambda recommendation_detail_id=recommendation_detail_id: (
                        client.get_savings_plan_purchase_recommendation_details(
                            RecommendationDetailId=recommendation_detail_id
                        )
                    ),
                    rate_limit_safe_mode=rate_limit_safe_mode,
                ),
            )
            details.append(detail)
        except (ClientError, BotoCoreError):
            continue

    return {
        "operation": "GetSavingsPlansPurchaseRecommendation",
        "startGenerationResponse": start_response,
        "startGenerationError": start_error,
        "request": DEFAULT_SP_REQUEST,
        "pages": pages,
        "items": flattened,
        "recommendationCount": len(flattened),
        "detailCount": len(details),
        "details": details,
    }
