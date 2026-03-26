from finops_pack.analyzers.schedule_recommendations import (
    ESTIMATED_STATUS,
    NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS,
    build_schedule_recommendation_rows,
)
from finops_pack.config import BusinessHours, ScheduleConfig


def test_build_schedule_recommendation_rows_filters_to_conservative_candidates() -> None:
    rows = build_schedule_recommendation_rows(
        {
            "items": [
                {
                    "accountId": "123456789012",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-123",
                    "instanceArn": "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
                    "name": "app-1",
                    "state": "running",
                    "instanceType": "m5.large",
                    "platformDetails": "Linux/UNIX",
                    "launchTime": "2026-03-01T00:00:00+00:00",
                    "rootDeviceType": "ebs",
                    "lifecycle": "",
                    "tags": {"Name": "app-1"},
                },
                {
                    "accountId": "123456789012",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-stopped",
                    "state": "stopped",
                    "rootDeviceType": "ebs",
                    "lifecycle": "",
                    "tags": {},
                },
                {
                    "accountId": "123456789012",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-asg",
                    "state": "running",
                    "rootDeviceType": "ebs",
                    "lifecycle": "",
                    "tags": {"aws:autoscaling:groupName": "workers"},
                },
            ]
        },
        schedule=ScheduleConfig(),
        resource_daily_snapshot={
            "resultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-11"},
                    "Groups": [
                        {
                            "Keys": ["i-123"],
                            "Metrics": {"UnblendedCost": {"Amount": "7.00", "Unit": "USD"}},
                        }
                    ],
                },
                {
                    "TimePeriod": {"Start": "2026-03-11", "End": "2026-03-12"},
                    "Groups": [
                        {
                            "Keys": ["i-123"],
                            "Metrics": {"UnblendedCost": {"Amount": "7.00", "Unit": "USD"}},
                        }
                    ],
                },
            ],
            "windowDays": 14,
        },
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["instanceId"] == "i-123"
    assert row["businessHours"] == "mon,tue,wed,thu,fri@09:00-17:00"
    assert row["offHoursRatio"] == 0.7619
    assert row["recentAvgDailyCost"] == 1.0
    assert row["estimatedOffHoursDailySavings"] == 0.76
    assert row["Resource cost (14d)"] == "2026-03-10=$7.00; 2026-03-11=$7.00"
    assert row["estimationStatus"] == ESTIMATED_STATUS


def test_build_schedule_recommendation_rows_marks_missing_resource_daily_data() -> None:
    rows = build_schedule_recommendation_rows(
        {
            "items": [
                {
                    "accountId": "123456789012",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-123",
                    "instanceArn": "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
                    "name": "app-1",
                    "state": "running",
                    "instanceType": "m5.large",
                    "platformDetails": "Linux/UNIX",
                    "launchTime": "2026-03-01T00:00:00+00:00",
                    "rootDeviceType": "ebs",
                    "lifecycle": "",
                    "tags": {"Name": "app-1"},
                }
            ]
        },
        schedule=ScheduleConfig(
            timezone="America/New_York",
            business_hours=BusinessHours(
                days=["mon", "tue", "wed", "thu", "fri"],
                start_hour=8,
                end_hour=18,
            ),
        ),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["scheduleTimezone"] == "America/New_York"
    assert row["businessHours"] == "mon,tue,wed,thu,fri@08:00-18:00"
    assert row["offHoursRatio"] == 0.7024
    assert row["recentAvgDailyCost"] == ""
    assert row["estimatedOffHoursDailySavings"] == ""
    assert row["estimationStatus"] == NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS
    assert "not collected" in row["estimationReason"]
