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
)


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
                        "has not populated for the last 14 days."
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
                        "has not populated for the last 14 days."
                    ),
                ),
            ],
        )
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
                            "arn:aws:ec2:us-east-1:123456789012:"
                            "instance/i-1234567890abcdef0"
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

    monkeypatch.setattr(cli, "assume_role_session", assume_role_session)
    monkeypatch.setattr(cli, "enable_cost_optimization_hub", enable_coh)
    monkeypatch.setattr(cli, "_build_access_report", build_access_report)
    monkeypatch.setattr(cli, "list_recommendation_summaries", list_recommendation_summaries)
    monkeypatch.setattr(cli, "list_recommendations", list_recommendations)
    monkeypatch.setattr(
        cli,
        "collect_top_recommendation_details",
        collect_top_recommendation_details,
    )
    monkeypatch.setattr(cli, "list_accounts", list_accounts)

    args = argparse.Namespace(
        command="run",
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        region="us-west-2",
        session_name="test-session",
        check_identity=False,
        enable_coh=True,
        config=str(config_file),
        output_dir=str(tmp_path / "output"),
    )

    result = cli.handle_run(args)

    assert result == 0
    assume_role_session.assert_called_once_with(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="us-west-2",
    )
    enable_coh.assert_called_once_with(session, region_name="us-west-2")
    list_accounts.assert_called_once_with(session)
    build_access_report.assert_called_once()
    list_recommendation_summaries.assert_called_once_with(session, region_name="us-east-1")
    list_recommendations.assert_called_once_with(session, region_name="us-east-1")

    output = capsys.readouterr().out
    assert "enable_coh=True" in output
    assert "region_coverage=us-west-2,us-east-1" in output
    assert "coh_estimated_total_deduped_savings=42.5" in output
    assert "coh_recommendation_count=1" in output
    assert "coh_normalized_recommendation_count=1" in output
    assert "coh_csv_export_path=" in output
    assert "coh_json_export_path=" in output
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
    coh_summaries = json.loads(
        (tmp_path / "out" / "raw" / "coh_summaries.json").read_text(encoding="utf-8")
    )
    assert coh_summaries["estimatedTotalDedupedSavings"] == 42.5
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
        exports_json[0]["recommended_resource_details"]["ec2Instance"]["instanceType"]
        == "t3.large"
    )
    exports_csv = (tmp_path / "output" / "exports.csv").read_text(encoding="utf-8")
    assert "resourceId,accountId,type,action,estSavings,region" in exports_csv
    assert "i-1234567890abcdef0,123456789012,Ec2Instance,Rightsize,42.5,us-east-1" in exports_csv
    dashboard_html = (tmp_path / "output" / "dashboard.html").read_text(encoding="utf-8")
    assert "Access Report" in dashboard_html
    assert "Region Coverage" in dashboard_html
    assert "Account Map" in dashboard_html
    assert "730-hour monthly normalization" in dashboard_html
    assert "Recommendation IDs can expire after about 24 hours" in dashboard_html
    assert "prod-core" in dashboard_html


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
