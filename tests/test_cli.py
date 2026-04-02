import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import finops_pack.cli as cli
from finops_pack.models import (
    AccessCheck,
    AccessReport,
    AccountRecord,
    ModuleStatus,
    RegionCoverage,
    SpendBaseline,
    SpendBaselineBucket,
)
from finops_pack.prerequisites import CE_RESOURCE_LEVEL_DOC_NOTE


def test_demo_command_runs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "demo-output"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                f"demo_fixture_dir: {repo_root / 'demo/fixtures'}",
                f"output_dir: {output_dir}",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "demo", "--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert "Running finops-pack in demo mode" in result.stdout
    assert (output_dir / "accounts.json").exists()
    assert (output_dir / "access_report.json").exists()
    assert (output_dir / "dashboard.html").exists()
    assert (output_dir / "exports.csv").exists()
    assert (output_dir / "exports.json").exists()
    assert (output_dir / "exports.schema.json").exists()
    assert (tmp_path / "out" / "summary.json").exists()
    assert (tmp_path / "out" / "index.html").exists()
    assert (tmp_path / "out" / "downloads" / "accounts.json").exists()
    assert (tmp_path / "out" / "downloads" / "exports.json").exists()
    assert (tmp_path / "out" / "downloads" / "exports.schema.json").exists()
    assert (tmp_path / "out" / "schedule" / "schedule_recs.csv").exists()


def test_run_requires_role_arn() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "role_arn is required" in result.stdout


def test_iam_policy_command_prints_policy_json() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "iam-policy", "--mode", "full"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    policy = json.loads(result.stdout)
    actions = {action for statement in policy["Statement"] for action in statement["Action"]}
    assert "cost-optimization-hub:UpdateEnrollmentStatus" in actions


