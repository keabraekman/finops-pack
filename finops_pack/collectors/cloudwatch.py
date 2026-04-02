"""Small CloudWatch metric helpers shared by native collectors."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

METRIC_WINDOW_DAYS = 14
S3_STORAGE_WINDOW_DAYS = 3


def _metric_window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    return start, end


def collect_single_stat_metric(
    session: boto3.Session,
    *,
    region_name: str,
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    statistic: str,
    period: int = 86400,
    days: int = METRIC_WINDOW_DAYS,
    unit: str | None = None,
) -> float | None:
    """Return an aggregate CloudWatch statistic averaged across datapoints."""
    try:
        client = session.client("cloudwatch", region_name=region_name)
    except Exception:
        return None
    start_time, end_time = _metric_window(days)
    request: dict[str, Any] = {
        "Namespace": namespace,
        "MetricName": metric_name,
        "Dimensions": dimensions,
        "StartTime": start_time,
        "EndTime": end_time,
        "Period": period,
        "Statistics": [statistic],
    }
    if unit is not None:
        request["Unit"] = unit

    try:
        response = client.get_metric_statistics(**request)
    except (ClientError, BotoCoreError, Exception):
        return None

    datapoints = response.get("Datapoints", [])
    if not isinstance(datapoints, list) or not datapoints:
        return None

    values = [
        float(point[statistic])
        for point in datapoints
        if isinstance(point, dict) and isinstance(point.get(statistic), (int, float))
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def collect_sum_metric(
    session: boto3.Session,
    *,
    region_name: str,
    namespace: str,
    metric_name: str,
    dimensions: list[dict[str, str]],
    period: int = 86400,
    days: int = METRIC_WINDOW_DAYS,
    unit: str | None = None,
) -> float | None:
    """Return the summed CloudWatch metric value across the requested time window."""
    try:
        client = session.client("cloudwatch", region_name=region_name)
    except Exception:
        return None
    start_time, end_time = _metric_window(days)
    request: dict[str, Any] = {
        "Namespace": namespace,
        "MetricName": metric_name,
        "Dimensions": dimensions,
        "StartTime": start_time,
        "EndTime": end_time,
        "Period": period,
        "Statistics": ["Sum"],
    }
    if unit is not None:
        request["Unit"] = unit

    try:
        response = client.get_metric_statistics(**request)
    except (ClientError, BotoCoreError, Exception):
        return None

    datapoints = response.get("Datapoints", [])
    if not isinstance(datapoints, list) or not datapoints:
        return None

    values = [
        float(point["Sum"])
        for point in datapoints
        if isinstance(point, dict) and isinstance(point.get("Sum"), (int, float))
    ]
    if not values:
        return None
    return round(sum(values), 2)
