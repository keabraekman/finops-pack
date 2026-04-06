from pathlib import Path

from finops_pack.api.emailer import EmailService
from finops_pack.api.runner import RunOrchestrator
from finops_pack.api.settings import WebSettings
from finops_pack.api.storage import SQLiteLeadStore
from finops_pack.api.validation import ValidationResult
from finops_pack.domain.models.assessment import AccountScopeType


def _build_settings(tmp_path: Path) -> WebSettings:
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "src" / "finops_pack" / "api"
    data_dir = tmp_path / "web-data"
    return WebSettings(
        app_name="AWS Savings Review",
        brand_name="AWS Savings Review",
        base_url="http://testserver",
        repo_root=repo_root,
        data_dir=data_dir,
        database_path=data_dir / "leadgen.sqlite3",
        template_dir=package_root / "templates",
        static_dir=package_root / "static",
        operator_trusted_account_id="111122223333",
        default_regions=("us-east-1", "us-west-2", "us-west-1"),
        session_name="finops-pack-web",
        run_collect_ce_resource_daily=True,
        run_rate_limit_safe_mode=True,
        report_cta_label="Book an implementation review",
        report_cta_url="https://example.com/review",
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


def test_run_orchestrator_uses_resolved_regions_from_validation_payload(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    store = SQLiteLeadStore(settings.database_path)
    store.initialize()
    orchestrator = RunOrchestrator(settings, store, EmailService(settings))

    run = store.create_validated_run_draft(
        role_arn="arn:aws:iam::123456789012:role/aws-savings-review-readonly",
        external_id="aws-savings-review-example-abc123",
        generated_external_id="aws-savings-review-example-abc123",
        company_name="Example Co",
        contact_name="Jane Doe",
        notes=None,
        validation_payload=ValidationResult(
            can_proceed=True,
            account_id="123456789012",
            account_scope=AccountScopeType.SINGLE_ACCOUNT,
            resolved_regions=("us-east-1", "eu-west-1", "ap-southeast-2"),
            blocking_issues=(),
            warnings=(),
            checks=(),
        ).to_payload(),
    )

    command = orchestrator._build_command(
        run_public_id=run.public_id,
        report_dir=tmp_path / "report",
        run=run,
    )

    region_index = command.index("--regions")
    assert command[region_index + 1 : region_index + 4] == [
        "us-east-1",
        "eu-west-1",
        "ap-southeast-2",
    ]