def test_handle_run_enables_cost_optimization_hub(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "region: us-west-2",
                "regions:",
                "  - us-west-2",
                "  - us-east-1",
                "schedule:",
                "  timezone: America/New_York",
                "  business_hours:",
                "    days:",
                "      - mon",
                "      - tue",
                "      - wed",
                "      - thu",
                "      - fri",
                "    start_hour: 8",
                "    end_hour: 18",
            ]
        ),
        encoding="utf-8",
    )
    session = Mock()
    assume_role_session = Mock(return_value=session)
    enable_coh = Mock(return_value="Active")
    build_access_report = Mock(
        return_value=AccessReport(
            account_id="123456789012",
            region_coverage=RegionCoverage(
                strategy="fixed",
                primary_region="us-west-2",
                regions=["us-west-2", "us-east-1"],
            ),
            checks=[
                AccessCheck(
                    check_id="cost_optimization_hub",
                    label="COH enabled?",
                    status="ACTIVE",
                    enabled=True,
                    reason="Cost Optimization Hub enrollment status is Active.",
                ),
                AccessCheck(
                    check_id="cost_explorer",
                    label="CE enabled?",
                    status="ACTIVE",
                    enabled=True,
                    reason="Cost Explorer returned billing data for a recent completed day.",
                ),
                AccessCheck(
                    check_id="resource_level_costs",
                    label="resource-level enabled?",
                    enabled=False,
                    reason=(
                        "Resource-level daily cost data is not enabled or "
                        "has not populated for the last 14 days. "
                        f"{CE_RESOURCE_LEVEL_DOC_NOTE}"
                    ),
                ),
            ],
            modules=[
                ModuleStatus(
                    module_id="cost_optimization_hub",
                    label="Cost Optimization Hub module",
                    status="ACTIVE",
                    reason="Cost Optimization Hub enrollment status is Active.",
                ),
                ModuleStatus(
                    module_id="cost_explorer",
                    label="Cost Explorer module",
                    status="ACTIVE",
                    reason="Cost Explorer returned billing data for a recent completed day.",
                ),
                ModuleStatus(
                    module_id="resource_level_costs",
                    label="Resource-level cost module",
                    status="DEGRADED",
                    reason=(
                        "Resource-level daily cost data is not enabled or "
                        "has not populated for the last 14 days. "
                        f"{CE_RESOURCE_LEVEL_DOC_NOTE}"
                    ),
                ),
            ],
        )
    )
    collect_spend_baseline = Mock(
        return_value=(
            {
                "operation": "GetCostAndUsage",
                "request": {
                    "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-24"},
                    "Granularity": "MONTHLY",
                    "Metrics": ["UnblendedCost"],
                },
                "pages": [
                    {
                        "ResultsByTime": [
                            {
                                "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-01"},
                                "Total": {"UnblendedCost": {"Amount": "84.50", "Unit": "USD"}},
                            },
                            {
                                "TimePeriod": {"Start": "2026-03-01", "End": "2026-03-24"},
                                "Total": {"UnblendedCost": {"Amount": "115.50", "Unit": "USD"}},
                            },
                        ]
                    }
                ],
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
                "bucketCount": 2,
                "windowDays": 30,
                "totalAmount": 200.0,
                "averageDailyAmount": 6.67,
                "unit": "USD",
            },
            SpendBaseline(
                window_start="2026-02-22",
                window_end="2026-03-24",
                window_days=30,
                total_amount=200.0,
                average_daily_amount=6.67,
                unit="USD",
                monthly_buckets=[
                    SpendBaselineBucket(
                        start="2026-02-22",
                        end="2026-03-01",
                        amount=84.5,
                        unit="USD",
                    ),
                    SpendBaselineBucket(
                        start="2026-03-01",
                        end="2026-03-24",
                        amount=115.5,
                        unit="USD",
                    ),
                ],
            ),
        )
    )
    collect_resource_daily_costs = Mock(
        return_value={
            "operation": "GetCostAndUsageWithResources",
            "request": {
                "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-24"},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "Filter": {
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Elastic Compute Cloud - Compute"],
                    }
                },
                "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            },
            "pages": [
                {
                    "ResultsByTime": [
                        {
                            "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-11"},
                            "Groups": [
                                {
                                    "Keys": ["i-1234567890abcdef0"],
                                    "Metrics": {"UnblendedCost": {"Amount": "4.20", "Unit": "USD"}},
                                }
                            ],
                        }
                    ]
                }
            ],
            "resultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-11"},
                    "Groups": [
                        {
                            "Keys": ["i-1234567890abcdef0"],
                            "Metrics": {"UnblendedCost": {"Amount": "4.20", "Unit": "USD"}},
                        }
                    ],
                }
            ],
            "timePeriodCount": 1,
            "groupCount": 1,
            "windowDays": 14,
        }
    )
    collect_ec2_inventory = Mock(
        return_value={
            "operation": "DescribeInstances",
            "regions": ["us-west-2", "us-east-1"],
            "accountCount": 1,
            "itemCount": 1,
            "errorCount": 0,
            "items": [
                {
                    "accountId": "123456789012",
                    "accountName": "prod-core",
                    "region": "us-east-1",
                    "instanceId": "i-1234567890abcdef0",
                    "instanceArn": (
                        "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0"
                    ),
                    "name": "web-1",
                    "state": "running",
                    "instanceType": "m5.large",
                    "platformDetails": "Linux/UNIX",
                    "launchTime": "2026-03-01T00:00:00+00:00",
                    "rootDeviceType": "ebs",
                    "lifecycle": "",
                    "tags": {"Name": "web-1"},
                }
            ],
            "errors": [],
        }
    )
    build_schedule_recommendation_rows = Mock(
        return_value=[
            {
                "accountId": "123456789012",
                "accountName": "prod-core",
                "region": "us-east-1",
                "instanceId": "i-1234567890abcdef0",
                "instanceArn": "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
                "name": "web-1",
                "state": "running",
                "instanceType": "m5.large",
                "platform": "Linux/UNIX",
                "launchTime": "2026-03-01T00:00:00+00:00",
                "scheduleTimezone": "America/New_York",
                "businessHours": "mon,tue,wed,thu,fri@08:00-18:00",
                "offHoursRatio": 0.7024,
                "costWindowDays": 14,
                "recentAvgDailyCost": 4.2,
                "estimatedOffHoursDailySavingsLow": 2.07,
                "estimatedOffHoursDailySavings": 2.95,
                "estimatedOffHoursDailySavingsHigh": 2.95,
                "Resource cost (14d)": "2026-03-10=$4.20",
                "estimationStatus": "estimated",
                "estimationReason": (
                    "Estimated from Cost Explorer resource-level daily cost "
                    "over the last 14 completed days."
                ),
                "candidateReason": "Running, EBS-backed, and not tagged as managed.",
            }
        ]
    )
    list_accounts = Mock(
        return_value=[
            AccountRecord(
                account_id="123456789012",
                name="prod-core",
                email="prod-core@example.com",
                status="ACTIVE",
            )
        ]
    )
    list_recommendation_summaries = Mock(
        return_value={
            "operation": "ListRecommendationSummaries",
            "request": {},
            "pages": [
                {
                    "estimatedTotalDedupedSavings": 42.5,
                    "currencyCode": "USD",
                    "items": [{"group": "EC2", "estimatedMonthlySavings": 42.5}],
                }
            ],
            "items": [{"group": "EC2", "estimatedMonthlySavings": 42.5}],
            "itemCount": 1,
            "estimatedTotalDedupedSavings": 42.5,
            "currencyCode": "USD",
            "groupBy": None,
            "metrics": None,
        }
    )
    list_recommendations = Mock(
        return_value={
            "operation": "ListRecommendations",
            "request": {"includeAllRecommendations": True},
            "pages": [
                {
                    "items": [
                        {
                            "recommendationId": "rec-1",
                            "estimatedMonthlySavings": 42.5,
                            "currentResourceType": "Ec2Instance",
                            "currentResourceSummary": "m5.large at low utilization",
                            "recommendedResourceSummary": "t3.large estimated to satisfy demand",
                        }
                    ]
                }
            ],
            "items": [
                {
                    "recommendationId": "rec-1",
                    "estimatedMonthlySavings": 42.5,
                    "currentResourceType": "Ec2Instance",
                    "currentResourceSummary": "m5.large at low utilization",
                    "recommendedResourceSummary": "t3.large estimated to satisfy demand",
                }
            ],
            "itemCount": 1,
        }
    )
    collect_top_recommendation_details = Mock(
        return_value=(
            [
                (
                    {
                        "recommendationId": "rec-1",
                        "estimatedMonthlySavings": 42.5,
                        "currentResourceType": "Ec2Instance",
                        "currentResourceSummary": "m5.large at low utilization",
                        "recommendedResourceSummary": "t3.large estimated to satisfy demand",
                    },
                    {
                        "recommendationId": "rec-1",
                        "accountId": "123456789012",
                        "region": "us-east-1",
                        "resourceId": "i-1234567890abcdef0",
                        "resourceArn": (
                            "arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0"
                        ),
                        "currentResourceType": "Ec2Instance",
                        "recommendedResourceType": "Ec2Instance",
                        "estimatedMonthlySavings": 42.5,
                        "estimatedMonthlyCost": 100.0,
                        "estimatedSavingsPercentage": 42.5,
                        "currencyCode": "USD",
                        "implementationEffort": "Low",
                        "actionType": "Rightsize",
                        "restartNeeded": False,
                        "rollbackPossible": True,
                        "recommendedResourceDetails": {
                            "ec2Instance": {"instanceType": "t3.large", "platform": "Linux/UNIX"}
                        },
                    },
                )
            ],
            [],
        )
    )
    publish_report_site_to_s3 = Mock(return_value=Mock(report_url="https://example.com/report"))
    build_run_id = Mock(return_value="20260401T010203Z-test")
    load_previous_summary_from_s3 = Mock(
        return_value=Mock(
            run_id="20260331T010203Z-prev",
            summary={
                "run": {"generated_at": "2026-03-31 01:02:03 UTC"},
                "accounts": {"total": 2},
                "coh": {
                    "normalized_recommendation_count": 3,
                    "normalized_estimated_monthly_savings": 30.0,
                },
            },
        )
    )

    monkeypatch.setattr(cli, "assume_role_session", assume_role_session)
    monkeypatch.setattr(cli, "build_run_id", build_run_id)
    monkeypatch.setattr(cli, "load_previous_summary_from_s3", load_previous_summary_from_s3)
    monkeypatch.setattr(cli, "enable_cost_optimization_hub", enable_coh)
    monkeypatch.setattr(cli, "_build_access_report", build_access_report)
    monkeypatch.setattr(cli, "collect_spend_baseline", collect_spend_baseline)
    monkeypatch.setattr(cli, "collect_resource_daily_costs", collect_resource_daily_costs)
    monkeypatch.setattr(cli, "collect_ec2_inventory", collect_ec2_inventory)
    monkeypatch.setattr(
        cli,
        "build_schedule_recommendation_rows",
        build_schedule_recommendation_rows,
    )
    monkeypatch.setattr(cli, "list_recommendation_summaries", list_recommendation_summaries)
    monkeypatch.setattr(cli, "list_recommendations", list_recommendations)
    monkeypatch.setattr(
        cli,
        "collect_top_recommendation_details",
        collect_top_recommendation_details,
    )
    monkeypatch.setattr(cli, "list_accounts", list_accounts)
    monkeypatch.setattr(cli, "publish_report_site_to_s3", publish_report_site_to_s3)

    args = argparse.Namespace(
        command="run",
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        region="us-west-2",
        session_name="test-session",
        check_identity=False,
        enable_coh=True,
        collect_ce_resource_daily=True,
        enable_ce_rightsizing_fallback=False,
        enable_ce_savings_plan_fallback=False,
        rate_limit_safe_mode=True,
        config=str(config_file),
        output_dir=str(tmp_path / "output"),
        client_id="acme-prod",
        report_bucket="s3://report-bucket",
        report_retention_days=14,
        no_upload=False,
    )

    result = cli.handle_run(args)

    assert result == 0
    assume_role_session.assert_called_once_with(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="us-west-2",
    )
    enable_coh.assert_called_once_with(
        session,
        region_name="us-west-2",
        rate_limit_safe_mode=True,
    )
    list_accounts.assert_called_once_with(session)
    build_access_report.assert_called_once()
    collect_spend_baseline.assert_called_once_with(
        session,
        region_name="us-east-1",
        rate_limit_safe_mode=True,
    )
    collect_resource_daily_costs.assert_called_once_with(
        session,
        region_name="us-east-1",
        rate_limit_safe_mode=True,
    )
    collect_ec2_inventory.assert_called_once_with(
        session,
        account_records=list_accounts.return_value,
        regions=["us-west-2", "us-east-1"],
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        current_account_id="123456789012",
    )
    list_recommendation_summaries.assert_called_once_with(
        session,
        region_name="us-east-1",
        rate_limit_safe_mode=True,
    )
    list_recommendations.assert_called_once_with(
        session,
        region_name="us-east-1",
        rate_limit_safe_mode=True,
    )
    collect_top_recommendation_details.assert_called_once_with(
        session,
        recommendations_snapshot=list_recommendations.return_value,
        top_n=20,
        region_name="us-east-1",
        rate_limit_safe_mode=True,
    )
    load_previous_summary_from_s3.assert_called_once_with(
        session=session,
        bucket="report-bucket",
        client_id="acme-prod",
        current_run_id="20260401T010203Z-test",
    )
    publish_report_site_to_s3.assert_called_once()
    publish_call = publish_report_site_to_s3.call_args.kwargs
    assert publish_call["session"] is session
    assert publish_call["bucket"] == "report-bucket"
    assert publish_call["client_id"] == "acme-prod"
    assert publish_call["run_id"] == "20260401T010203Z-test"
    assert publish_call["retention_days"] == 14
    assert publish_call["preview_dir"] == (tmp_path / "out")
    assert callable(publish_call["build_index_html"])
    uploaded_asset_names = {asset.object_name for asset in publish_call["assets"]}
    assert "style.css" in uploaded_asset_names
    assert "downloads/exports.csv" in uploaded_asset_names
    assert "downloads/exports.json" in uploaded_asset_names
    assert "downloads/exports.schema.json" in uploaded_asset_names
    assert "summary.json" in uploaded_asset_names
    build_schedule_recommendation_rows.assert_called_once_with(
        collect_ec2_inventory.return_value,
        schedule=cli.load_config(str(config_file)).schedule,
        resource_daily_snapshot=collect_resource_daily_costs.return_value,
    )

    output = capsys.readouterr().out
    assert "enable_coh=True" in output
    assert "collect_ce_resource_daily=True" in output
    assert "enable_ce_rightsizing_fallback=False" in output
    assert "enable_ce_savings_plan_fallback=False" in output
    assert "rate_limit_safe_mode=True" in output
    assert "client_id=acme-prod" in output
    assert "run_id=20260401T010203Z-test" in output
    assert "report_bucket=report-bucket" in output
    assert "report_retention_days=14" in output
    assert "comparison_previous_run_id=20260331T010203Z-prev" in output
    assert "savings_change_since_last_report=+$12.50 / month" in output
    assert "region_coverage=us-west-2,us-east-1" in output
    assert "schedule_timezone=America/New_York" in output
    assert "schedule_business_hours=mon,tue,wed,thu,fri@08:00-18:00" in output
    assert "ce_total_spend_last_30_days=200.0" in output
    assert "ce_resource_daily_group_count=1" in output
    assert "coh_estimated_total_deduped_savings=42.5" in output
    assert "coh_recommendation_count=1" in output
    assert "coh_normalized_recommendation_count=1" in output
    assert "coh_csv_export_path=" in output
    assert "coh_json_export_path=" in output
    assert "coh_schema_export_path=" in output
    assert "ec2_inventory_instance_count=1" in output
    assert "schedule_recommendation_count=1" in output
    assert "schedule_estimated_count=1" in output
    assert "schedule_recs_path=" in output
    assert "Report URL: https://example.com/report" in output
    assert "resource_level_enabled=no" in output
    assert "module_resource_level_costs=DEGRADED" in output
    assert "account_count=1" in output

    accounts = json.loads((tmp_path / "output" / "accounts.json").read_text(encoding="utf-8"))
    assert accounts[0]["environment"] == "prod"
    assert accounts[0]["classification_source"] == "regex"
    access_report = json.loads(
        (tmp_path / "output" / "access_report.json").read_text(encoding="utf-8")
    )
    assert access_report["region_coverage"]["regions"] == ["us-west-2", "us-east-1"]
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["run"]["client_id"] == "acme-prod"
    assert summary["run"]["run_id"] == "20260401T010203Z-test"
    assert summary["run"]["rate_limit_safe_mode"] is True
    assert summary["run"]["schedule"]["timezone"] == "America/New_York"
    assert summary["run"]["schedule"]["business_hours"]["start_hour"] == 8
    assert summary["run"]["schedule"]["business_hours"]["end_hour"] == 18
    assert summary["accounts"]["total"] == 1
    assert summary["inventory"]["ec2_instance_count"] == 1
    assert summary["inventory"]["ec2_inventory_error_count"] == 0
    assert summary["schedule_recommendations"]["recommendation_count"] == 1
    assert summary["schedule_recommendations"]["estimated_count"] == 1
    assert summary["schedule_recommendations"]["needs_ce_resource_level_opt_in_count"] == 0
    assert summary["ce"]["spend_baseline_total"] == 200.0
    assert summary["ce"]["resource_daily_collected"] is True
    assert summary["ce"]["resource_daily_group_count"] == 1
    assert summary["coh"]["recommendation_count"] == 1
    assert summary["coh"]["normalized_recommendation_count"] == 1
    assert summary["coh"]["normalized_estimated_monthly_savings"] == 42.5
    assert summary["comparison"]["previous_run_id"] == "20260331T010203Z-prev"
    assert summary["comparison"]["savings_change_since_last_report"] == 12.5
    coh_summaries = json.loads(
        (tmp_path / "out" / "raw" / "coh_summaries.json").read_text(encoding="utf-8")
    )
    assert coh_summaries["estimatedTotalDedupedSavings"] == 42.5
    ce_total_spend = json.loads(
        (tmp_path / "out" / "raw" / "ce_total_spend.json").read_text(encoding="utf-8")
    )
    assert ce_total_spend["totalAmount"] == 200.0
    ce_resource_daily = json.loads(
        (tmp_path / "out" / "raw" / "ce_resource_daily.json").read_text(encoding="utf-8")
    )
    assert ce_resource_daily["groupCount"] == 1
    ec2_inventory = json.loads(
        (tmp_path / "out" / "raw" / "ec2_inventory.json").read_text(encoding="utf-8")
    )
    assert ec2_inventory["itemCount"] == 1
    coh_recommendations = json.loads(
        (tmp_path / "out" / "raw" / "coh_recommendations.json").read_text(encoding="utf-8")
    )
    assert coh_recommendations["itemCount"] == 1
    normalized_recommendations = json.loads(
        (tmp_path / "out" / "normalized" / "recommendations.json").read_text(encoding="utf-8")
    )
    assert normalized_recommendations[0]["category"] == "rightsizing / idle deletion"
    assert normalized_recommendations[0]["recommendation"]["code"] == "coh-rightsize-ec2instance"
    exports_json = json.loads((tmp_path / "output" / "exports.json").read_text(encoding="utf-8"))
    assert (
        exports_json[0]["recommended_resource_details"]["ec2Instance"]["instanceType"] == "t3.large"
    )
    exports_csv = (tmp_path / "output" / "exports.csv").read_text(encoding="utf-8")
    assert "resourceId,accountId,type,action,estSavings,region,Resource cost (14d)" in exports_csv
    assert (
        "i-1234567890abcdef0,123456789012,Ec2Instance,Rightsize,42.5,us-east-1,2026-03-10=$4.20"
    ) in exports_csv
    exports_schema = json.loads(
        (tmp_path / "output" / "exports.schema.json").read_text(encoding="utf-8")
    )
    assert exports_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert exports_schema["type"] == "array"
    schedule_csv = (tmp_path / "out" / "schedule" / "schedule_recs.csv").read_text(encoding="utf-8")
    assert (
        "estimatedOffHoursDailySavingsLow,estimatedOffHoursDailySavings,"
        "estimatedOffHoursDailySavingsHigh" in schedule_csv
    )
    assert "i-1234567890abcdef0" in schedule_csv
    assert "2.07,2.95,2.95" in schedule_csv
    assert "estimated" in schedule_csv
    dashboard_html = (tmp_path / "output" / "dashboard.html").read_text(encoding="utf-8")
    assert "Access Report" in dashboard_html
    assert "Region Coverage" in dashboard_html
    assert "Spend Baseline" in dashboard_html
    assert "Account Map" in dashboard_html
    assert "Top Opportunities" in dashboard_html
    assert "Savings by Category" in dashboard_html
    assert "Savings by Account" in dashboard_html
    assert "Prod vs Non-Prod Savings" in dashboard_html
    assert "730-hour monthly normalization" in dashboard_html
    assert "Recommendation IDs can expire after about 24 hours" in dashboard_html
    assert "FinOps Pack Dashboard - acme-prod" in dashboard_html
    assert "20260401T010203Z-test" in dashboard_html
    assert "prod-core" in dashboard_html
    assert "Rightsizing / Idle Deletion" in dashboard_html
    assert "Savings by Lever" in dashboard_html
    assert "Savings Change Since Last Report" in dashboard_html
    assert "+$12.50 / month" in dashboard_html
    assert "Download Files" in dashboard_html
    assert "Privacy + Retention" in dashboard_html

    preview_html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "FinOps Pack Dashboard - acme-prod" in preview_html
    assert "Privacy + Retention" in preview_html
    assert "Download Files" in preview_html
    assert 'href="report-bundle.zip"' in preview_html
    assert 'href="downloads/accounts.json"' in preview_html
    assert 'href="downloads/access_report.json"' in preview_html
    assert 'href="downloads/exports.csv"' in preview_html
    assert 'href="downloads/exports.json"' in preview_html
    assert 'href="downloads/exports.schema.json"' in preview_html
    assert 'href="summary.json"' in preview_html
    assert 'href="schedule/schedule_recs.csv"' in preview_html
    assert (tmp_path / "out" / "report-bundle.zip").exists()
    assert (tmp_path / "out" / "downloads" / "accounts.json").exists()
    assert (tmp_path / "out" / "downloads" / "access_report.json").exists()
    assert (tmp_path / "out" / "downloads" / "exports.csv").exists()
    assert (tmp_path / "out" / "downloads" / "exports.json").exists()
    assert (tmp_path / "out" / "downloads" / "exports.schema.json").exists()

    publish_report_site_to_s3.reset_mock()
    load_previous_summary_from_s3.reset_mock()
    args.no_upload = True
    result = cli.handle_run(args)

    assert result == 0
    load_previous_summary_from_s3.assert_not_called()
    publish_report_site_to_s3.assert_not_called()
    no_upload_output = capsys.readouterr().out
    assert "upload_enabled=False" in no_upload_output
    assert "Report URL:" not in no_upload_output


