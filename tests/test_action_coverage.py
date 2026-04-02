# ruff: noqa: E501
from pathlib import Path

from finops_pack.analyzers.action_opportunities import (
    build_action_opportunities,
    rank_action_opportunities,
)
from finops_pack.config import AppConfig
from finops_pack.demo_fixtures import load_demo_fixture_bundle
from finops_pack.models import AccountMapEntry, ActionOpportunity, RegionCoverage
from finops_pack.render.dashboard import render_appendix_html


def test_demo_fixtures_cover_all_twelve_levers() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    bundle = load_demo_fixture_bundle(
        repo_root / "demo/fixtures",
        config=AppConfig(),
        fallback_region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1", "us-west-2"],
        ),
    )

    actions = build_action_opportunities(
        account_map=bundle.account_map,
        recommendations=bundle.recommendations,
        schedule_recommendations=bundle.schedule_recommendations,
        native_actions=bundle.native_actions,
    )

    assert {action.lever_key for action in actions} == {
        "nonprod_schedule",
        "commitments",
        "ec2_rightsizing",
        "graviton_migration",
        "rds_rightsizing",
        "rds_nonprod_schedule",
        "ecs_fargate_rightsizing",
        "nat_gateway_cleanup",
        "ebs_cleanup_tuning",
        "lambda_memory_rightsizing",
        "rds_aurora_storage_tuning",
        "s3_lifecycle_storage_class",
    }
    assert bundle.spend_baseline is not None
    assert (
        round(sum(action.monthly_savings for action in actions), 2)
        < bundle.spend_baseline.total_amount
    )
    assert {"Native finops-pack", "AWS COH", "AWS Compute Optimizer"} <= {
        action.source_label for action in actions
    }


def test_rank_action_opportunities_prefers_owner_relevant_high_savings_actions() -> None:
    ranked = rank_action_opportunities(
        [
            ActionOpportunity(
                bucket="Storage cleanup",
                lever_key="s3_lifecycle_storage_class",
                action_label="Apply S3 lifecycle rules to 4 buckets",
                monthly_savings=25.0,
                source_label="Native finops-pack",
            ),
            ActionOpportunity(
                bucket="Stop waste",
                lever_key="nonprod_schedule",
                action_label="Stop 3 non-prod EC2 instances off-hours",
                monthly_savings=200.0,
                source_label="Native finops-pack",
                confidence="high",
            ),
        ]
    )

    assert ranked[0].lever_key == "nonprod_schedule"
    assert ranked[1].lever_key == "s3_lifecycle_storage_class"


def test_rank_action_opportunities_keeps_material_compute_actions_ahead_of_small_cleanup() -> None:
    ranked = rank_action_opportunities(
        [
            ActionOpportunity(
                bucket="Storage cleanup",
                lever_key="ebs_cleanup_tuning",
                action_label="Clean up or tune 3 EBS volumes",
                monthly_savings=52.4,
                source_label="Native finops-pack",
                risk="low",
                effort="low",
                confidence="high",
            ),
            ActionOpportunity(
                bucket="Rightsize",
                lever_key="ec2_rightsizing",
                action_label="Rightsize 2 EC2 instances",
                monthly_savings=121.8,
                source_label="AWS Compute Optimizer",
                risk="medium",
                effort="medium",
                confidence="high",
            ),
        ]
    )

    assert ranked[0].lever_key == "ec2_rightsizing"
    assert ranked[1].lever_key == "ebs_cleanup_tuning"


def test_render_appendix_html_includes_savings_evidence_sections() -> None:
    html = render_appendix_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
        action_opportunities=[
            ActionOpportunity(
                bucket="Stop waste",
                lever_key="nat_gateway_cleanup",
                action_label="Delete 1 low-value NAT gateway",
                monthly_savings=32.85,
                source_label="Native finops-pack",
                why_it_matters="NAT gateways keep charging an hourly rate even when recent traffic is minimal.",
                what_to_do_first="Confirm the route tables before removing the gateway.",
                evidence_summary="1 NAT gateway showed low traffic over the recent CloudWatch window.",
                supporting_items=[
                    {
                        "account_name": "prod-core",
                        "region": "us-east-1",
                        "resource_name": "nat-123",
                        "resource_id": "nat-123",
                        "detail": "Very low traffic",
                        "monthly_savings": 32.85,
                        "monthly_savings_display": "$32.85/mo",
                    }
                ],
            )
        ],
    )

    assert "Savings Evidence" in html
    assert "NAT Gateway cleanup" in html
    assert "Delete 1 low-value NAT gateway" in html
    assert "Back to Dashboard" in html
