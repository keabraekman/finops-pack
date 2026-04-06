from pathlib import Path
from typing import Literal

from finops_pack.domain.models import (
    AccessCheck,
    AccessReport,
    AccountMapEntry,
    ActionOpportunity,
    ModuleStatus,
    NormalizedRecommendation,
    Recommendation,
    SavingsRange,
    SpendBaseline,
    SpendBaselineBucket,
)
from finops_pack.orchestration.prerequisites import (
    CE_RESOURCE_LEVEL_DOC_NOTE,
    CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE,
)
from finops_pack.reporting.dashboard import (
    STATIC_DIR,
    build_dashboard_download_links,
    render_appendix_html,
    render_dashboard_html,
)


def _build_recommendation(
    recommendation_id: str,
    *,
    title: str,
    summary: str,
    category: Literal[
        "rightsizing / idle deletion",
        "commitment (SP/RI)",
        "storage/network/etc.",
    ],
    account_id: str,
    monthly_savings: float,
    region: str = "us-east-1",
    action_type: str = "Rightsize",
    current_resource_type: str = "Ec2Instance",
) -> NormalizedRecommendation:
    return NormalizedRecommendation(
        recommendation_id=recommendation_id,
        category=category,
        account_id=account_id,
        region=region,
        current_resource_type=current_resource_type,
        action_type=action_type,
        estimated_monthly_savings=monthly_savings,
        currency_code="USD",
        recommendation=Recommendation(
            code=f"code-{recommendation_id}",
            title=title,
            summary=summary,
            action=f"Act on {title}",
            savings=SavingsRange(
                monthly_low_usd=monthly_savings,
                monthly_high_usd=monthly_savings,
            ),
        ),
    )


def test_render_dashboard_html_includes_savings_breakdowns() -> None:
    account_map = [
        AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod"),
        AccountMapEntry(account_id="222222222222", name="sandbox-apps", environment="nonprod"),
        AccountMapEntry(account_id="333333333333", name="shared-services", environment="unknown"),
    ]
    recommendations = [
        _build_recommendation(
            "rec-1",
            title="Rightsize Compute",
            summary="Current: m5.large. Recommended: t3.large.",
            category="rightsizing / idle deletion",
            account_id="111111111111",
            monthly_savings=123.45,
        ),
        _build_recommendation(
            "rec-2",
            title="Buy Savings Plan",
            summary="Purchase Savings Plans coverage for steady usage.",
            category="commitment (SP/RI)",
            account_id="222222222222",
            monthly_savings=67.89,
            action_type="PurchaseSavingsPlans",
            current_resource_type="SavingsPlans",
        ),
        _build_recommendation(
            "rec-3",
            title="Delete Idle Storage",
            summary="Delete unattached storage volumes.",
            category="rightsizing / idle deletion",
            account_id="333333333333",
            monthly_savings=10.11,
            action_type="Delete",
            current_resource_type="EbsVolume",
        ),
    ]
    schedule_recommendations = [
        {
            "accountId": "222222222222",
            "accountName": "sandbox-apps",
            "region": "us-east-1",
            "instanceId": "i-nonprod-1",
            "instanceArn": "arn:aws:ec2:us-east-1:222222222222:instance/i-nonprod-1",
            "name": "dev-batch",
            "instanceType": "m5.large",
            "platform": "Linux/UNIX",
            "offHoursRatio": 0.7619,
            "estimatedOffHoursDailySavingsLow": 8.64,
            "estimatedOffHoursDailySavings": 12.34,
            "estimatedOffHoursDailySavingsHigh": 12.34,
            "Resource cost (14d)": "2026-03-10=$14.50",
            "estimationStatus": "estimated",
            "estimationReason": "Estimated from Cost Explorer resource-level daily cost.",
        },
        {
            "accountId": "111111111111",
            "accountName": "prod-core",
            "region": "us-east-1",
            "instanceId": "i-prod-1",
            "instanceArn": "arn:aws:ec2:us-east-1:111111111111:instance/i-prod-1",
            "name": "prod-batch",
            "instanceType": "m5.large",
            "platform": "Linux/UNIX",
            "offHoursRatio": 0.7619,
            "estimatedOffHoursDailySavingsLow": 70.0,
            "estimatedOffHoursDailySavings": 100.0,
            "estimatedOffHoursDailySavingsHigh": 100.0,
            "Resource cost (14d)": "2026-03-10=$40.00",
            "estimationStatus": "estimated",
            "estimationReason": "Estimated from Cost Explorer resource-level daily cost.",
        },
    ]

    html = render_dashboard_html(
        account_map,
        spend_baseline=SpendBaseline(
            window_start="2026-02-22",
            window_end="2026-03-24",
            window_days=30,
            total_amount=201.45,
            average_daily_amount=6.72,
            unit="USD",
            monthly_buckets=[
                SpendBaselineBucket(
                    start="2026-02-22",
                    end="2026-03-01",
                    amount=45.0,
                    unit="USD",
                ),
                SpendBaselineBucket(
                    start="2026-03-01",
                    end="2026-03-24",
                    amount=156.45,
                    unit="USD",
                ),
            ],
        ),
        coh_summary={"estimatedTotalDedupedSavings": 201.45, "currencyCode": "USD"},
        recommendations=recommendations,
        schedule_recommendations=schedule_recommendations,
    )

    assert "Current AWS spend" in html
    assert "AWS recommendation savings" in html
    assert "Modeled schedule savings" in html
    assert "Total estimated savings" in html
    assert "Savings as % of spend" not in html
    assert "$201.45" in html
    assert "Priority Actions" in html
    assert "Savings by bucket" in html
    assert '<details class="bucket-disclosure">' in html
    assert '<details class="bucket-disclosure" open>' not in html
    assert "Expand" in html
    assert "Open technical appendix" in html
    assert 'href="appendix.html"' in html
    assert "AWS recommendations come directly from AWS recommendation services." in html
    assert "Stop 1 non-prod EC2 instance off-hours" in html
    assert "Buy 1 compute savings plan" in html
    assert "Clean up or tune 1 EBS volume" in html
    assert "prod-core" in html
    assert "sandbox-apps" in html
    assert "dev-batch" in html
    assert "prod-batch" not in html
    assert "AWS Cost Optimization Hub" in html
    assert "Platform analysis" in html
    assert "Privacy + Retention" not in html
    assert "Access Report" not in html
    assert "Needs Review" not in html
    assert html.index("Open technical appendix") > html.index("Opportunity details")