def test_merge_coh_collection_status_marks_module_degraded_when_collection_fails() -> None:
    report = AccessReport(
        account_id="123456789012",
        region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1"],
        ),
        modules=[
            ModuleStatus(
                module_id="cost_optimization_hub",
                label="Cost Optimization Hub module",
                status="ACTIVE",
                reason="Cost Optimization Hub enrollment status is Active.",
            )
        ],
    )

    cli._merge_coh_collection_status(
        report,
        summaries_snapshot={
            "error": "Failed to list Cost Optimization Hub recommendation summaries: denied"
        },
        recommendations_snapshot={},
    )

    assert report.modules[0].status == "DEGRADED"
    assert (
        "Failed to list Cost Optimization Hub recommendation summaries" in report.modules[0].reason
    )


def test_build_access_report_marks_modules_degraded_when_prerequisites_are_missing() -> None:
    session = Mock()
    sts_client = Mock()
    sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
    coh_client = Mock()
    coh_client.list_enrollment_statuses.return_value = {
        "items": [{"accountId": "123456789012", "status": "Inactive"}]
    }
    ce_client = Mock()
    ce_client.get_cost_and_usage.return_value = {}
    ce_client.get_cost_and_usage_with_resources.side_effect = ClientError(
        {"Error": {"Code": "DataUnavailableException", "Message": "resource-level disabled"}},
        "GetCostAndUsageWithResources",
    )

    def client_factory(service_name: str, **_: object) -> Mock:
        return {
            "sts": sts_client,
            "cost-optimization-hub": coh_client,
            "ce": ce_client,
        }[service_name]

    session.client.side_effect = client_factory

    report = cli._build_access_report(
        session,
        region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1", "us-west-2"],
        ),
    )

    check_map = {check.check_id: check for check in report.checks}
    module_map = {module.module_id: module for module in report.modules}

    assert report.account_id == "123456789012"
    assert check_map["cost_optimization_hub"].enabled is False
    assert check_map["cost_explorer"].enabled is True
    assert check_map["resource_level_costs"].enabled is False
    assert module_map["cost_optimization_hub"].status == "DEGRADED"
    assert module_map["resource_level_costs"].status == "DEGRADED"
    assert "not enabled" in module_map["resource_level_costs"].reason
    assert CE_RESOURCE_LEVEL_DOC_NOTE in module_map["resource_level_costs"].reason


