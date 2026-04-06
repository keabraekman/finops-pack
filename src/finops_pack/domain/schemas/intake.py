"""Input validation helpers for customer intake."""

from __future__ import annotations

import re
from dataclasses import dataclass

from finops_pack.domain.models.assessment import AccountScopeType

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class IntakeSubmission:
    """Normalized customer intake payload."""

    company_name: str | None
    contact_name: str | None
    email: str
    account_scope: AccountScopeType
    role_arn: str
    external_id: str
    notes: str | None = None

    @property
    def is_valid_email(self) -> bool:
        """Return True when the submitted email looks deliverable enough for v1."""
        return bool(EMAIL_RE.match(self.email))
