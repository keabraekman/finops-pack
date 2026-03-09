from finops_pack import Finding, Recommendation, Resource, SavingsRange


def test_models_can_be_created():
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

    assert finding.resource.service == "ec2"
    assert finding.recommendation.savings.monthly_low_usd == 10.0
    assert finding.recommendation.savings.annual_high_usd == 300.0