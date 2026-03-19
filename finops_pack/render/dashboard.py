"""Dashboard rendering helpers for account inventory output."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from finops_pack.models import AccessReport, AccountMapEntry

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _group_accounts(account_map: list[AccountMapEntry]) -> dict[str, list[AccountMapEntry]]:
    """Group account map entries by environment for rendering."""
    groups: dict[str, list[AccountMapEntry]] = {
        "prod": [],
        "nonprod": [],
        "unknown": [],
    }
    for entry in sorted(account_map, key=lambda item: (item.name.lower(), item.account_id)):
        groups[entry.environment].append(entry)
    return groups


def _build_executive_summary(
    account_map: list[AccountMapEntry],
    access_report: AccessReport | None = None,
) -> str:
    """Create a short inventory summary for the dashboard."""
    grouped = _group_accounts(account_map)
    summary = (
        f"Classified {len(account_map)} AWS accounts: "
        f"{len(grouped['prod'])} prod, "
        f"{len(grouped['nonprod'])} non-prod, "
        f"{len(grouped['unknown'])} needing review."
    )
    if access_report is None or not access_report.modules:
        return summary

    degraded_modules = [module for module in access_report.modules if module.status == "DEGRADED"]
    if degraded_modules:
        summary += f" {len(degraded_modules)} billing module(s) are degraded."
    return summary


def render_dashboard_html(
    account_map: list[AccountMapEntry],
    *,
    title: str = "FinOps Pack Dashboard",
    subtitle: str = "AWS Organizations account inventory and environment classification.",
    generated_at: str | None = None,
    account_id: str | None = "AWS Organizations",
    region: str = "us-east-1",
    access_report: AccessReport | None = None,
) -> str:
    """Render the dashboard HTML for account inventory."""
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("report.html.j2")
    grouped = _group_accounts(account_map)

    return template.render(
        title=title,
        subtitle=subtitle,
        generated_at=generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        account_id=account_id,
        region=region,
        executive_summary=_build_executive_summary(account_map, access_report),
        account_map={
            "entries": account_map,
            "prod": grouped["prod"],
            "nonprod": grouped["nonprod"],
            "unknown": grouped["unknown"],
            "total": len(account_map),
        },
        findings=[],
        recommendations=[],
        access_report=access_report,
        show_findings_section=False,
        show_recommendations_section=False,
    )


def write_dashboard(
    account_map: list[AccountMapEntry],
    destination: str | Path,
    *,
    account_id: str | None = "AWS Organizations",
    region: str = "us-east-1",
    access_report: AccessReport | None = None,
) -> Path:
    """Write the account dashboard HTML and its stylesheet."""
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        render_dashboard_html(
            account_map,
            account_id=account_id,
            region=region,
            access_report=access_report,
        ),
        encoding="utf-8",
    )

    stylesheet_path = destination_path.parent / "style.css"
    shutil.copyfile(STATIC_DIR / "style.css", stylesheet_path)
    return destination_path
