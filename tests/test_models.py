from finops_pack import (
    Finding,
    NormalizedRecommendation,
    Recommendation,
    Resource,
    SavingsRange,
    build_stable_finding_id,
)
from finops_pack.models import (
    AccessCheck,
    AccessReport,
    ActionOpportunity,
    DailyCostPoint,
    ModuleStatus,
    RegionCoverage,
    ResourceCostSeries,
    SpendBaseline,
    SpendBaselineBucket,
)


def test_models_can_be_created() -> None:
    savings = SavingsRange(monthly_low_usd=10.0, monthly_high_usd=25.0)

    resource = Resource(
        provider="aws",
        account_id="123456789012",
        region="us-east-1",
        service="ec2",
        resource_id="i-1234567890abcdef0",
    )

    recommendation = Recommendation(
        code="stop-idle-ec2",
        title="Stop idle EC2 instance",
        summary="This EC2 instance appears underutilized.",
        action="Stop the instance during off-hours or rightsize it.",
        savings=savings,
    )

    finding = Finding(
        finding_id="finding-001",
        finding_type="idle_resource",
        severity="medium",
        resource=resource,
        recommendation=recommendation,
    )

    assert finding.recommendation.savings is not None
    assert finding.resource.service == "ec2"
    assert finding.recommendation.savings.monthly_low_usd == 10.0
    assert finding.recommendation.savings.annual_high_usd == 300.0


def test_finding_generates_stable_id_from_resource_type_and_region() -> None:
    resource = Resource(
        provider="aws",
        account_id="123456789012",
        region="us-east-1",
        service="ec2",
        resource_id="i-1234567890abcdef0",
    )
    recommendation = Recommendation(
        code="stop-idle-ec2",
        title="Stop idle EC2 instance",
        summary="This EC2 instance appears underutilized.",
        action="Stop the instance during off-hours or rightsize it.",
    )

    finding = Finding(
        finding_type="idle_resource",
        severity="medium",
        resource=resource,
        recommendation=recommendation,
    )

    assert finding.finding_id == build_stable_finding_id(
        resource_id="i-1234567890abcdef0",
        finding_type="idle_resource",
        region="us-east-1",
    )


def test_access_report_models_can_be_created() -> None:
    report = AccessReport(
        account_id="123456789012",
        region_coverage=RegionCoverage(
            strategy="fixed",
            primary_region="us-east-1",
            regions=["us-east-1", "us-west-2"],
        ),
        checks=[
            AccessCheck(
                check_id="cost_explorer",
                label="CE enabled?",
                status="ACTIVE",
                enabled=True,
                reason="Cost Explorer returned billing data.",
            )
        ],
        modules=[
            ModuleStatus(
                module_id="cost_explorer",
                label="Cost Explorer module",
                status="ACTIVE",
                reason="Cost Explorer returned billing data.",
            )
        ],
    )

    assert report.region_coverage is not None
    assert report.region_coverage.regions == ["us-east-1", "us-west-2"]
    assert report.checks[0].enabled is True


def test_normalized_recommendation_model_can_be_created() -> None:
    normalized = NormalizedRecommendation(
        recommendation_id="rec-123",
        category="rightsizing / idle deletion",
        account_id="123456789012",
        region="us-east-1",
        resource_id="i-1234567890abcdef0",
        recommended_resource_details={"ec2Instance": {"instanceType": "t3.large"}},
        action_type="Rightsize",
        estimated_monthly_savings=15.0,
        recommendation=Recommendation(
            code="coh-rightsize-ec2instance",
            title="Rightsize Ec2Instance",
            summary="Current: m5.large. Recommended: t3.large.",
            action="Rightsize the Ec2Instance.",
            savings=SavingsRange(monthly_low_usd=15.0, monthly_high_usd=15.0),
        ),
    )

    assert normalized.category == "rightsizing / idle deletion"
    assert normalized.recommendation is not None
    assert normalized.recommendation.savings is not None
    assert normalized.recommended_resource_details is not None


def test_spend_baseline_model_can_be_created() -> None:
    baseline = SpendBaseline(
        window_start="2026-02-22",
        window_end="2026-03-24",
        window_days=30,
        total_amount=321.09,
        average_daily_amount=10.7,
        unit="USD",
        monthly_buckets=[
            SpendBaselineBucket(
                start="2026-02-22",
                end="2026-03-01",
                amount=88.88,
                unit="USD",
            ),
            SpendBaselineBucket(
                start="2026-03-01",
                end="2026-03-24",
                amount=232.21,
                unit="USD",
            ),
        ],
    )

    assert baseline.total_amount == 321.09
    assert len(baseline.monthly_buckets) == 2


def test_resource_cost_series_model_can_be_created() -> None:
    series = ResourceCostSeries(
        identifier="i-1234567890abcdef0",
        unit="USD",
        total_amount=7.3,
        daily_costs=[
            DailyCostPoint(date="2026-03-10", amount=4.2),
            DailyCostPoint(date="2026-03-11", amount=3.1),
        ],
    )

    assert series.identifier == "i-1234567890abcdef0"
    assert series.total_amount == 7.3
    assert len(series.daily_costs) == 2


def test_action_opportunity_model_can_be_created() -> None:
    action = ActionOpportunity(
        bucket="Stop waste",
        lever_key="nonprod_schedule",
        action_label="Stop 1 non-prod EC2 instance off-hours",
        monthly_savings=42.5,
        source_label="Native finops-pack",
        resource_count=1,
        account_count=1,
    )

    assert action.lever_key == "nonprod_schedule"
    assert action.resource_count == 1
    assert action.account_count == 1
    assert action.action_id is not None
