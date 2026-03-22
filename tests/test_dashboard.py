from finops_pack.models import (
    AccountMapEntry,
    NormalizedRecommendation,
    Recommendation,
    SavingsRange,
)
from finops_pack.render.dashboard import render_dashboard_html


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

    html = render_dashboard_html(
        account_map,
        coh_summary={"estimatedTotalDedupedSavings": 201.45, "currencyCode": "USD"},
        recommendations=recommendations,
    )

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
        coh_summary={"estimatedTotalDedupedSavings": 1000.0, "currencyCode": "USD"},
        recommendations=recommendations,
    )

    assert "Opportunity 1" in html
    assert "Opportunity 20" in html
    assert "Opportunity 21" not in html
