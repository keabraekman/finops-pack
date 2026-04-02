from finops_pack.analyzers.native_ebs import build_native_ebs_actions


def test_build_native_ebs_actions_detects_cleanup_and_tuning_opportunities() -> None:
    actions = build_native_ebs_actions(
        {
            "items": [
                {
                    "accountId": "111111111111",
                    "accountName": "sandbox",
                    "region": "us-east-1",
                    "volumeId": "vol-unattached",
                    "name": "orphaned-data",
                    "state": "available",
                    "volumeType": "gp3",
                    "sizeGiB": 20,
                    "iops": 3000,
                    "throughput": 125,
                    "attachmentCount": 0,
                    "createTime": "2026-03-01T00:00:00+00:00",
                },
                {
                    "accountId": "111111111111",
                    "accountName": "sandbox",
                    "region": "us-east-1",
                    "volumeId": "vol-gp2",
                    "name": "legacy-root",
                    "state": "in-use",
                    "volumeType": "gp2",
                    "sizeGiB": 100,
                    "iops": 300,
                    "throughput": 0,
                    "attachmentCount": 1,
                    "createTime": "2026-03-10T00:00:00+00:00",
                },
                {
                    "accountId": "111111111111",
                    "accountName": "sandbox",
                    "region": "us-east-1",
                    "volumeId": "vol-gp3",
                    "name": "overprovisioned",
                    "state": "in-use",
                    "volumeType": "gp3",
                    "sizeGiB": 200,
                    "iops": 6000,
                    "throughput": 250,
                    "attachmentCount": 1,
                    "createTime": "2026-03-10T00:00:00+00:00",
                },
            ]
        }
    )

    assert [action.action_label for action in actions] == [
        "Delete 1 unattached EBS volume",
        "Migrate 1 gp2 EBS volume to gp3",
        "Reduce provisioned performance on 1 gp3 EBS volume",
    ]
    assert actions[0].monthly_savings == 1.6
    assert actions[1].monthly_savings == 2.0
    assert actions[2].monthly_savings == 20.0
    assert actions[0].source_label == "Native finops-pack"
