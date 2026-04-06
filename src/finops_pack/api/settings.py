"""Environment-driven settings for the AWS Savings Review web app."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw.strip())


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(values) if values else default


def _find_repo_root(start: Path) -> Path:
    """Find the repository root from a package path."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return start.parents[2]


@dataclass(frozen=True)
class WebSettings:
    """Runtime settings for the FastAPI wrapper app."""

    app_name: str
    brand_name: str
    base_url: str
    repo_root: Path
    data_dir: Path
    database_path: Path
    template_dir: Path
    static_dir: Path
    operator_trusted_account_id: str
    default_regions: tuple[str, ...]
    session_name: str
    run_collect_ce_resource_daily: bool
    run_rate_limit_safe_mode: bool
    report_cta_label: str
    report_cta_url: str
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_tls: bool
    from_email: str | None
    notification_email: str | None
    web_host: str
    web_port: int

    @property
    def runs_dir(self) -> Path:
        """Return the directory where per-run artifacts are stored."""
        return self.data_dir / "runs"


def load_web_settings() -> WebSettings:
    """Load web settings from environment variables."""
    package_root = Path(__file__).resolve().parent
    repo_root = _find_repo_root(package_root)
    data_dir = Path(os.getenv("FINOPS_WEB_DATA_DIR", str(repo_root / "web_data"))).expanduser()
    database_path = Path(
        os.getenv("FINOPS_WEB_DB_PATH", str(data_dir / "leadgen.sqlite3"))
    ).expanduser()

    return WebSettings(
        app_name=os.getenv("FINOPS_WEB_APP_NAME", "AWS Savings Review"),
        brand_name=os.getenv("FINOPS_WEB_BRAND_NAME", "AWS Savings Review"),
        base_url=os.getenv("FINOPS_WEB_BASE_URL", "http://localhost:8000").rstrip("/"),
        repo_root=repo_root,
        data_dir=data_dir,
        database_path=database_path,
        template_dir=package_root / "templates",
        static_dir=package_root / "static",
        operator_trusted_account_id=os.getenv(
            "FINOPS_WEB_TRUSTED_AWS_ACCOUNT_ID",
            os.getenv("KEA_TRUSTED_AWS_ACCOUNT_ID", "111122223333"),
        ),
        default_regions=_env_list(
            "FINOPS_WEB_DEFAULT_REGIONS",
            ("us-east-1", "us-west-2", "us-west-1"),
        ),
        session_name=os.getenv("FINOPS_WEB_SESSION_NAME", "finops-pack-web"),
        run_collect_ce_resource_daily=_env_bool(
            "FINOPS_WEB_COLLECT_CE_RESOURCE_DAILY",
            True,
        ),
        run_rate_limit_safe_mode=_env_bool("FINOPS_WEB_RATE_LIMIT_SAFE_MODE", True),
        report_cta_label=os.getenv(
            "FINOPS_WEB_REPORT_CTA_LABEL",
            "Book an implementation review",
        ),
        report_cta_url=os.getenv(
            "FINOPS_WEB_REPORT_CTA_URL",
            "https://calendly.com/replace-me/aws-savings-review",
        ),
        smtp_host=os.getenv("FINOPS_WEB_SMTP_HOST"),
        smtp_port=_env_int("FINOPS_WEB_SMTP_PORT", 587),
        smtp_username=os.getenv("FINOPS_WEB_SMTP_USERNAME"),
        smtp_password=os.getenv("FINOPS_WEB_SMTP_PASSWORD"),
        smtp_use_tls=_env_bool("FINOPS_WEB_SMTP_USE_TLS", True),
        from_email=os.getenv("FINOPS_WEB_FROM_EMAIL"),
        notification_email=os.getenv("FINOPS_WEB_NOTIFY_EMAIL"),
        web_host=os.getenv("FINOPS_WEB_HOST", "0.0.0.0"),
        web_port=_env_int("FINOPS_WEB_PORT", 8000),
    )
