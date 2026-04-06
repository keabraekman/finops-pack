"""Assessment enqueue use case."""

from __future__ import annotations

from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.jobs.coordinator import JobCoordinator
from finops_pack.jobs.messages import AssessmentJobMessage


def enqueue_assessment(
    *,
    coordinator: JobCoordinator,
    run_public_id: str,
    account_scope: AccountScopeType,
) -> AssessmentJobMessage:
    """Queue an assessment run for a background worker."""
    return coordinator.enqueue_assessment(
        run_public_id=run_public_id,
        account_scope=account_scope,
    )

