"""Job message models."""

from __future__ import annotations

from dataclasses import dataclass

from finops_pack.domain.models.assessment import AccountScopeType


@dataclass(frozen=True)
class AssessmentJobMessage:
    """Message consumed by the worker for an assessment run."""

    job_public_id: str
    run_public_id: str
    account_scope: AccountScopeType
    attempt: int = 0

