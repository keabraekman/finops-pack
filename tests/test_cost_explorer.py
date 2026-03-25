from unittest.mock import Mock, call

import pytest
from botocore.exceptions import ClientError

import finops_pack.aws.cost_explorer as cost_explorer
from finops_pack.aws.cost_explorer import (
    build_resource_cost_series_lookup,
    collect_resource_daily_costs,
    collect_spend_baseline,
    find_resource_cost_series,
    format_resource_cost_series,
)


def test_collect_spend_baseline_uses_monthly_unblended_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cost_explorer,
        "_rolling_completed_day_window",
        Mock(return_value={"Start": "2026-02-22", "End": "2026-03-24"}),
    )
    client = Mock()
    client.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-02-22", "End": "2026-03-01"},
                "Total": {"UnblendedCost": {"Amount": "84.50", "Unit": "USD"}},
            },
            {
                "TimePeriod": {"Start": "2026-03-01", "End": "2026-03-24"},
                "Total": {"UnblendedCost": {"Amount": "115.50", "Unit": "USD"}},
            },
        ]
    }
    session = Mock()
    session.client.return_value = client

    snapshot, baseline = collect_spend_baseline(session, region_name="us-east-1")

    session.client.assert_called_once_with("ce", region_name="us-east-1")
    client.get_cost_and_usage.assert_called_once_with(
        TimePeriod={"Start": "2026-02-22", "End": "2026-03-24"},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    assert snapshot["totalAmount"] == 200.0
    assert snapshot["bucketCount"] == 2
    assert baseline.total_amount == 200.0
    assert baseline.average_daily_amount == 6.67
    assert len(baseline.monthly_buckets) == 2


def test_collect_resource_daily_costs_groups_by_resource_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cost_explorer,
        "_rolling_completed_day_window",
        Mock(return_value={"Start": "2026-03-10", "End": "2026-03-24"}),
    )
    client = Mock()
    client.get_cost_and_usage_with_resources.side_effect = [
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-11"},
                    "Groups": [
                        {"Keys": ["i-123"], "Metrics": {"UnblendedCost": {"Amount": "4.2"}}}
                    ],
                }
            ],
            "NextPageToken": "token-1",
        },
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-11", "End": "2026-03-12"},
                    "Groups": [
                        {"Keys": ["i-456"], "Metrics": {"UnblendedCost": {"Amount": "3.1"}}}
                    ],
                }
            ]
        },
    ]
    session = Mock()
    session.client.return_value = client

    snapshot = collect_resource_daily_costs(session, region_name="us-east-1")

    session.client.assert_called_once_with("ce", region_name="us-east-1")
    assert client.get_cost_and_usage_with_resources.call_args_list == [
        call(
            TimePeriod={"Start": "2026-03-10", "End": "2026-03-24"},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter={
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": ["Amazon Elastic Compute Cloud - Compute"],
                }
            },
            GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
        ),
        call(
            TimePeriod={"Start": "2026-03-10", "End": "2026-03-24"},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            Filter={
                "Dimensions": {
                    "Key": "SERVICE",
                    "Values": ["Amazon Elastic Compute Cloud - Compute"],
                }
            },
            GroupBy=[{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            NextPageToken="token-1",
        ),
    ]
    assert snapshot["timePeriodCount"] == 2
    assert snapshot["groupCount"] == 2


def test_collect_resource_daily_costs_wraps_client_errors() -> None:
    client = Mock()
    client.get_cost_and_usage_with_resources.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetCostAndUsageWithResources",
    )
    session = Mock()
    session.client.return_value = client

    with pytest.raises(RuntimeError, match="Failed to collect Cost Explorer resource-level"):
        collect_resource_daily_costs(session)


def test_build_resource_cost_series_lookup_supports_resource_id_and_arn_matching() -> None:
    lookup = build_resource_cost_series_lookup(
        {
            "resultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-10", "End": "2026-03-11"},
                    "Groups": [
                        {
                            "Keys": ["i-1234567890abcdef0"],
                            "Metrics": {"UnblendedCost": {"Amount": "4.20", "Unit": "USD"}},
                        }
                    ],
                },
                {
                    "TimePeriod": {"Start": "2026-03-11", "End": "2026-03-12"},
                    "Groups": [
                        {
                            "Keys": ["i-1234567890abcdef0"],
                            "Metrics": {"UnblendedCost": {"Amount": "3.10", "Unit": "USD"}},
                        }
                    ],
                },
            ]
        }
    )

    direct_match = find_resource_cost_series(lookup, resource_id="i-1234567890abcdef0")
    arn_match = find_resource_cost_series(
        lookup,
        resource_arn="arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0",
    )

    assert direct_match is not None
    assert arn_match is not None
    assert direct_match.total_amount == 7.3
    assert len(direct_match.daily_costs) == 2
    assert arn_match.identifier == "i-1234567890abcdef0"
    assert format_resource_cost_series(direct_match) == "2026-03-10=$4.20; 2026-03-11=$3.10"
