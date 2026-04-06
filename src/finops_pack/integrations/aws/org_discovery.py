"""AWS Organizations discovery helpers."""

from __future__ import annotations

from typing import Any

from finops_pack.domain.models.assessment import DiscoveredAccount
from finops_pack.integrations.aws.collectors.organizations import list_accounts
from finops_pack.integrations.aws.session_factory import build_assumed_role_session


def discover_organization_accounts(session: Any) -> tuple[DiscoveredAccount, ...]:
    """Return active accounts visible from an AWS Organizations management account."""
    accounts = list_accounts(session)
    return tuple(
        DiscoveredAccount(
            account_id=account.account_id,
            name=account.name,
            email=account.email,
            status=account.status,
        )
        for account in accounts
    )


def discover_organization_accounts_for_role(
    *,
    role_arn: str,
    external_id: str,
    session_name: str,
) -> tuple[DiscoveredAccount, ...]:
    """Assume a management-account role and discover Organization accounts."""
    session = build_assumed_role_session(
        role_arn=role_arn,
        external_id=external_id,
        session_name=session_name,
        region_name="us-east-1",
    )
    return discover_organization_accounts(session)
