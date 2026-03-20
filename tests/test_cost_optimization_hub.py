from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from finops_pack.aws.cost_optimization_hub import (
    enable_cost_optimization_hub,
    list_recommendation_summaries,
    list_recommendations,
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
    paginator = Mock()
    paginator.paginate.return_value = [
        {
            "estimatedTotalDedupedSavings": 125.5,
            "currencyCode": "USD",
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
    client = Mock()
    client.get_paginator.return_value = paginator
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendation_summaries(session, region_name="us-east-1")

    session.client.assert_called_once_with("cost-optimization-hub", region_name="us-east-1")
    client.get_paginator.assert_called_once_with("list_recommendation_summaries")
    paginator.paginate.assert_called_once_with()
    assert snapshot["itemCount"] == 2
    assert snapshot["estimatedTotalDedupedSavings"] == 125.5
    assert len(snapshot["pages"]) == 2


def test_list_recommendations_collects_all_pages() -> None:
    paginator = Mock()
    paginator.paginate.return_value = [
        {
            "items": [
                {"recommendationId": "rec-1", "estimatedMonthlySavings": 12.5},
                {"recommendationId": "rec-2", "estimatedMonthlySavings": 8.0},
            ]
        },
        {"items": [{"recommendationId": "rec-3", "estimatedMonthlySavings": 3.0}]},
    ]
    client = Mock()
    client.get_paginator.return_value = paginator
    session = Mock()
    session.client.return_value = client

    snapshot = list_recommendations(session)

    client.get_paginator.assert_called_once_with("list_recommendations")
    paginator.paginate.assert_called_once_with(includeAllRecommendations=True)
    assert snapshot["itemCount"] == 3
    assert len(snapshot["items"]) == 3


def test_list_recommendation_summaries_wraps_paginator_errors() -> None:
    paginator = Mock()
    paginator.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "ListRecommendationSummaries",
    )
    client = Mock()
    client.get_paginator.return_value = paginator
    session = Mock()
    session.client.return_value = client

    with pytest.raises(
        RuntimeError, match="Failed to list Cost Optimization Hub recommendation summaries"
    ):
        list_recommendation_summaries(session)
