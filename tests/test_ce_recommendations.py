from unittest.mock import Mock, call

from finops_pack.aws.ce_recommendations import (
    collect_rightsizing_recommendations,
    collect_savings_plans_purchase_recommendations,
)


def test_collect_rightsizing_recommendations_collects_pages() -> None:
    client = Mock()
    client.get_rightsizing_recommendation.side_effect = [
        {
            "RightsizingRecommendations": [{"AccountId": "123456789012"}],
            "NextPageToken": "token-1",
        },
        {
            "RightsizingRecommendations": [{"AccountId": "210987654321"}],
        },
    ]
    session = Mock()
    session.client.return_value = client

    snapshot = collect_rightsizing_recommendations(session, region_name="us-east-1")

    session.client.assert_called_once_with("ce", region_name="us-east-1")
    assert client.get_rightsizing_recommendation.call_args_list == [
        call(
            Service="AmazonEC2",
            Configuration={
                "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                "BenefitsConsidered": True,
            },
            PageSize=20,
        ),
        call(
            Service="AmazonEC2",
            Configuration={
                "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                "BenefitsConsidered": True,
            },
            PageSize=20,
            NextPageToken="token-1",
        ),
    ]
    assert snapshot["recommendationCount"] == 2


def test_collect_savings_plans_purchase_recommendations_starts_and_fetches_details() -> None:
    client = Mock()
    client.start_savings_plans_purchase_recommendation_generation.return_value = {
        "RecommendationId": "sp-rec-1"
    }
    client.get_savings_plans_purchase_recommendation.return_value = {
        "Metadata": {"RecommendationId": "sp-rec-1"},
        "SavingsPlansPurchaseRecommendation": {
            "SavingsPlansPurchaseRecommendationDetails": [
                {"RecommendationDetailId": "detail-1"}
            ]
        },
    }
    client.get_savings_plan_purchase_recommendation_details.return_value = {
        "RecommendationDetailId": "detail-1",
        "RecommendationDetailData": {"EstimatedMonthlySavingsAmount": "12.34"},
    }
    session = Mock()
    session.client.return_value = client

    snapshot = collect_savings_plans_purchase_recommendations(session, region_name="us-east-1")

    session.client.assert_called_once_with("ce", region_name="us-east-1")
    client.start_savings_plans_purchase_recommendation_generation.assert_called_once_with()
    client.get_savings_plans_purchase_recommendation.assert_called_once_with(
        SavingsPlansType="COMPUTE_SP",
        TermInYears="ONE_YEAR",
        PaymentOption="NO_UPFRONT",
        AccountScope="PAYER",
        LookbackPeriodInDays="THIRTY_DAYS",
        PageSize=20,
    )
    client.get_savings_plan_purchase_recommendation_details.assert_called_once_with(
        RecommendationDetailId="detail-1"
    )
    assert snapshot["recommendationCount"] == 1
    assert snapshot["detailCount"] == 1
    assert snapshot["startGenerationResponse"] == {"RecommendationId": "sp-rec-1"}
