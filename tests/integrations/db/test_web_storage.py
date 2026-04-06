from pathlib import Path

from finops_pack.api.storage import SQLiteLeadStore


def test_sqlite_lead_store_persists_runs_leads_and_artifacts(tmp_path: Path) -> None:
    store = SQLiteLeadStore(tmp_path / "leadgen.sqlite3")
    store.initialize()

    run = store.create_validated_run_draft(
        role_arn="arn:aws:iam::123456789012:role/finops-pack-readonly",
        external_id="kea-finops-example-abc123",
        generated_external_id="kea-finops-example-abc123",
        company_name="Example Co",
        contact_name="Jane Doe",
        notes="First pass",
        validation_payload={"can_proceed": True, "checks": []},
    )
    lead = store.create_or_update_lead(
        email="jane@example.com",
        company_name="Example Co",
        contact_name="Jane Doe",
    )

    workspace_dir = tmp_path / "runs" / run.public_id
    report_dir = workspace_dir / "report"
    report_dir.mkdir(parents=True)

    store.attach_lead_to_run(run_public_id=run.public_id, lead_id=lead.id)
    store.mark_run_queued(run.public_id)
    store.mark_run_running(
        run_public_id=run.public_id,
        workspace_dir=workspace_dir,
        report_dir=report_dir,
    )
    store.mark_run_succeeded(
        run_public_id=run.public_id,
        account_id="123456789012",
        process_log="all good",
        workspace_dir=workspace_dir,
        report_dir=report_dir,
        artifact_paths={
            "dashboard": "dashboard.html",
            "appendix": "appendix.html",
            "summary": "out/summary.json",
        },
    )

    saved_run = store.get_run_by_public_id(run.public_id)
    assert saved_run is not None
    assert saved_run.status == "SUCCEEDED"
    assert saved_run.lead_public_id == lead.public_id
    assert saved_run.account_id == "123456789012"
    assert {artifact.kind for artifact in saved_run.artifacts} == {
        "appendix",
        "dashboard",
        "summary",
    }

    history = store.list_runs_for_lead_public_id(lead.public_id)
    assert len(history) == 1
    assert history[0].public_id == run.public_id
