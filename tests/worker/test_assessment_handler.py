from pathlib import Path

from finops_pack.api.storage import SQLiteLeadStore
from finops_pack.domain.models.assessment import AccountScopeType, DiscoveredAccount
from finops_pack.jobs.messages import AssessmentJobMessage
from finops_pack.worker.handlers.assessment import AssessmentJobHandler


class DummyOrchestrator:
    def __init__(self) -> None:
        self.runs: list[str] = []

    def run_report(self, run_public_id: str) -> None:
        self.runs.append(run_public_id)


def test_assessment_handler_discovers_org_accounts_before_running_report(
    tmp_path: Path,
) -> None:
    store = SQLiteLeadStore(tmp_path / "leadgen.sqlite3")
    store.initialize()
    run = store.create_validated_run_draft(
        role_arn="arn:aws:iam::123456789012:role/aws-savings-review-readonly",
        external_id="aws-savings-review-example-abc123",
        generated_external_id="aws-savings-review-example-abc123",
        company_name="Example Co",
        contact_name="Jane Doe",
        notes=None,
        validation_payload={"can_proceed": True, "checks": []},
        account_scope=AccountScopeType.ORGANIZATION.value,
        status="QUEUED",
    )
    orchestrator = DummyOrchestrator()
    handler = AssessmentJobHandler(
        store=store,
        orchestrator=orchestrator,  # type: ignore[arg-type]
        account_discovery=lambda _run: [
            DiscoveredAccount(account_id="123456789012", name="Management"),
            DiscoveredAccount(account_id="210987654321", name="Production"),
        ],
    )

    handler.handle(
        AssessmentJobMessage(
            job_public_id="job_123",
            run_public_id=run.public_id,
            account_scope=AccountScopeType.ORGANIZATION,
            attempt=1,
        )
    )

    saved_run = store.get_run_by_public_id(run.public_id)
    assert saved_run is not None
    assert saved_run.validation_payload["organization_discovery_status"] == "completed"
    assert len(saved_run.validation_payload["discovered_accounts"]) == 2
    assert orchestrator.runs == [run.public_id]

