"""AWS validation and setup helpers for the lead-gen app."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.api.settings import WebSettings
from finops_pack.cli import (
    _check_cost_explorer,
    _check_cost_optimization_hub,
    _check_resource_level_costs,
    _get_account_id,
)
from finops_pack.domain.models import AccessCheck
from finops_pack.domain.models.assessment import AccountScopeType
from finops_pack.integrations.aws.assume_role import assume_role_session
from finops_pack.integrations.policy.iam_policy_generator import render_policy

ROLE_ARN_RE = re.compile(r"^arn:aws:iam::\d{12}:role\/[\w+=,.@\-\/]{1,512}$")


@dataclass(frozen=True)
class ValidationCheckResult:
    """A single validation checkpoint rendered in the UI."""

    label: str
    level: str
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    """User-facing validation result for a lead submission."""

    can_proceed: bool
    account_id: str | None
    account_scope: AccountScopeType
    resolved_regions: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: tuple[ValidationCheckResult, ...]

    def to_payload(self) -> dict[str, Any]:
        """Serialize the validation result into JSON-safe data."""
        return {
            "can_proceed": self.can_proceed,
            "account_id": self.account_id,
            "account_scope": self.account_scope.value,
            "resolved_regions": list(self.resolved_regions),
            "blocking_issues": list(self.blocking_issues),
            "warnings": list(self.warnings),
            "checks": [asdict(check) for check in self.checks],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ValidationResult:
        """Deserialize a validation result from storage."""
        checks = tuple(
            ValidationCheckResult(
                label=str(item.get("label", "")),
                level=str(item.get("level", "warning")),
                detail=str(item.get("detail", "")),
            )
            for item in payload.get("checks", [])
            if isinstance(item, dict)
        )
        return cls(
            can_proceed=bool(payload.get("can_proceed")),
            account_id=(
                str(payload["account_id"])
                if isinstance(payload.get("account_id"), str) and payload.get("account_id")
                else None
            ),
            account_scope=AccountScopeType.from_form_value(
                str(payload.get("account_scope", AccountScopeType.SINGLE_ACCOUNT.value))
            ),
            resolved_regions=tuple(
                str(item)
                for item in payload.get("resolved_regions", [])
                if isinstance(item, str) and item
            ),
            blocking_issues=tuple(str(item) for item in payload.get("blocking_issues", [])),
            warnings=tuple(str(item) for item in payload.get("warnings", [])),
            checks=checks,
        )


def generate_external_id(company_name: str | None = None) -> str:
    """Generate a customer-specific external ID suggestion."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (company_name or "customer").strip().lower()).strip("-")
    if not cleaned:
        cleaned = "customer"
    token = secrets.token_urlsafe(6).replace("_", "").replace("-", "")
    return f"aws-savings-review-{cleaned[:20]}-{token[:10]}"


def build_trust_policy(*, trusted_account_id: str, external_id: str) -> str:
    """Render the trust policy JSON shown on the setup page."""
    payload = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowSavingsReviewWithExternalId",
                "Effect": "Allow",
                "Principal": {
                    "AWS": f"arn:aws:iam::{trusted_account_id}:root",
                },
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "sts:ExternalId": external_id,
                    }
                },
            }
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def build_permissions_policy() -> str:
    """Render the read-only permissions policy JSON."""
    return render_policy("min")


def _friendly_assume_role_error(raw_message: str) -> str:
    """Translate low-level STS failures into plain-English guidance."""
    lowered = raw_message.lower()
    if "accessdenied" in lowered or "not authorized" in lowered:
        return (
            "We could not assume the role. Double-check the trusted AWS account ID, "
            "the external ID in the trust policy, and the role ARN you pasted."
        )
    if "could not be found" in lowered or "no such entity" in lowered:
        return "We could not find that IAM role ARN. Confirm the role exists in the target account."
    return (
        "We could not assume the role with the details provided. "
        "Please recheck the role ARN and external ID."
    )


def _discover_active_regions(
    session: Any,
    *,
    fallback_regions: tuple[str, ...],
) -> tuple[tuple[str, ...], str | None]:
    """Discover active commercial regions, or fall back to configured defaults."""
    try:
        response = session.client("ec2", region_name="us-east-1").describe_regions(
            AllRegions=False
        )
    except (ClientError, BotoCoreError):
        return (
            fallback_regions,
            (
                "Active region discovery was unavailable, so this review will use the "
                "fallback coverage set instead."
            ),
        )

    discovered_regions = [
        str(item.get("RegionName", "")).strip()
        for item in response.get("Regions", [])
        if isinstance(item, dict)
    ]
    filtered_regions = [
        region
        for region in discovered_regions
        if region
        and region.count("-") == 2
        and not region.startswith(("cn-", "us-gov-"))
    ]
    if not filtered_regions:
        return (
            fallback_regions,
            (
                "No active commercial regions were returned during discovery, so this review "
                "will use the fallback coverage set instead."
            ),
        )

    ordered_regions: list[str] = []
    seen: set[str] = set()
    for region in [*fallback_regions, *filtered_regions]:
        if region in seen or region not in filtered_regions:
            continue
        seen.add(region)
        ordered_regions.append(region)
    return (tuple(ordered_regions), None)


