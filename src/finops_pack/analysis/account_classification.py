"""Classification rules for AWS account environments."""

from __future__ import annotations

import re
from collections.abc import Sequence

from finops_pack.domain.models import AccountMapEntry, AccountRecord

NONPROD_NAME_PATTERN = re.compile(
    r"\b(?:dev|development|test|testing|qa|uat|stage|staging|sandbox|nonprod|non-prod|preprod|pre-prod)\b",
    re.IGNORECASE,
)
PROD_NAME_PATTERN = re.compile(r"\b(?:prod|production|live)\b", re.IGNORECASE)


def validate_account_overrides(
    prod_account_ids: Sequence[str], nonprod_account_ids: Sequence[str]
) -> None:
    """Ensure account ID overrides do not conflict."""
    overlaps = set(prod_account_ids) & set(nonprod_account_ids)
    if overlaps:
        raise ValueError(
            "prod_account_ids and nonprod_account_ids cannot overlap: "
            + ", ".join(sorted(overlaps))
        )


def classify_account(
    account: AccountRecord,
    *,
    prod_account_ids: Sequence[str],
    nonprod_account_ids: Sequence[str],
) -> AccountMapEntry:
    """Classify a single account as prod, nonprod, or unknown."""
    validate_account_overrides(prod_account_ids, nonprod_account_ids)

    prod_ids = set(prod_account_ids)
    nonprod_ids = set(nonprod_account_ids)

    if account.account_id in prod_ids:
        return AccountMapEntry(
            account_id=account.account_id,
            name=account.name,
            email=account.email,
            status=account.status,
            environment="prod",
            confidence="high",
            classification_source="config",
            classification_reason="Matched prod_account_ids override.",
        )

    if account.account_id in nonprod_ids:
        return AccountMapEntry(
            account_id=account.account_id,
            name=account.name,
            email=account.email,
            status=account.status,
            environment="nonprod",
            confidence="high",
            classification_source="config",
            classification_reason="Matched nonprod_account_ids override.",
        )

    if NONPROD_NAME_PATTERN.search(account.name):
        return AccountMapEntry(
            account_id=account.account_id,
            name=account.name,
            email=account.email,
            status=account.status,
            environment="nonprod",
            confidence="medium",
            classification_source="regex",
            classification_reason="Matched default non-production name pattern.",
        )

    if PROD_NAME_PATTERN.search(account.name):
        return AccountMapEntry(
            account_id=account.account_id,
            name=account.name,
            email=account.email,
            status=account.status,
            environment="prod",
            confidence="medium",
            classification_source="regex",
            classification_reason="Matched default production name pattern.",
        )

    return AccountMapEntry(
        account_id=account.account_id,
        name=account.name,
        email=account.email,
        status=account.status,
        environment="unknown",
        confidence="low",
        classification_source="default",
        classification_reason="No classification rule matched.",
    )


def classify_accounts(
    accounts: Sequence[AccountRecord],
    *,
    prod_account_ids: Sequence[str],
    nonprod_account_ids: Sequence[str],
) -> list[AccountMapEntry]:
    """Classify a list of account records."""
    validate_account_overrides(prod_account_ids, nonprod_account_ids)
    return [
        classify_account(
            account,
            prod_account_ids=prod_account_ids,
            nonprod_account_ids=nonprod_account_ids,
        )
        for account in accounts
    ]
