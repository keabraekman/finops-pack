from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from finops_pack.api.app import create_app
from finops_pack.api.settings import WebSettings
from finops_pack.api.validation import ValidationCheckResult, ValidationResult
from finops_pack.domain.models.assessment import AccountScopeType


class DummyValidator:
    def __init__(self, result: ValidationResult) -> None:
        self.result = result

    def validate_submission(
        self,
        *,
        role_arn: str,
        external_id: str,
        confirmed_cost_explorer: bool,
        confirmed_cost_optimization_hub: bool,
        account_scope: AccountScopeType = AccountScopeType.SINGLE_ACCOUNT,
    ) -> ValidationResult:
        return self.result


class DummyEmailService:
    def __init__(self) -> None:
        self.confirmations: list[tuple[str, str]] = []
        self.notifications: list[tuple[str, str]] = []

    def send_lead_confirmation(self, lead: Any, run: Any) -> None:
        self.confirmations.append((str(lead.email), str(run.public_id)))

    def send_internal_submission_notification(self, lead: Any, run: Any) -> None:
        self.notifications.append((str(lead.email), str(run.public_id)))

    def send_lead_report_ready(self, lead: Any, run: Any) -> None:
        return None

    def send_internal_report_ready(self, lead: Any, run: Any) -> None:
        return None

    def send_internal_run_failed(self, lead: Any, run: Any) -> None:
        return None


class DummyOrchestrator:
    def __init__(self) -> None:
        self.started_runs: list[str] = []

    def run_report(self, run_public_id: str) -> None:
        self.started_runs.append(run_public_id)


class DummyJobCoordinator:
    def __init__(self) -> None:
        self.queued_runs: list[tuple[str, AccountScopeType]] = []

    def enqueue_assessment(
        self,
        *,
        run_public_id: str,
        account_scope: AccountScopeType,
    ) -> object:
        self.queued_runs.append((run_public_id, account_scope))
        return object()


def _build_settings(tmp_path: Path) -> WebSettings:
    repo_root = Path(__file__).resolve().parents[2]
    template_root = repo_root / "src" / "finops_pack" / "api" / "templates"
    static_root = repo_root / "src" / "finops_pack" / "api" / "static"
    data_dir = tmp_path / "web-data"
    return WebSettings(
        app_name="AWS Savings Review",
        brand_name="AWS Savings Review",
        base_url="http://testserver",
        repo_root=repo_root,
        data_dir=data_dir,
        database_path=data_dir / "leadgen.sqlite3",
        template_dir=template_root,
        static_dir=static_root,
        operator_trusted_account_id="111122223333",
        default_regions=("us-east-1", "us-west-2", "us-west-1"),
        session_name="finops-pack-web",
        run_collect_ce_resource_daily=True,
        run_rate_limit_safe_mode=True,
        report_cta_label="Book a savings review",
        report_cta_url="https://calendly.com/kea/review",
        smtp_host=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_use_tls=True,
        from_email=None,
        notification_email=None,
        web_host="127.0.0.1",
        web_port=8000,
    )


def test_web_app_renders_landing_and_setup_pages(tmp_path: Path) -> None:
    app = create_app(settings=_build_settings(tmp_path))
    client = TestClient(app)

    landing = client.get("/")
    assert landing.status_code == 200
    assert "AWS Savings Review" in landing.text

    setup = client.get("/setup")
    assert setup.status_code == 200
    assert "Suggested external ID" in setup.text
    assert "111122223333" in setup.text