def _map_access_check(
    check: AccessCheck,
    *,
    warning_when_not_active: bool = False,
) -> tuple[ValidationCheckResult, str | None, str | None]:
    """Map access checks to UI-ready validation output."""
    is_active = check.status == "ACTIVE" and check.enabled is not False
    if is_active:
        return (
            ValidationCheckResult(
                label=check.label,
                level="pass",
                detail=check.reason or "Ready.",
            ),
            None,
            None,
        )

    message = check.reason or "This check did not return a ready state."
    if warning_when_not_active:
        return (
            ValidationCheckResult(
                label=check.label,
                level="warning",
                detail=message,
            ),
            None,
            message,
        )

    return (
        ValidationCheckResult(
            label=check.label,
            level="fail",
            detail=message,
        ),
        message,
        None,
    )


class SubmissionValidator:
    """Validate the role setup details submitted through the website."""

    def __init__(self, settings: WebSettings) -> None:
        self._settings = settings

    def validate_submission(
        self,
        *,
        role_arn: str,
        external_id: str,
        confirmed_cost_explorer: bool,
        confirmed_cost_optimization_hub: bool,
        account_scope: AccountScopeType = AccountScopeType.SINGLE_ACCOUNT,
    ) -> ValidationResult:
        """Validate the AWS setup information before collecting an email address."""
        blocking_issues: list[str] = []
        warnings: list[str] = []
        checks: list[ValidationCheckResult] = []

        normalized_role_arn = role_arn.strip()
        normalized_external_id = external_id.strip()

        if not ROLE_ARN_RE.match(normalized_role_arn):
            blocking_issues.append(
                "Paste a valid IAM role ARN, for example "
                "arn:aws:iam::123456789012:role/aws-savings-review-readonly."
            )
            checks.append(
                ValidationCheckResult(
                    label="Role ARN format",
                    level="fail",
                    detail="The role ARN does not look like an IAM role ARN.",
                )
            )
        else:
            checks.append(
                ValidationCheckResult(
                    label="Role ARN format",
                    level="pass",
                    detail="The role ARN format looks valid.",
                )
            )

        if not normalized_external_id:
            blocking_issues.append("Paste the external ID you placed in the role trust policy.")
            checks.append(
                ValidationCheckResult(
                    label="External ID provided",
                    level="fail",
                    detail="An external ID is required so the role can be assumed safely.",
                )
            )
        else:
            checks.append(
                ValidationCheckResult(
                    label="External ID provided",
                    level="pass",
                    detail="An external ID was provided.",
                )
            )

        if not confirmed_cost_explorer:
            blocking_issues.append("Confirm that Cost Explorer is enabled before continuing.")
        if not confirmed_cost_optimization_hub:
            blocking_issues.append(
                "Confirm that Cost Optimization Hub is enabled before continuing."
            )

        if blocking_issues:
            return ValidationResult(
                can_proceed=False,
                account_id=None,
                account_scope=account_scope,
                resolved_regions=(),
                blocking_issues=tuple(blocking_issues),
                warnings=tuple(warnings),
                checks=tuple(checks),
            )

        try:
            session = assume_role_session(
                role_arn=normalized_role_arn,
                external_id=normalized_external_id,
                session_name=self._settings.session_name,
                region_name="us-east-1",
            )
        except RuntimeError as exc:
            checks.append(
                ValidationCheckResult(
                    label="Assume role",
                    level="fail",
                    detail=_friendly_assume_role_error(str(exc)),
                )
            )
            return ValidationResult(
                can_proceed=False,
                account_id=None,
                account_scope=account_scope,
                resolved_regions=(),
                blocking_issues=(_friendly_assume_role_error(str(exc)),),
                warnings=tuple(warnings),
                checks=tuple(checks),
            )

        account_id = _get_account_id(session)
        checks.append(
            ValidationCheckResult(
                label="Assume role",
                level="pass",
                detail=(
                    f"Read-only role assumption worked for account {account_id}."
                    if account_id
                    else "Read-only role assumption worked."
                ),
            )
        )
        resolved_regions, region_warning = _discover_active_regions(
            session,
            fallback_regions=self._settings.default_regions,
        )
        checks.append(
            ValidationCheckResult(
                label="Region coverage",
                level="warning" if region_warning else "pass",
                detail=(
                    region_warning
                    or f"This review will scan {len(resolved_regions)} active AWS regions."
                ),
            )
        )
        if region_warning is not None:
            warnings.append(region_warning)

        ce_check = _check_cost_explorer(session)
        ce_result, ce_blocker, _ = _map_access_check(ce_check)
        checks.append(ce_result)
        if ce_blocker is not None:
            blocking_issues.append(ce_blocker)

        coh_check = _check_cost_optimization_hub(session, account_id=account_id)
        coh_result, _, coh_warning = _map_access_check(
            coh_check,
            warning_when_not_active=True,
        )
        checks.append(coh_result)
        if coh_warning is not None:
            warnings.append(
                "Cost Optimization Hub is not fully ready yet. "
                "The report can still run, but AWS-native recommendation coverage may be thin."
            )

        resource_check = _check_resource_level_costs(session)
        resource_result, _, resource_warning = _map_access_check(
            resource_check,
            warning_when_not_active=True,
        )
        checks.append(resource_result)
        if resource_warning is not None:
            warnings.append(
                "Resource-level Cost Explorer data is optional. "
                "If it is not enabled yet, schedule-style savings estimates may be limited."
            )

        return ValidationResult(
            can_proceed=not blocking_issues,
            account_id=account_id,
            account_scope=account_scope,
            resolved_regions=resolved_regions,
            blocking_issues=tuple(blocking_issues),
            warnings=tuple(warnings),
            checks=tuple(checks),
        )
