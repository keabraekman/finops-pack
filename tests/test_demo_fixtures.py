import json
from pathlib import Path

from finops_pack.config import AppConfig
from finops_pack.demo_fixtures import load_demo_fixture_bundle, parse_spend_baseline_snapshot
from finops_pack.models import RegionCoverage


def test_parse_spend_baseline_snapshot_builds_model() -> None:
    baseline = parse_spend_baseline_snapshot(
        {
            "request": {
                "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-24"},
            },
            "resultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-01"},
                    "Total": {"UnblendedCost": {"Amount": "84.50", "Unit": "USD"}},
                },
                {
                    "TimePeriod": {"Start": "2026-03-01", "End": "2026-03-24"},
                    "Total": {"UnblendedCost": {"Amount": "115.50", "Unit": "USD"}},
                },
            ],
            "windowDays": 30,
            "totalAmount": 200.0,
            "averageDailyAmount": 6.67,
            "unit": "USD",
        }
    )

    assert baseline is not None
    assert baseline.total_amount == 200.0
    assert baseline.average_daily_amount == 6.67
    assert len(baseline.monthly_buckets) == 2


def test_load_demo_fixture_bundle_supports_scrubbed_output_fixtures(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "accounts.json").write_text(
        json.dumps(
            [
                {
                    "account_id": "111111111111",
                    "name": "prod-core",
                    "email": "prod-core@example.com",
                    "status": "ACTIVE",
                    "environment": "prod",
                    "confidence": "high",
                    "classification_source": "config",
                    "classification_reason": "Pinned in config.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (fixture_dir / "access_report.json").write_text(
        json.dumps(
            {
                "account_id": "Demo Fixture",
                "region_coverage": {
                    "strategy": "fixed",
                    "primary_region": "us-east-1",
                    "regions": ["us-east-1", "us-west-2"],
                },
                "checks": [],
                "modules": [],
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "summary.json").write_text(
        json.dumps(
            {
                "run": {
                    "generated_at": "2026-04-01 00:00:00 UTC",
                    "client_id": "acme-prod",
                    "run_id": "20260401T000000Z-demo",
                    "account_id": "Demo Fixture",
                    "region": "us-east-1",
                    "schedule": {
                        "timezone": "UTC",
                        "business_hours": {
                            "days": ["mon", "tue", "wed", "thu", "fri"],
                            "start_hour": 9,
                            "end_hour": 17,
                        },
                    },
                },
                "comparison": {
                    "previous_run_id": "20260331T000000Z-demo",
                    "savings_change_since_last_report": 12.5,
                    "savings_change_display": "+$12.50 / month",
                    "summary": "Up from the previous report.",
                },
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "ce_total_spend.json").write_text(
        json.dumps(
            {
                "request": {
                    "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-24"},
                },
                "resultsByTime": [
                    {
                        "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-01"},
                        "Total": {"UnblendedCost": {"Amount": "84.50", "Unit": "USD"}},
                    },
                    {
                        "TimePeriod": {"Start": "2026-03-01", "End": "2026-03-24"},
                        "Total": {"UnblendedCost": {"Amount": "115.50", "Unit": "USD"}},
                    },
                ],
                "windowDays": 30,
                "totalAmount": 200.0,
                "averageDailyAmount": 6.67,
                "unit": "USD",
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "coh_summaries.json").write_text(
        json.dumps(
            {
                "estimatedTotalDedupedSavings": 42.5,
                "currencyCode": "USD",
                "itemCount": 1,
                "items": [{"group": "EC2", "estimatedMonthlySavings": 42.5}],
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "exports.json").write_text(
        json.dumps(
            [
                {
                    "recommendation_id": "rec-1",
                    "source": "cost_optimization_hub",
                    "category": "rightsizing / idle deletion",
                    "account_id": "111111111111",
                    "region": "us-east-1",
                    "resource_id": "i-1234567890abcdef0",
                    "resource_arn": (
                        "arn:aws:ec2:us-east-1:111111111111:instance/i-1234567890abcdef0"
                    ),
                    "current_resource_type": "Ec2Instance",
                    "recommended_resource_type": "Ec2Instance",
                    "current_resource_summary": "m5.large",
                    "recommended_resource_summary": "t3.large",
                    "recommended_resource_details": {"ec2Instance": {"instanceType": "t3.large"}},
                    "action_type": "Rightsize",
                    "currency_code": "USD",
                    "estimated_monthly_savings": 42.5,
                    "estimated_monthly_cost": 120.0,
                    "estimated_savings_percentage": 35.4,
                    "recommendation": {
                        "code": "coh-rightsize-ec2instance",
                        "title": "Rightsize Ec2Instance",
                        "summary": "Current: m5.large. Recommended: t3.large.",
                        "action": "Rightsize the Ec2Instance.",
                        "effort": "medium",
                        "risk": "medium",
                        "savings": {
                            "monthly_low_usd": 42.5,
                            "monthly_high_usd": 42.5,
                            "annual_low_usd": 510.0,
                            "annual_high_usd": 510.0,
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (fixture_dir / "schedule_recs.json").write_text(
        json.dumps(
            [
                {
                    "accountId": "111111111111",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-1234567890abcdef0",
                    "instanceArn": (
                        "arn:aws:ec2:us-east-1:111111111111:instance/i-1234567890abcdef0"
                    ),
                    "name": "prod-core-web",
                    "state": "running",
                    "instanceType": "m5.large",
                    "platform": "Linux/UNIX",
                    "launchTime": "2026-03-01T00:00:00+00:00",
                    "scheduleTimezone": "UTC",
                    "businessHours": "mon,tue,wed,thu,fri@09:00-17:00",
                    "offHoursRatio": 0.7619,
                    "costWindowDays": 14,
                    "recentAvgDailyCost": 3.87,
                    "estimatedOffHoursDailySavingsLow": 2.07,
                    "estimatedOffHoursDailySavings": 2.95,
                    "estimatedOffHoursDailySavingsHigh": 2.95,
                    "Resource cost (14d)": "2026-03-10=$4.20",
                    "estimationStatus": "estimated",
                    "estimationReason": "Estimated from Cost Explorer resource-level daily cost.",
                    "candidateReason": "Running and EBS-backed.",
                }
            ]
        ),
        encoding="utf-8",
    )

    bundle = load_demo_fixture_bundle(
        fixture_dir,
        config=AppConfig(),
        fallback_region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1"],
        ),
    )

    assert bundle.client_id == "acme-prod"
    assert bundle.run_id == "20260401T000000Z-demo"
    assert bundle.access_report.region_coverage is not None
    assert bundle.access_report.region_coverage.regions == ["us-east-1", "us-west-2"]
    assert bundle.spend_baseline is not None
    assert bundle.spend_baseline.total_amount == 200.0
    assert len(bundle.recommendations) == 1
    assert bundle.recommendations[0].recommendation_id == "rec-1"
    assert bundle.comparison_context is not None
    assert bundle.comparison_context["previous_run_id"] == "20260331T000000Z-demo"
