from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import finops_pack.collectors.ec2 as ec2
from finops_pack.collectors.ec2 import collect_ec2_inventory, derive_account_role_arn
from finops_pack.models import AccountRecord


def _build_ec2_client(*, pages: list[dict], error: ClientError | None = None) -> Mock:
    client = Mock()
    paginator = Mock()
    if error is not None:
        paginator.paginate.side_effect = error
    else:
        paginator.paginate.return_value = pages
    client.get_paginator.return_value = paginator
    return client


def test_derive_account_role_arn_preserves_role_path() -> None:
    derived = derive_account_role_arn(
        "arn:aws:iam::111111111111:role/service-role/finops-pack-readonly",
        "222222222222",
    )

    assert derived == "arn:aws:iam::222222222222:role/service-role/finops-pack-readonly"


def test_collect_ec2_inventory_collects_across_accounts_and_regions_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_session = Mock()
    member_session = Mock()
    assume_role_session = Mock(return_value=member_session)
    monkeypatch.setattr(ec2, "assume_role_session", assume_role_session)

    current_us_east_1 = _build_ec2_client(
        pages=[
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-current",
                                "InstanceType": "t3.medium",
                                "RootDeviceType": "ebs",
                                "State": {"Name": "running"},
                                "Placement": {"AvailabilityZone": "us-east-1a"},
                                "LaunchTime": datetime(2026, 3, 1, tzinfo=UTC),
                                "Tags": [{"Key": "Name", "Value": "current-app"}],
                            }
                        ]
                    }
                ]
            }
        ]
    )
    current_us_west_2 = _build_ec2_client(
        pages=[],
        error=ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}},
            "DescribeInstances",
        ),
    )
    current_session.client.side_effect = lambda service_name, region_name=None: {
        ("ec2", "us-east-1"): current_us_east_1,
        ("ec2", "us-west-2"): current_us_west_2,
    }[(service_name, region_name)]

    member_us_east_1 = _build_ec2_client(
        pages=[
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-member",
                                "InstanceType": "m5.large",
                                "RootDeviceType": "ebs",
                                "State": {"Name": "running"},
                                "Placement": {"AvailabilityZone": "us-east-1b"},
                                "LaunchTime": datetime(2026, 3, 2, tzinfo=UTC),
                                "Tags": [{"Key": "Name", "Value": "member-app"}],
                            }
                        ]
                    }
                ]
            }
        ]
    )
    member_us_west_2 = _build_ec2_client(pages=[{"Reservations": []}])
    member_session.client.side_effect = lambda service_name, region_name=None: {
        ("ec2", "us-east-1"): member_us_east_1,
        ("ec2", "us-west-2"): member_us_west_2,
    }[(service_name, region_name)]

    snapshot = collect_ec2_inventory(
        current_session,
        account_records=[
            AccountRecord(account_id="111111111111", name="management", status="ACTIVE"),
            AccountRecord(account_id="222222222222", name="member", status="ACTIVE"),
            AccountRecord(account_id="333333333333", name="suspended", status="SUSPENDED"),
        ],
        regions=["us-east-1", "us-west-2"],
        role_arn="arn:aws:iam::111111111111:role/finops-pack-readonly",
        external_id="external-id",
        session_name="test-session",
        current_account_id="111111111111",
    )

    assume_role_session.assert_called_once_with(
        role_arn="arn:aws:iam::222222222222:role/finops-pack-readonly",
        external_id="external-id",
        session_name="test-session",
        region_name="us-east-1",
    )
    assert snapshot["itemCount"] == 2
    assert snapshot["errorCount"] == 2
    assert [item["instanceId"] for item in snapshot["items"]] == ["i-current", "i-member"]
    assert snapshot["items"][0]["instanceArn"] == (
        "arn:aws:ec2:us-east-1:111111111111:instance/i-current"
    )
    assert snapshot["items"][1]["accountName"] == "member"
    assert snapshot["items"][1]["launchTime"] == "2026-03-02T00:00:00+00:00"
    assert snapshot["errors"][0]["scope"] == "region"
    assert snapshot["errors"][0]["region"] == "us-west-2"
    assert snapshot["errors"][1]["error"] == "Skipped because account status is SUSPENDED."
