from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.use_cases.submit_intake import build_intake_submission


def test_build_intake_submission_normalizes_org_scope_and_email() -> None:
    submission = build_intake_submission(
        company_name=" Example Co ",
        contact_name=" Jane ",
        email=" Jane@Example.com ",
        account_scope="organization",
        role_arn=" arn:aws:iam::123456789012:role/aws-savings-review-readonly ",
        external_id=" ext-123 ",
        notes=" Review prod first ",
    )

    assert submission.email == "jane@example.com"
    assert submission.account_scope == AccountScopeType.ORGANIZATION
    assert submission.company_name == "Example Co"
    assert submission.notes == "Review prod first"
    assert submission.is_valid_email
