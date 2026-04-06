"""AWS Organization account discovery use case."""

from __future__ import annotations

from finops_pack.domain.models.assessment import DiscoveredAccount
from finops_pack.integrations.aws.org_discovery import discover_organization_accounts_for_role


def discover_accounts(
    *,
    role_arn: str,
    external_id: str,
    session_name: str,
) -> tuple[DiscoveredAccount, ...]:
    """Discover accounts from a submitted AWS Organization management-account role."""
    return discover_organization_accounts_for_role(
        role_arn=role_arn,
        external_id=external_id,
        session_name=session_name,
    )