def test_render_dashboard_html_limits_top_opportunities_to_twenty() -> None:
    account_map = [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")]
    recommendations = [
        _build_recommendation(
            f"rec-{index}",
            title=f"Opportunity {index}",
            summary=f"Summary {index}",
            category="rightsizing / idle deletion",
            account_id="111111111111",
            monthly_savings=float(100 - index),
        )
        for index in range(1, 22)
    ]

    html = render_dashboard_html(
        account_map,
        report_mode="technical",
        spend_baseline=SpendBaseline(
            window_start="2026-02-22",
            window_end="2026-03-24",
            window_days=30,
            total_amount=1000.0,
            average_daily_amount=33.33,
            unit="USD",
            monthly_buckets=[
                SpendBaselineBucket(
                    start="2026-02-22",
                    end="2026-03-01",
                    amount=250.0,
                    unit="USD",
                ),
                SpendBaselineBucket(
                    start="2026-03-01",
                    end="2026-03-24",
                    amount=750.0,
                    unit="USD",
                ),
            ],
        ),
        coh_summary={"estimatedTotalDedupedSavings": 1000.0, "currencyCode": "USD"},
        recommendations=recommendations,
    )

    assert "Opportunity 1" in html
    assert "Opportunity 20" in html
    assert "Opportunity 21" not in html


def test_render_dashboard_html_uses_honest_zero_state_and_surfaces_collection_notes() -> None:
    html = render_dashboard_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
        access_report=AccessReport(
            account_id="111111111111",
            checks=[
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
                    enabled=None,
                    reason="Resource-level Cost Explorer check failed: validation failed.",
                ),
            ],
            modules=[
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
                    reason="Resource-level Cost Explorer check failed: validation failed.",
                ),
            ],
        ),
    )

    assert "No ranked savings actions were produced in this run." in html
    assert "We found AWS savings opportunities worth reviewing." not in html
    assert "Coverage notes" in html
    assert "Resource-level cost data" in html
    assert "validation failed" in html
    assert "No savings actions were generated. Review the coverage notes below" in html


