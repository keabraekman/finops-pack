"""Collectors for AWS Organizations account inventory."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.models import AccountRecord


def list_accounts(session: boto3.Session) -> list[AccountRecord]:
    """Collect and normalize AWS Organizations accounts."""
    client = session.client("organizations")
    paginator = client.get_paginator("list_accounts")
    accounts: list[AccountRecord] = []

    try:
        for page in paginator.paginate():
            for raw_account in page.get("Accounts", []):
                account_id = raw_account.get("Id")
                name = raw_account.get("Name")
                if not account_id or not name:
                    raise RuntimeError("ListAccounts returned an account without Id or Name.")

                status = raw_account.get("Status") or raw_account.get("State") or "UNKNOWN"
                accounts.append(
                    AccountRecord(
                        account_id=account_id,
                        name=name,
                        email=raw_account.get("Email"),
                        status=status,
                    )
                )
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to list AWS Organizations accounts: {exc}") from exc

    return sorted(accounts, key=lambda account: (account.name.lower(), account.account_id))


def load_account_records(path: str | Path) -> list[AccountRecord]:
    """Load account records from a JSON fixture."""
    fixture_path = Path(path)
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("accounts.json must contain a top-level array.")

    return [AccountRecord(**item) for item in raw]
