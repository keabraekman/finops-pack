import pytest

from finops_pack.analyzers.account_classification import (
    classify_account,
    validate_account_overrides,
)
from finops_pack.models import AccountRecord


def test_classify_account_prefers_prod_override() -> None:
    account = AccountRecord(account_id="111111111111", name="sandbox-apps")

    entry = classify_account(
        account,
        prod_account_ids=["111111111111"],
        nonprod_account_ids=[],
    )

    assert entry.environment == "prod"
    assert entry.confidence == "high"
    assert entry.classification_source == "config"


def test_classify_account_prefers_nonprod_override_over_regex() -> None:
    account = AccountRecord(account_id="222222222222", name="production-core")

    entry = classify_account(
        account,
        prod_account_ids=[],
        nonprod_account_ids=["222222222222"],
    )

    assert entry.environment == "nonprod"
    assert entry.confidence == "high"
    assert entry.classification_source == "config"


def test_classify_account_uses_nonprod_regex() -> None:
    account = AccountRecord(account_id="333333333333", name="non-prod-data")

    entry = classify_account(
        account,
        prod_account_ids=[],
        nonprod_account_ids=[],
    )

    assert entry.environment == "nonprod"
    assert entry.confidence == "medium"
    assert entry.classification_source == "regex"


def test_classify_account_returns_unknown_when_no_rule_matches() -> None:
    account = AccountRecord(account_id="444444444444", name="shared-services")

    entry = classify_account(
        account,
        prod_account_ids=[],
        nonprod_account_ids=[],
    )

    assert entry.environment == "unknown"
    assert entry.confidence == "low"
    assert entry.classification_source == "default"


def test_validate_account_overrides_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        validate_account_overrides(["123456789012"], ["123456789012"])
