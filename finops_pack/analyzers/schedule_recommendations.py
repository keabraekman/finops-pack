"""Schedule-aware EC2 stop recommendation helpers."""

from __future__ import annotations

from typing import Any

from finops_pack.aws.cost_explorer import (
    RESOURCE_DAILY_WINDOW_DAYS,
    build_resource_cost_series_lookup,
    find_resource_cost_series,
    format_resource_cost_series,
)
from finops_pack.config import ScheduleConfig

ESTIMATED_STATUS = "estimated"
NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS = "needs CE resource-level opt-in"
NO_RECENT_RESOURCE_LEVEL_COST_STATUS = "no recent resource-level cost"


def _format_schedule_business_hours(schedule: ScheduleConfig) -> str:
    return (
        f"{','.join(schedule.business_hours.days)}@"
        f"{schedule.business_hours.start_hour:02d}:00-"
        f"{schedule.business_hours.end_hour:02d}:00"
    )


def calculate_off_hours_ratio(schedule: ScheduleConfig) -> float:
    """Return the share of weekly hours that fall outside configured business hours."""
    business_hours_per_day = schedule.business_hours.end_hour - schedule.business_hours.start_hour
    business_hours_per_week = business_hours_per_day * len(schedule.business_hours.days)
    off_hours_per_week = max(0, (7 * 24) - business_hours_per_week)
    return round(off_hours_per_week / (7 * 24), 4)


def _managed_service_reason(tags: dict[str, str]) -> str | None:
    tag_keys = {key.strip().lower() for key in tags if key.strip()}
    if "aws:autoscaling:groupname" in tag_keys:
        return "EC2 Auto Scaling"
    if any(
        key.startswith(prefix)
        for key in tag_keys
        for prefix in (
            "kubernetes.io/cluster/",
            "k8s.io/cluster-autoscaler/",
            "alpha.eksctl.io/",
            "eks:",
        )
    ):
        return "Kubernetes/EKS"
    if "amazonecsmanaged" in tag_keys or any(key.startswith("aws:ecs:") for key in tag_keys):
        return "Amazon ECS"
    if any(
        key.startswith(prefix)
        for key in tag_keys
        for prefix in ("elasticbeanstalk:", "aws:elasticbeanstalk:")
    ):
        return "Elastic Beanstalk"
    return None


def evaluate_stoppable_candidate(instance: dict[str, Any]) -> tuple[bool, str]:
    """Apply conservative EC2-only stop-scheduling guardrails."""
    state = str(instance.get("state") or "").lower()
    if state != "running":
        rendered_state = state or "unknown"
        return False, f"Excluded because instance state is {rendered_state}."

    root_device_type = str(instance.get("rootDeviceType") or "").lower()
    if root_device_type != "ebs":
        return (
            False,
            "Excluded because only EBS-backed instances are considered safely stoppable in v1.",
        )

    lifecycle = str(instance.get("lifecycle") or "").lower()
    if lifecycle:
        return (
            False,
            f"Excluded because instance lifecycle is {lifecycle}, not standard on-demand.",
        )

    tags = instance.get("tags", {})
    if not isinstance(tags, dict):
        tags = {}
    managed_service = _managed_service_reason(tags)
    if managed_service is not None:
        return (
            False,
            f"Excluded because tags suggest the instance is managed by {managed_service}.",
        )

    return (
        True,
        (
            "Running, EBS-backed, and not tagged as managed by Auto Scaling, "
            "Kubernetes/EKS, ECS, or Elastic Beanstalk."
        ),
    )


def build_schedule_recommendation_rows(
    inventory_snapshot: dict[str, Any],
    *,
    schedule: ScheduleConfig,
    resource_daily_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build CSV-ready schedule recommendation rows from inventory and CE snapshots."""
    raw_items = inventory_snapshot.get("items", [])
    if not isinstance(raw_items, list):
        return []

    off_hours_ratio = calculate_off_hours_ratio(schedule)
    business_hours = _format_schedule_business_hours(schedule)

    resource_daily_available = (
        resource_daily_snapshot is not None and not resource_daily_snapshot.get("error")
    )
    resource_cost_lookup = (
        build_resource_cost_series_lookup(resource_daily_snapshot)
        if resource_daily_available and resource_daily_snapshot is not None
        else {}
    )
    window_days = (
        int(resource_daily_snapshot.get("windowDays", RESOURCE_DAILY_WINDOW_DAYS))
        if resource_daily_available and resource_daily_snapshot is not None
        else RESOURCE_DAILY_WINDOW_DAYS
    )
    missing_resource_daily_reason = (
        "Cost Explorer resource-level daily data was not collected for this run."
    )
    if resource_daily_snapshot is not None:
        snapshot_error = resource_daily_snapshot.get("error")
        if isinstance(snapshot_error, str) and snapshot_error:
            missing_resource_daily_reason = snapshot_error

    rows: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        is_candidate, candidate_reason = evaluate_stoppable_candidate(item)
        if not is_candidate:
            continue

        row = {
            "accountId": item.get("accountId", ""),
            "accountName": item.get("accountName", ""),
            "region": item.get("region", ""),
            "instanceId": item.get("instanceId", ""),
            "instanceArn": item.get("instanceArn", ""),
            "name": item.get("name", ""),
            "state": item.get("state", ""),
            "instanceType": item.get("instanceType", ""),
            "platform": item.get("platformDetails") or item.get("platform") or "",
            "launchTime": item.get("launchTime", ""),
            "scheduleTimezone": schedule.timezone,
            "businessHours": business_hours,
            "offHoursRatio": off_hours_ratio,
            "costWindowDays": window_days,
            "recentAvgDailyCost": "",
            "estimatedOffHoursDailySavings": "",
            "Resource cost (14d)": "",
            "estimationStatus": NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS,
            "estimationReason": missing_resource_daily_reason,
            "candidateReason": candidate_reason,
        }

        if resource_daily_available:
            series = find_resource_cost_series(
                resource_cost_lookup,
                resource_arn=item.get("instanceArn"),
                resource_id=item.get("instanceId"),
            )
            if series is None:
                row["estimationStatus"] = NO_RECENT_RESOURCE_LEVEL_COST_STATUS
                row["estimationReason"] = (
                    "No EC2 resource-level daily cost was returned for the last "
                    f"{window_days} completed days."
                )
            else:
                avg_daily_cost = round(series.total_amount / window_days, 2)
                row["recentAvgDailyCost"] = avg_daily_cost
                row["estimatedOffHoursDailySavings"] = round(avg_daily_cost * off_hours_ratio, 2)
                row["Resource cost (14d)"] = format_resource_cost_series(series)
                row["estimationStatus"] = ESTIMATED_STATUS
                row["estimationReason"] = (
                    "Estimated from Cost Explorer resource-level daily cost over the last "
                    f"{window_days} completed days."
                )

        rows.append(row)

    rows.sort(key=lambda row: (row["accountId"], row["region"], row["instanceId"]))
    return rows