def test_web_app_validates_intake_then_queues_background_job(tmp_path: Path) -> None:
    validator = DummyValidator(
        ValidationResult(
            can_proceed=True,
            account_id="123456789012",
            account_scope=AccountScopeType.SINGLE_ACCOUNT,
            resolved_regions=("us-east-1", "us-west-2"),
            blocking_issues=(),
            warnings=("Resource-level data is optional.",),
            checks=(
                ValidationCheckResult(
                    label="Assume role",
                    level="pass",
                    detail="Read-only role assumption worked.",
                ),
            ),
        )
    )
    email_service = DummyEmailService()
    orchestrator = DummyOrchestrator()
    job_coordinator = DummyJobCoordinator()
    app = create_app(
        settings=_build_settings(tmp_path),
        validator=cast(Any, validator),
        email_service=cast(Any, email_service),
        orchestrator=cast(Any, orchestrator),
        job_coordinator=cast(Any, job_coordinator),
    )
    client = TestClient(app)

    response = client.post(
        "/intake",
        data={
            "company_name": "Example Co",
            "contact_name": "Jane Doe",
            "email": "lead@example.com",
            "account_scope": "single_account",
            "role_arn": "arn:aws:iam::123456789012:role/aws-savings-review-readonly",
            "external_id": "aws-savings-review-example-abc123",
            "notes": "Please prioritize obvious savings",
            "cost_explorer_enabled": "yes",
            "cost_optimization_hub_enabled": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    status_location = urlparse(response.headers["location"])
    assert status_location.path.startswith("/runs/run_")
    run_public_id = status_location.path.split("/")[2]
    assert email_service.confirmations == [("lead@example.com", run_public_id)]
    assert email_service.notifications == [("lead@example.com", run_public_id)]
    assert orchestrator.started_runs == []
    assert job_coordinator.queued_runs == [(run_public_id, AccountScopeType.SINGLE_ACCOUNT)]

    run = app.state.services.store.get_run_by_public_id(run_public_id)
    assert run is not None
    assert run.status == "QUEUED"
    assert run.lead_email == "lead@example.com"

    history = client.get(f"/history/{run.lead_public_id}")
    assert history.status_code == 200
    assert run_public_id in history.text


def test_web_app_shows_validation_errors_without_advancing(tmp_path: Path) -> None:
    validator = DummyValidator(
        ValidationResult(
            can_proceed=False,
            account_id=None,
            account_scope=AccountScopeType.SINGLE_ACCOUNT,
            resolved_regions=(),
            blocking_issues=("Cost Explorer must be enabled first.",),
            warnings=(),
            checks=(
                ValidationCheckResult(
                    label="Cost Explorer",
                    level="fail",
                    detail="Cost Explorer is not enabled yet.",
                ),
            ),
        )
    )
    app = create_app(settings=_build_settings(tmp_path), validator=cast(Any, validator))
    client = TestClient(app)

    response = client.post(
        "/intake",
        data={
            "company_name": "Example Co",
            "contact_name": "Jane Doe",
            "email": "lead@example.com",
            "account_scope": "single_account",
            "role_arn": "arn:aws:iam::123456789012:role/aws-savings-review-readonly",
            "external_id": "aws-savings-review-example-abc123",
            "cost_explorer_enabled": "yes",
            "cost_optimization_hub_enabled": "yes",
        },
    )

    assert response.status_code == 200
    assert "Cost Explorer must be enabled first." in response.text
    assert "Work email" in response.text


def test_web_app_serves_completed_report_artifacts(tmp_path: Path) -> None:
    app = create_app(settings=_build_settings(tmp_path))
    client = TestClient(app)
    store = app.state.services.store

    run = store.create_validated_run_draft(
        role_arn="arn:aws:iam::123456789012:role/aws-savings-review-readonly",
        external_id="aws-savings-review-example-abc123",
        generated_external_id="aws-savings-review-example-abc123",
        company_name="Example Co",
        contact_name="Jane Doe",
        notes=None,
        validation_payload={"can_proceed": True, "checks": []},
        account_scope="single_account",
    )
    lead = store.create_or_update_lead(
        email="lead@example.com",
        company_name="Example Co",
        contact_name="Jane Doe",
    )
    store.attach_lead_to_run(run_public_id=run.public_id, lead_id=lead.id)
    store.mark_run_queued(run.public_id)

    workspace_dir = tmp_path / "reports" / run.public_id
    report_dir = workspace_dir / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "dashboard.html").write_text(
        "<html><body>Dashboard</body></html>",
        encoding="utf-8",
    )
    (report_dir / "style.css").write_text("body { color: black; }", encoding="utf-8")
    (report_dir / "appendix.html").write_text(
        "<html><body>Appendix</body></html>",
        encoding="utf-8",
    )
    (report_dir / "out").mkdir()
    (report_dir / "out" / "summary.json").write_text("{}", encoding="utf-8")
    (report_dir / "out" / "report-bundle.zip").write_bytes(b"zip")

    store.mark_run_succeeded(
        run_public_id=run.public_id,
        account_id="123456789012",
        process_log="finished",
        workspace_dir=workspace_dir,
        report_dir=report_dir,
        artifact_paths={
            "dashboard": "dashboard.html",
            "appendix": "appendix.html",
            "summary": "out/summary.json",
            "bundle": "out/report-bundle.zip",
        },
    )

    result_page = client.get(f"/runs/{run.public_id}")
    assert result_page.status_code == 200
    assert "Your report is ready." in result_page.text
    assert f"/artifacts/{run.public_id}/dashboard.html" in result_page.text

    dashboard = client.get(f"/artifacts/{run.public_id}/dashboard.html")
    assert dashboard.status_code == 200
    assert "Dashboard" in dashboard.text

    status_response = client.get(f"/runs/{run.public_id}/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "SUCCEEDED"
    assert status_response.json()["result_url"].endswith(f"/runs/{run.public_id}")
