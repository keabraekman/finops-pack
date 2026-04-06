"""Helpers for Cost Explorer baseline and resource-level cost collection."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.domain.models import (
    DailyCostPoint,
    ResourceCostSeries,
    SpendBaseline,
    SpendBaselineBucket,
)

UNBLENDED_COST_METRIC = "UnblendedCost"
SPEND_BASELINE_WINDOW_DAYS = 30
RESOURCE_DAILY_WINDOW_DAYS = 14
RESOURCE_DAILY_SERVICE = "Amazon Elastic Compute Cloud - Compute"
RESOURCE_DAILY_GROUP_BY_KEY = "RESOURCE_ID"
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
SAFE_MODE_REQUEST_DELAY_SECONDS = 0.2


def _rolling_completed_day_window(days: int) -> dict[str, str]:
    """Return an inclusive-start, exclusive-end window over completed days."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days)
    return {"Start": start.isoformat(), "End": today.isoformat()}


def _is_throttling_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return isinstance(code, str) and code in THROTTLING_ERROR_CODES


def _call_with_backoff(
    request: Callable[[], Any],
    *,
    rate_limit_safe_mode: bool,
) -> Any:
    """Retry throttled Cost Explorer API calls with exponential backoff."""
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
    """Collect Cost Explorer pages while guarding against repeated tokens."""
    next_page_token: str | None = None
    pages: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()

    while True:
        page_request = dict(request_kwargs)
        if next_page_token is not None:
            page_request["NextPageToken"] = next_page_token

        page = cast(
            dict[str, Any],
            _call_with_backoff(
                lambda request_kwargs=page_request: request_page(request_kwargs),
                rate_limit_safe_mode=rate_limit_safe_mode,
            ),
        )
        pages.append(page)

        raw_next_page_token = page.get("NextPageToken")
        if not isinstance(raw_next_page_token, str) or not raw_next_page_token:
            break
        if raw_next_page_token in seen_tokens:
            raise RuntimeError(
                f"{operation_name} returned a repeated NextPageToken and pagination was aborted."
            )
        seen_tokens.add(raw_next_page_token)
        next_page_token = raw_next_page_token
        if rate_limit_safe_mode:
            time.sleep(SAFE_MODE_REQUEST_DELAY_SECONDS)

    return pages


def _extract_metric_amount(
    metric_payload: dict[str, Any],
    *,
    metric_name: str = UNBLENDED_COST_METRIC,
) -> tuple[float, str | None]:
    total = metric_payload.get("Total")
    if not isinstance(total, dict):
        raise RuntimeError("Cost Explorer response did not include a Total object.")

    metric = total.get(metric_name)
    if not isinstance(metric, dict):
        raise RuntimeError(f"Cost Explorer response did not include {metric_name}.")

    raw_amount = metric.get("Amount")
    if raw_amount is None:
        raise RuntimeError(f"Cost Explorer response did not include {metric_name}.Amount.")

    try:
        amount = float(raw_amount)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Cost Explorer response returned a non-numeric {metric_name}.Amount."
        ) from exc

    unit = metric.get("Unit")
    return amount, unit if isinstance(unit, str) and unit else None


def build_resource_daily_request_kwargs(*, time_period: dict[str, str]) -> dict[str, object]:
    """Return the validated CE request shape used for resource-level EC2 spend calls."""
    return {
        "TimePeriod": time_period,
        "Granularity": "DAILY",
        "Metrics": [UNBLENDED_COST_METRIC],
        "Filter": {
            "Dimensions": {
                "Key": "SERVICE",
                "Values": [RESOURCE_DAILY_SERVICE],
            }
        },
        "GroupBy": [{"Type": "DIMENSION", "Key": RESOURCE_DAILY_GROUP_BY_KEY}],
    }


def _extract_group_metric_amount(
    metric_payload: dict[str, Any],
    *,
    metric_name: str = UNBLENDED_COST_METRIC,
) -> tuple[float, str | None]:
    metrics = metric_payload.get("Metrics")
    if not isinstance(metrics, dict):
        raise RuntimeError("Cost Explorer group response did not include a Metrics object.")

    metric = metrics.get(metric_name)
    if not isinstance(metric, dict):
        raise RuntimeError(f"Cost Explorer group response did not include {metric_name}.")

    raw_amount = metric.get("Amount")
    if raw_amount is None:
        raise RuntimeError(f"Cost Explorer group response did not include {metric_name}.Amount.")

    try:
        amount = float(raw_amount)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Cost Explorer group response returned a non-numeric {metric_name}.Amount."
        ) from exc

    unit = metric.get("Unit")
    return amount, unit if isinstance(unit, str) and unit else None


