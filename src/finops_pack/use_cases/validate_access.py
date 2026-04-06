"""Access validation use case wrapper."""

from __future__ import annotations

from finops_pack.api.validation import SubmissionValidator, ValidationResult
from finops_pack.domain.models.assessment import AccountScopeType


def validate_access(
    *,
    validator: SubmissionValidator,
    role_arn: str,
    external_id: str,
    account_scope: AccountScopeType,
    confirmed_cost_explorer: bool,
    confirmed_cost_optimization_hub: bool,
) -> ValidationResult:
    """Validate a submitted cross-account role before queueing assessment work."""
    return validator.validate_submission(
        role_arn=role_arn,
        external_id=external_id,
        account_scope=account_scope,
        confirmed_cost_explorer=confirmed_cost_explorer,
        confirmed_cost_optimization_hub=confirmed_cost_optimization_hub,
    )

