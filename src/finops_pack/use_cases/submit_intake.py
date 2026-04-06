"""Normalize and submit customer intake data."""

from __future__ import annotations

from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.domain.schemas.intake import IntakeSubmission


def build_intake_submission(
    *,
    company_name: str,
    contact_name: str,
    email: str,
    account_scope: str,
    role_arn: str,
    external_id: str,
    notes: str = "",
) -> IntakeSubmission:
    """Return a normalized intake submission from form data."""
    return IntakeSubmission(
        company_name=company_name.strip() or None,
        contact_name=contact_name.strip() or None,
        email=email.strip().lower(),
        account_scope=AccountScopeType.from_form_value(account_scope),
        role_arn=role_arn.strip(),
        external_id=external_id.strip(),
        notes=notes.strip() or None,
    )

