from pathlib import Path

from finops_pack.models import (
    AccessCheck,
    AccessReport,
    AccountMapEntry,
    ModuleStatus,
    NormalizedRecommendation,
    Recommendation,
    SavingsRange,
    SpendBaseline,
    SpendBaselineBucket,
)
from finops_pack.prerequisites import (
    CE_RESOURCE_LEVEL_DOC_NOTE,
    CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE,
)
from finops_pack.render.dashboard import build_dashboard_download_links, render_dashboard_html


def _build_recommendation(
    recommendation_id: str,
    *,
    title: str,
    summary: str,
    category: str,
    account_id: str,
    monthly_savings: float,
    region: str = "us-east-1",
) -> NormalizedRecommendation:
    return NormalizedRecommendation(
        recommendation_id=recommendation_id,
        category=category,
        account_id=account_id,
        region=region,
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
        ),
        _build_recommendation(
            "rec-3",
            title="Delete Idle Storage",
            summary="Delete unattached storage volumes.",
            category="rightsizing / idle deletion",
            account_id="333333333333",
            monthly_savings=10.11,
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

    assert "Spend Baseline" in html
    assert "Average Daily Spend" in html
    assert "$201.45" in html
    assert "Savings by Lever" in html
    assert "Top Opportunities" in html
    assert "Savings by Category" in html
    assert "Savings by Account" in html
    assert "Prod vs Non-Prod Savings" in html
    assert "Rightsizing / Idle Deletion" in html
    assert "$133.56" in html
    assert "$123.45" in html
    assert "$67.89" in html
    assert "$10.11" in html
    assert "prod-core" in html
    assert "sandbox-apps" in html
    assert "Needs Review" in html
    assert "Non-Prod Schedule Table" in html
    assert "Schedule-Only Low Savings" in html
    assert "Schedule-Only Likely Savings" in html
    assert "Schedule-Only High Savings" in html
    assert "dev-batch" in html
    assert "prod-batch" not in html
    assert "$8.64" in html
    assert "$12.34" in html


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


def test_render_dashboard_html_includes_prerequisites_and_remediation_steps() -> None:
    html = render_dashboard_html(
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

    assert "Prerequisites" in html
    assert "Remediation Steps" in html
    assert CE_RESOURCE_LEVEL_DOC_NOTE in html
    assert CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE in html
    assert "Optional fallback modules" in html


def test_render_dashboard_html_includes_download_links() -> None:
    html = render_dashboard_html(
        [AccountMapEntry(account_id="111111111111", name="prod-core", environment="prod")],
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
    assert "Download Files" in html
    assert "Download All" in html
    assert "Accounts JSON" in html
    assert "Summary JSON" in html
    assert 'href="report-bundle.zip"' in html
    assert 'href="downloads/accounts.json"' in html
    assert 'href="summary.json"' in html