def _resource_identifier_aliases(identifier: str) -> list[str]:
    """Return the exact identifier plus stable aliases derived from ARN-like values."""
    normalized_identifier = identifier.strip()
    aliases = [normalized_identifier]

    if not normalized_identifier:
        return aliases

    tail = normalized_identifier.rsplit("/", 1)[-1]
    if tail and tail not in aliases:
        aliases.append(tail)

    if normalized_identifier.startswith("arn:"):
        resource_fragment = normalized_identifier.split(":", maxsplit=5)[-1]
        arn_tail = resource_fragment.rsplit("/", 1)[-1]
        if arn_tail and arn_tail not in aliases:
            aliases.append(arn_tail)

    return aliases


def collect_spend_baseline(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
) -> tuple[dict[str, Any], SpendBaseline]:
    """Collect a last-30-completed-days spend baseline grouped by month."""
    client = session.client("ce", region_name=region_name)
    request_time_period = _rolling_completed_day_window(SPEND_BASELINE_WINDOW_DAYS)
    request_kwargs: dict[str, object] = {
        "TimePeriod": request_time_period,
        "Granularity": "MONTHLY",
        "Metrics": [UNBLENDED_COST_METRIC],
    }

    try:
        pages = _paginate(
            lambda page_kwargs: cast(dict[str, Any], client.get_cost_and_usage(**page_kwargs)),
            request_kwargs=request_kwargs,
            rate_limit_safe_mode=rate_limit_safe_mode,
            operation_name="GetCostAndUsage",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "OptInRequiredException":
            raise RuntimeError(
                "Cost Explorer is not enabled for this account yet. "
                "Enable Cost Explorer in Billing and Cost Management before retrying."
            ) from exc
        raise RuntimeError(f"Failed to collect Cost Explorer spend baseline: {exc}") from exc
    except BotoCoreError as exc:
        raise RuntimeError(f"Failed to collect Cost Explorer spend baseline: {exc}") from exc

    results_by_time: list[dict[str, Any]] = []
    monthly_buckets: list[SpendBaselineBucket] = []
    unit: str | None = None

    for result in [item for page in pages for item in page.get("ResultsByTime", [])]:
        if not isinstance(result, dict):
            continue
        results_by_time.append(result)
        amount, result_unit = _extract_metric_amount(result)
        time_period = result.get("TimePeriod", {})
        start = time_period.get("Start")
        end = time_period.get("End")
        if not isinstance(start, str) or not isinstance(end, str):
            raise RuntimeError("Cost Explorer spend baseline result did not include a time period.")
        if unit is None:
            unit = result_unit
        monthly_buckets.append(
            SpendBaselineBucket(
                start=start,
                end=end,
                amount=round(amount, 2),
                unit=result_unit or unit or "USD",
            )
        )

    total_amount = round(sum(bucket.amount for bucket in monthly_buckets), 2)
    baseline = SpendBaseline(
        window_start=request_time_period["Start"],
        window_end=request_time_period["End"],
        window_days=SPEND_BASELINE_WINDOW_DAYS,
        total_amount=total_amount,
        average_daily_amount=round(
            total_amount / SPEND_BASELINE_WINDOW_DAYS,
            2,
        ),
        unit=unit or "USD",
        monthly_buckets=monthly_buckets,
    )

    return (
        {
            "operation": "GetCostAndUsage",
            "request": request_kwargs,
            "pages": pages,
            "resultsByTime": results_by_time,
            "bucketCount": len(monthly_buckets),
            "windowDays": SPEND_BASELINE_WINDOW_DAYS,
            "totalAmount": baseline.total_amount,
            "averageDailyAmount": baseline.average_daily_amount,
            "unit": baseline.unit,
        },
        baseline,
    )


def collect_resource_daily_costs(
    session: boto3.Session,
    *,
    region_name: str = "us-east-1",
    rate_limit_safe_mode: bool = False,
) -> dict[str, Any]:
    """Collect last-14-completed-days resource-level daily EC2 spend."""
    client = session.client("ce", region_name=region_name)
    time_period = _rolling_completed_day_window(RESOURCE_DAILY_WINDOW_DAYS)
    request_kwargs = build_resource_daily_request_kwargs(time_period=time_period)

    try:
        pages = _paginate(
            lambda page_kwargs: cast(
                dict[str, Any],
                client.get_cost_and_usage_with_resources(**page_kwargs),
            ),
            request_kwargs=request_kwargs,
            rate_limit_safe_mode=rate_limit_safe_mode,
            operation_name="GetCostAndUsageWithResources",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"DataUnavailableException", "OptInRequiredException"}:
            raise RuntimeError(
                "Cost Explorer resource-level daily data is opt-in and only covers the last "
                "14 days. Enable it in Billing and Cost Management preferences before retrying."
            ) from exc
        raise RuntimeError(
            f"Failed to collect Cost Explorer resource-level daily costs: {exc}"
        ) from exc
    except BotoCoreError as exc:
        raise RuntimeError(
            f"Failed to collect Cost Explorer resource-level daily costs: {exc}"
        ) from exc

    results_by_time = [item for page in pages for item in page.get("ResultsByTime", [])]
    group_count = sum(
        len(result.get("Groups", []))
        for result in results_by_time
        if isinstance(result, dict) and isinstance(result.get("Groups"), list)
    )

    return {
        "operation": "GetCostAndUsageWithResources",
        "request": request_kwargs,
        "pages": pages,
        "resultsByTime": results_by_time,
        "timePeriodCount": len(results_by_time),
        "groupCount": group_count,
        "windowDays": RESOURCE_DAILY_WINDOW_DAYS,
    }


def build_resource_cost_series_lookup(
    resource_daily_snapshot: dict[str, Any],
) -> dict[str, ResourceCostSeries]:
    """Transform raw resource-level CE data into an identifier -> daily-series lookup."""
    aggregated: dict[str, dict[str, Any]] = {}
    results_by_time = resource_daily_snapshot.get("resultsByTime", [])
    if not isinstance(results_by_time, list):
        return {}

    for result in results_by_time:
        if not isinstance(result, dict):
            continue
        time_period = result.get("TimePeriod", {})
        date = time_period.get("Start")
        if not isinstance(date, str) or not date:
            continue

        groups = result.get("Groups", [])
        if not isinstance(groups, list):
            continue

        for group in groups:
            if not isinstance(group, dict):
                continue
            keys = group.get("Keys", [])
            if not isinstance(keys, list) or not keys or not isinstance(keys[0], str):
                continue

            identifier = keys[0].strip()
            if not identifier:
                continue

            try:
                amount, unit = _extract_group_metric_amount(group)
            except RuntimeError:
                continue
            series_entry = aggregated.setdefault(
                identifier,
                {
                    "unit": unit or "USD",
                    "daily_costs": {},
                },
            )
            if unit and not series_entry["unit"]:
                series_entry["unit"] = unit

            series_entry["daily_costs"][date] = round(
                float(series_entry["daily_costs"].get(date, 0.0)) + amount,
                2,
            )

    alias_lookup: dict[str, ResourceCostSeries] = {}
    for identifier, payload in aggregated.items():
        daily_costs = [
            DailyCostPoint(date=date, amount=amount)
            for date, amount in sorted(payload["daily_costs"].items())
        ]
        total_amount = round(sum(point.amount for point in daily_costs), 2)
        series = ResourceCostSeries(
            identifier=identifier,
            unit=payload["unit"],
            total_amount=total_amount,
            daily_costs=daily_costs,
        )
        for alias in _resource_identifier_aliases(identifier):
            alias_lookup.setdefault(alias, series)

    return alias_lookup


def find_resource_cost_series(
    resource_cost_lookup: dict[str, ResourceCostSeries],
    *,
    resource_arn: str | None = None,
    resource_id: str | None = None,
) -> ResourceCostSeries | None:
    """Resolve a resource cost series from either a resource ARN or resource ID."""
    for candidate in (resource_arn, resource_id):
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        for alias in _resource_identifier_aliases(candidate):
            series = resource_cost_lookup.get(alias)
            if series is not None:
                return series
    return None


def format_resource_cost_series(series: ResourceCostSeries | None) -> str:
    """Render a compact daily cost series for CSV output."""
    if series is None:
        return ""

    formatted_days = []
    for point in series.daily_costs:
        if series.unit == "USD":
            amount_display = f"${point.amount:.2f}"
        else:
            amount_display = f"{point.amount:.2f} {series.unit}"
        formatted_days.append(f"{point.date}={amount_display}")
    return "; ".join(formatted_days)
