import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from finops_pack.integrations.aws.collectors.organizations import (
    list_accounts,
    load_account_records,
)


def test_list_accounts_uses_paginator_and_normalizes_records() -> None:
    paginator = Mock()
    paginator.paginate.return_value = [
        {
            "Accounts": [
                {
                    "Id": "222222222222",
                    "Name": "sandbox-apps",
                    "Email": "sandbox-apps@example.com",
                    "State": "ACTIVE",
                }
            ]
        },
        {
            "Accounts": [
                {
                    "Id": "111111111111",
                    "Name": "prod-core",
                    "Email": "prod-core@example.com",
                    "Status": "SUSPENDED",
                }
            ]
        },
    ]
    client = Mock()
    client.get_paginator.return_value = paginator
    session = Mock()
    session.client.return_value = client

    accounts = list_accounts(session)

    session.client.assert_called_once_with("organizations")
    client.get_paginator.assert_called_once_with("list_accounts")
    assert [account.account_id for account in accounts] == ["111111111111", "222222222222"]
    assert accounts[0].status == "SUSPENDED"
    assert accounts[1].status == "ACTIVE"


def test_list_accounts_wraps_client_errors() -> None:
    paginator = Mock()
    paginator.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "ListAccounts",
    )
    client = Mock()
    client.get_paginator.return_value = paginator
    session = Mock()
    session.client.return_value = client

    with pytest.raises(RuntimeError, match="Failed to list AWS Organizations accounts"):
        list_accounts(session)


def test_load_account_records_reads_json_fixture(tmp_path: Path) -> None:
    fixture_path = tmp_path / "accounts.json"
    fixture_path.write_text(
        json.dumps(
            [
                {
                    "account_id": "123456789012",
                    "name": "prod-core",
                    "email": "prod-core@example.com",
                    "status": "ACTIVE",
                }
            ]
        ),
        encoding="utf-8",
    )

    accounts = load_account_records(fixture_path)

    assert len(accounts) == 1
    assert accounts[0].name == "prod-core"
