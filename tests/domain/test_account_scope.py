from finops_pack.domain.models.assessment import AccountScope, AccountScopeType


def test_account_scope_parses_single_and_organization_modes() -> None:
    assert AccountScopeType.from_form_value("single_account") == AccountScopeType.SINGLE_ACCOUNT
    assert AccountScopeType.from_form_value("organization") == AccountScopeType.ORGANIZATION
    assert AccountScopeType.from_form_value("org") == AccountScopeType.ORGANIZATION

    scope = AccountScope(
        scope_type=AccountScopeType.ORGANIZATION,
        role_arn="arn:aws:iam::123456789012:role/aws-savings-review-readonly",
        external_id="aws-savings-review-example",
    )
    assert scope.is_organization is True

