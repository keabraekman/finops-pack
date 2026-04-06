"""Assessment job handler."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict

from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.api.runner import RunOrchestrator
from finops_pack.api.storage import RunRecord, SQLiteLeadStore
from finops_pack.domain.models.assessment import AccountScopeType, DiscoveredAccount
from finops_pack.integrations.aws.org_discovery import discover_organization_accounts_for_role
from finops_pack.jobs.messages import AssessmentJobMessage


class AssessmentRunFailed(RuntimeError):
    """Raised when the scan completed but the customer-visible run failed."""


AccountDiscovery = Callable[[RunRecord], Sequence[DiscoveredAccount]]


class AssessmentJobHandler:
    """Execute a queued AWS Savings Review assessment."""

    def __init__(
        self,
        *,
        store: SQLiteLeadStore,
        orchestrator: RunOrchestrator,
        account_discovery: AccountDiscovery | None = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._account_discovery = account_discovery or self._discover_accounts

    def handle(self, message: AssessmentJobMessage) -> None:
        """Run account discovery when needed, then generate the report."""
        run = self._store.get_run_by_public_id(message.run_public_id)
        if run is None:
            raise RuntimeError(f"Run {message.run_public_id} no longer exists.")

        if message.account_scope == AccountScopeType.ORGANIZATION:
            self._persist_discovered_accounts(run)

        self._orchestrator.run_report(message.run_public_id)
        finished_run = self._store.get_run_by_public_id(message.run_public_id)
        if finished_run is not None and finished_run.status == "FAILED":
            raise AssessmentRunFailed(
                finished_run.error_summary or "The report run failed."
            )

    def _persist_discovered_accounts(self, run: RunRecord) -> None:
        try:
            discovered_accounts = tuple(self._account_discovery(run))
        except (RuntimeError, ClientError, BotoCoreError) as exc:
            self._store.merge_run_validation_payload(
                run_public_id=run.public_id,
                updates={
                    "account_scope": AccountScopeType.ORGANIZATION.value,
                    "organization_discovery_status": "unavailable",
                    "organization_discovery_reason": _friendly_org_discovery_error(exc),
                    "discovered_accounts": [],
                },
            )
            return

        self._store.merge_run_validation_payload(
            run_public_id=run.public_id,
            updates={
                "account_scope": AccountScopeType.ORGANIZATION.value,
                "organization_discovery_status": "completed",
                "discovered_accounts": [
                    asdict(account) for account in discovered_accounts
                ],
            },
        )

    def _discover_accounts(self, run: RunRecord) -> Sequence[DiscoveredAccount]:
        return discover_organization_accounts_for_role(
            role_arn=run.role_arn,
            external_id=run.external_id,
            session_name="aws-savings-review-org-discovery",
        )


def _friendly_org_discovery_error(exc: Exception) -> str:
    return (
        "AWS Organizations account discovery was unavailable for this role. "
        "The assessment will continue with the submitted management-account role only."
        f" Detail: {exc}"
    )

