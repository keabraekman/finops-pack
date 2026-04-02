from finops_pack.analyzers.rds_schedule import build_rds_schedule_actions
from finops_pack.config import BusinessHours, ScheduleConfig
from finops_pack.models import AccountMapEntry


def test_build_rds_schedule_actions_only_targets_stoppable_nonprod_databases() -> None:
    actions = build_rds_schedule_actions(
        {
            "items": [
                {
                    "accountId": "111111111111",
                    "accountName": "sandbox",
                    "region": "us-east-1",
                    "dbInstanceIdentifier": "dev-db",
                    "dbInstanceClass": "db.t3.medium",
                    "engine": "postgres",
                    "status": "available",
                    "multiAz": False,
                    "dbClusterIdentifier": "",
                    "readReplicaSourceDBInstanceIdentifier": "",
                    "readReplicaDBInstanceIdentifiers": [],
                },
                {
                    "accountId": "222222222222",
                    "accountName": "prod",
                    "region": "us-east-1",
                    "dbInstanceIdentifier": "prod-db",
                    "dbInstanceClass": "db.m5.large",
                    "engine": "postgres",
                    "status": "available",
                    "multiAz": False,
                    "dbClusterIdentifier": "",
                    "readReplicaSourceDBInstanceIdentifier": "",
                    "readReplicaDBInstanceIdentifiers": [],
                },
                {
                    "accountId": "111111111111",
                    "accountName": "sandbox",
                    "region": "us-east-1",
                    "dbInstanceIdentifier": "aurora-db",
                    "dbInstanceClass": "db.r5.large",
                    "engine": "aurora-postgresql",
                    "status": "available",
                    "multiAz": False,
                    "dbClusterIdentifier": "cluster-1",
                    "readReplicaSourceDBInstanceIdentifier": "",
                    "readReplicaDBInstanceIdentifiers": [],
                },
            ]
        },
        account_map=[
            AccountMapEntry(account_id="111111111111", name="sandbox", environment="nonprod"),
            AccountMapEntry(account_id="222222222222", name="prod", environment="prod"),
        ],
        schedule=ScheduleConfig(
            timezone="UTC",
            business_hours=BusinessHours(
                days=["mon", "tue", "wed", "thu", "fri"],
                start_hour=9,
                end_hour=17,
            ),
        ),
    )

    assert len(actions) == 1
    action = actions[0]
    assert action.action_label == "Stop 1 non-prod RDS instance off-hours"
    assert action.source_label == "Native finops-pack"
    assert action.monthly_savings > 0
    assert action.opportunity_count == 1
    assert action.supporting_items[0]["resource_id"] == "dev-db"