def test_render_dashboard_html_uses_positive_headline_for_native_only_actions() -> None:
    html = render_dashboard_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
        action_opportunities=[
            ActionOpportunity(
                bucket="Stop waste",
                lever_key="nat_gateway_cleanup",
                action_label="Delete 1 low-value NAT gateway",
                monthly_savings=32.85,
                source_label="Native finops-pack",
                why_it_matters="NAT gateways keep charging an hourly rate.",
                what_to_do_first="Confirm traffic can route without this gateway.",
                evidence_summary="Observed negligible recent traffic.",
            )
        ],
    )

    assert (
        "Estimated monthly savings opportunities: $32.85/mo in ranked savings "
        "opportunities."
    ) in html
    assert "No ranked savings actions were identified for the current review scope." not in html


def test_dashboard_stylesheet_includes_print_safe_export_rules() -> None:
    stylesheet = (STATIC_DIR / "style.css").read_text()

    assert "color-scheme: only light;" in stylesheet
    assert "@media print" in stylesheet
    assert "background: #ffffff !important;" in stylesheet
    assert "details.bucket-disclosure > :not(summary)" in stylesheet


def test_render_dashboard_html_includes_prerequisites_and_remediation_steps() -> None:
    html = render_appendix_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
        access_report=AccessReport(
            account_id="111111111111",
            checks=[
                AccessCheck(
                    check_id="cost_optimization_hub",
                    label="COH enabled?",
                    enabled=False,
                    reason="Cost Optimization Hub enrollment status is Inactive.",
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
                        "Resource-level daily cost data is not enabled or has not populated "
                        f"for the last 14 days. {CE_RESOURCE_LEVEL_DOC_NOTE}"
                    ),
                ),
            ],
            modules=[
                ModuleStatus(
                    module_id="cost_optimization_hub",
                    label="Cost Optimization Hub module",
                    status="DEGRADED",
                    reason="Cost Optimization Hub enrollment status is Inactive.",
                ),
                ModuleStatus(
                    module_id="resource_level_costs",
                    label="Resource-level cost module",
                    status="DEGRADED",
                    reason=(
                        "Resource-level daily cost data is not enabled or has not populated "
                        f"for the last 14 days. {CE_RESOURCE_LEVEL_DOC_NOTE}"
                    ),
                ),
            ],
        ),
    )

    assert "Validation details" in html
    assert "Recommended follow-up" in html
    assert CE_RESOURCE_LEVEL_DOC_NOTE in html
    assert CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE in html
    assert "Additional analysis coverage" in html


def test_render_dashboard_html_includes_download_links() -> None:
    html = render_appendix_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
        title="AWS Savings Review",
        client_id="acme-prod",
        run_id="20260401T010203Z-test",
        comparison_context={
            "savings_change_display": "+$12.50 / month",
            "summary": "vs 2026-03-31 01:02:03 UTC · -2 recommendations · -1 accounts",
        },
        download_links=build_dashboard_download_links(
            Path("/tmp/out/index.html"),
            [
                (
                    "Download All",
                    "Zipped preview bundle with the report HTML and linked artifacts.",
                    Path("/tmp/out/report-bundle.zip"),
                ),
                (
                    "Accounts JSON",
                    "Normalized account inventory with environment classification metadata.",
                    Path("/tmp/out/downloads/accounts.json"),
                ),
                (
                    "Summary JSON",
                    "Diff-friendly totals for the current run.",
                    Path("/tmp/out/summary.json"),
                ),
            ],
        ),
    )

    assert "Privacy + Retention" in html
    assert "acme-prod" in html
    assert "20260401T010203Z-test" in html
    assert "Technical Appendix" in html
    assert "Back to report" in html
    assert "Downloads" in html
    assert "Download All" in html
    assert "Accounts JSON" in html
    assert "Summary JSON" in html
    assert 'href="report-bundle.zip"' in html
    assert 'href="downloads/accounts.json"' in html
    assert 'href="summary.json"' in html
