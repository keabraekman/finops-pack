from unittest.mock import Mock, call

import pytest
from botocore.exceptions import ClientError

import finops_pack.integrations.aws.cost_optimization_hub as coh
from finops_pack.integrations.aws.cost_optimization_hub import (
    DEFAULT_SUMMARY_GROUP_BY,
    collect_top_recommendation_details,
    enable_cost_optimization_hub,
    get_recommendation,
    list_recommendation_summaries,
    list_recommendations,
    normalize_recommendation,
)


def test_enable_cost_optimization_hub_updates_enrollment_status() -> None:
    client = Mock()
    client.update_enrollment_status.return_value = {"status": "Active"}
    session = Mock()
    session.client.return_value = client

    status = enable_cost_optimization_hub(session, region_name="us-west-2")

    assert status == "Active"
    session.client.assert_called_once_with("cost-optimization-hub", region_name="us-west-2")
    client.update_enrollment_status.assert_called_once_with(status="Active")


def test_enable_cost_optimization_hub_wraps_client_errors() -> None:
    client = Mock()
    client.update_enrollment_status.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "UpdateEnrollmentStatus",
    )
    session = Mock()
    session.client.return_value = client

    with pytest.raises(RuntimeError, match="Failed to update Cost Optimization Hub"):
        enable_cost_optimization_hub(session)


def test_list_recommendation_summaries_collects_pages_and_deduped_total() -> None:
    client = Mock()
    client.list_recommendation_summaries.side_effect = [
        {
            "estimatedTotalDedupedSavings": 125.5,
            "currencyCode": "USD",
            "nextToken": "token-1",
            "items": [
                {
                    "group": "EC2",
                    "estimatedMonthlySavings": 100.0,
                    "recommendationCount": 2,
                }
            ],
        },
        {
            "estimatedTotalDedupedSavings": 125.5,
            "currencyCode": "USD",
            "items": [
                {
                    "group": "EBS",
                    "estimatedMonthlySavings": 25.5,
                    "recommendationCount": 1,
                }
            ],
        },
    ]
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendation_summaries(session, region_name="us-east-1")

    session.client.assert_called_once_with("cost-optimization-hub", region_name="us-east-1")
    assert client.list_recommendation_summaries.call_args_list == [
        call(groupBy=DEFAULT_SUMMARY_GROUP_BY),
        call(groupBy=DEFAULT_SUMMARY_GROUP_BY, nextToken="token-1"),
    ]
    assert snapshot["itemCount"] == 2
    assert snapshot["estimatedTotalDedupedSavings"] == 125.5
    assert snapshot["groupBy"] == DEFAULT_SUMMARY_GROUP_BY
    assert len(snapshot["pages"]) == 2


def test_list_recommendation_summaries_keeps_filters_while_applying_default_group_by() -> None:
    client = Mock()
    client.list_recommendation_summaries.return_value = {"items": []}
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendation_summaries(
        session,
        filter_expression={"regions": ["us-west-1"]},
    )

    assert snapshot["itemCount"] == 0
    client.list_recommendation_summaries.assert_called_once_with(
        filter={"regions": ["us-west-1"]},
        groupBy=DEFAULT_SUMMARY_GROUP_BY,
    )


def test_list_recommendations_collects_all_pages() -> None:
    client = Mock()
    client.list_recommendations.side_effect = [
        {
            "items": [
                {"recommendationId": "rec-1", "estimatedMonthlySavings": 12.5},
                {"recommendationId": "rec-2", "estimatedMonthlySavings": 8.0},
            ],
            "nextToken": "token-2",
        },
        {"items": [{"recommendationId": "rec-3", "estimatedMonthlySavings": 3.0}]},
    ]
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendations(session)

    assert client.list_recommendations.call_args_list == [
        call(includeAllRecommendations=True),
        call(includeAllRecommendations=True, nextToken="token-2"),
    ]
    assert snapshot["itemCount"] == 3
    assert len(snapshot["items"]) == 3


def test_list_recommendation_summaries_wraps_paginator_errors() -> None:
    client = Mock()
    client.list_recommendation_summaries.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "ListRecommendationSummaries",
    )
    session = Mock()
    session.client.return_value = client

    with pytest.raises(
        RuntimeError, match="Failed to list Cost Optimization Hub recommendation summaries"
    ):
        list_recommendation_summaries(session)


def test_get_recommendation_fetches_detail_payload() -> None:
    client = Mock()
    client.get_recommendation.return_value = {
        "recommendationId": "rec-1",
        "resourceId": "i-123",
    }
    session = Mock()
    session.client.return_value = client

    response = get_recommendation(session, recommendation_id="rec-1", region_name="us-east-1")

    session.client.assert_called_once_with("cost-optimization-hub", region_name="us-east-1")
    client.get_recommendation.assert_called_once_with(recommendationId="rec-1")
    assert response["resourceId"] == "i-123"


def test_list_recommendations_retries_throttling(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = Mock()
    monkeypatch.setattr(coh.time, "sleep", sleep)

    client = Mock()
    client.list_recommendations.side_effect = [
        ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
            "ListRecommendations",
        ),
        {"items": [{"recommendationId": "rec-1", "estimatedMonthlySavings": 12.5}]},
    ]
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendations(session, rate_limit_safe_mode=True)

    assert snapshot["itemCount"] == 1
    sleep.assert_called_once_with(0.5)