def test_build_access_report_marks_unknown_when_permissions_are_missing() -> None:
    session = Mock()
    sts_client = Mock()
    sts_client.get_caller_identity.return_value = {"Account": "123456789012"}
    coh_client = Mock()
    coh_client.list_enrollment_statuses.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "ListEnrollmentStatuses",
    )
    ce_client = Mock()
    ce_client.get_cost_and_usage.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetCostAndUsage",
    )
    ce_client.get_cost_and_usage_with_resources.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetCostAndUsageWithResources",
    )

    def client_factory(service_name: str, **_: object) -> Mock:
        return {
            "sts": sts_client,
            "cost-optimization-hub": coh_client,
            "ce": ce_client,
        }[service_name]

    session.client.side_effect = client_factory

    report = cli._build_access_report(
        session,
        region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1"],
        ),
    )

    for check in report.checks:
        assert check.enabled is None
        assert check.status == "DEGRADED"
        assert "denied" in check.reason.lower()
    for module in report.modules:
        assert module.status == "DEGRADED"


def test_append_optional_fallback_modules_marks_active_when_cost_explorer_is_ready() -> None:
    report = AccessReport(
        checks=[
            AccessCheck(
                check_id="cost_explorer",
                label="CE enabled?",
                status="ACTIVE",
                enabled=True,
                reason="Cost Explorer returned billing data for a recent completed day.",
            )
        ]
    )

    cli._append_optional_fallback_modules(
        report,
        enable_ce_rightsizing_fallback=True,
        enable_ce_savings_plan_fallback=True,
    )

    module_map = {module.module_id: module for module in report.modules}
    assert module_map["ce_rightsizing_fallback"].status == "ACTIVE"
    assert module_map["ce_savings_plan_fallback"].status == "ACTIVE"
