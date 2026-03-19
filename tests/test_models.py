from finops_pack import Finding, Recommendation, Resource, SavingsRange
from finops_pack.models import AccessCheck, AccessReport, ModuleStatus, RegionCoverage


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
