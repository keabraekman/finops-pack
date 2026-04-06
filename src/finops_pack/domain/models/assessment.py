"""Customer-facing assessment domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class AccountScopeType(StrEnum):
    """Supported AWS intake scopes."""

    SINGLE_ACCOUNT = "single_account"
    ORGANIZATION = "organization"

    @classmethod
    def from_form_value(cls, value: str | None) -> AccountScopeType:
        """Parse a form value into a supported account scope."""
        normalized = (value or "").strip().lower()
        if normalized in {"organization", "org", "aws_organization"}:
            return cls.ORGANIZATION
        return cls.SINGLE_ACCOUNT


class JobStatus(StrEnum):
    """Background job states used by the local queue."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYABLE_FAILURE = "retryable_failure"


class AssessmentRunStatus(StrEnum):
    """Customer-visible assessment run states."""

    AWAITING_EMAIL = "AWAITING_EMAIL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AccountScope:
    """AWS account scope submitted by a prospect."""

    scope_type: AccountScopeType
    role_arn: str
    external_id: str
    management_account_id: str | None = None

    @property
    def is_organization(self) -> bool:
        """Return True when this assessment should attempt Organizations discovery."""
        return self.scope_type == AccountScopeType.ORGANIZATION


@dataclass(frozen=True)
class Lead:
    """Customer lead identity."""

    email: str
    company_name: str | None = None
    contact_name: str | None = None


@dataclass(frozen=True)
class DiscoveredAccount:
    """AWS account discovered from an Organization management account."""

    account_id: str
    name: str
    email: str | None = None
    status: str = "ACTIVE"


@dataclass(frozen=True)
class ReportArtifact:
    """Report artifact generated for an assessment."""

    kind: str
    relative_path: str


@dataclass
class AssessmentRun:
    """High-level assessment run state for use cases and worker orchestration."""

    public_id: str
    account_scope: AccountScope
    status: AssessmentRunStatus = AssessmentRunStatus.QUEUED
    discovered_accounts: list[DiscoveredAccount] = field(default_factory=list)
    artifacts: list[ReportArtifact] = field(default_factory=list)