def test_list_recommendations_safe_mode_sets_max_results() -> None:
    client = Mock()
    client.list_recommendations.return_value = {
        "items": [{"recommendationId": "rec-1", "estimatedMonthlySavings": 12.5}]
    }
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendations(session, rate_limit_safe_mode=True)

    assert snapshot["itemCount"] == 1
    client.list_recommendations.assert_called_once_with(
        includeAllRecommendations=True,
        maxResults=20,
    )


def test_list_recommendation_summaries_rejects_repeated_next_token() -> None:
    client = Mock()
    client.list_recommendation_summaries.side_effect = [
        {"items": [], "nextToken": "token-1"},
        {"items": [], "nextToken": "token-1"},
    ]
    session = Mock()
    session.client.return_value = client

    with pytest.raises(RuntimeError, match="repeated nextToken"):
        list_recommendation_summaries(session)


def test_collect_top_recommendation_details_fetches_top_n_by_savings() -> None:
    session = Mock()
    session.client.return_value = Mock()
    get_recommendation_mock = session.client.return_value.get_recommendation
    get_recommendation_mock.side_effect = [
        {"recommendationId": "rec-2", "resourceId": "i-2"},
        {"recommendationId": "rec-3", "resourceId": "i-3"},
    ]

    details, errors = collect_top_recommendation_details(
        session,
        recommendations_snapshot={
            "items": [
                {"recommendationId": "rec-1", "estimatedMonthlySavings": 5.0},
                {"recommendationId": "rec-2", "estimatedMonthlySavings": 25.0},
                {"recommendationId": "rec-3", "estimatedMonthlySavings": 15.0},
            ]
        },
        top_n=2,
    )

    assert errors == []
    assert [item["recommendationId"] for item, _ in details] == ["rec-2", "rec-3"]
    assert [detail["resourceId"] for _, detail in details] == ["i-2", "i-3"]


def test_normalize_recommendation_maps_commitment_category() -> None:
    normalized = normalize_recommendation(
        {
            "recommendationId": "rec-commitment",
            "accountId": "123456789012",
            "region": "us-east-1",
            "resourceId": "sp-eligible",
            "resourceArn": "arn:aws:ec2::123456789012:savingsplan/sp-eligible",
            "currentResourceType": "Ec2InstanceSavingsPlans",
            "recommendedResourceType": "ComputeSavingsPlans",
            "estimatedMonthlySavings": 32.5,
            "estimatedMonthlyCost": 120.0,
            "estimatedSavingsPercentage": 27.1,
            "currencyCode": "USD",
            "implementationEffort": "Low",
            "actionType": "PurchaseSavingsPlans",
            "restartNeeded": False,
            "rollbackPossible": True,
            "recommendedResourceDetails": {
                "computeSavingsPlans": {
                    "paymentOption": "NoUpfront",
                    "termLength": "ONE_YEAR",
                }
            },
        }
    )

    assert normalized.category == "commitment (SP/RI)"
    assert normalized.recommendation is not None
    assert normalized.recommendation.code == "coh-purchasesavingsplans-ec2instancesavingsplans"
    assert normalized.recommendation.savings is not None
    assert normalized.recommendation.savings.monthly_low_usd == 32.5
    assert normalized.recommended_resource_details is not None
    assert (
        normalized.recommended_resource_details["computeSavingsPlans"]["termLength"] == "ONE_YEAR"
    )


def test_normalize_recommendation_maps_rightsizing_category_from_list_summary() -> None:
    normalized = normalize_recommendation(
        {
            "recommendationId": "rec-rightsize",
            "accountId": "123456789012",
            "region": "us-east-1",
            "resourceId": "i-123",
            "resourceArn": "arn:aws:ec2:us-east-1:123456789012:instance/i-123",
            "currentResourceType": "Ec2Instance",
            "recommendedResourceType": "Ec2Instance",
            "recommendedResourceDetails": {"ec2Instance": {"instanceType": "t3.large"}},
            "estimatedMonthlySavings": 18.0,
            "currencyCode": "USD",
            "implementationEffort": "Medium",
            "actionType": "Rightsize",
            "restartNeeded": True,
            "rollbackPossible": True,
        },
        list_item={
            "recommendationId": "rec-rightsize",
            "currentResourceSummary": "m5.large running at low utilization",
            "recommendedResourceSummary": "t3.large estimated to satisfy demand",
        },
    )

    assert normalized.category == "rightsizing / idle deletion"
    assert normalized.recommendation is not None
    assert "Current: m5.large" in normalized.recommendation.summary
    assert normalized.recommendation.effort == "medium"
    assert normalized.recommendation.risk == "medium"
    assert normalized.recommended_resource_summary == "t3.large estimated to satisfy demand"


def test_normalize_recommendation_maps_upgrade_for_compute_resources_to_rightsizing() -> None:
    normalized = normalize_recommendation(
        {
            "recommendationId": "rec-upgrade",
            "accountId": "123456789012",
            "region": "us-east-1",
            "resourceId": "db-123",
            "resourceArn": "arn:aws:rds:us-east-1:123456789012:db:db-123",
            "currentResourceType": "RdsDbInstance",
            "recommendedResourceType": "RdsDbInstance",
            "estimatedMonthlySavings": 21.0,
            "currencyCode": "USD",
            "implementationEffort": "Low",
            "actionType": "Upgrade",
            "restartNeeded": False,
            "rollbackPossible": True,
        }
    )

    assert normalized.category == "rightsizing / idle deletion"
    assert normalized.recommendation is not None
    assert normalized.recommendation.action == "Upgrade the RdsDbInstance as recommended."
