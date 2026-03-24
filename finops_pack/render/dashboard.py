"""Dashboard rendering helpers for account inventory output."""

from __future__ import annotations

import shutil
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from finops_pack.models import (
    AccessReport,
    AccountMapEntry,
    NormalizedRecommendation,
    SpendBaseline,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
TOP_OPPORTUNITIES_LIMIT = 20

CATEGORY_LABELS = {
    "rightsizing / idle deletion": "Rightsizing / Idle Deletion",
    "commitment (SP/RI)": "Commitment (SP/RI)",
    "storage/network/etc.": "Storage / Network / Etc.",
}
ENVIRONMENT_LABELS = {
    "prod": "Prod",
    "nonprod": "Non-Prod",
    "unknown": "Needs Review",
}


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


def _format_period_display(start: str, end: str) -> str:
    """Format an inclusive date range from CE's exclusive-end time periods."""
    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date() - timedelta(days=1)
    return f"{start_date.isoformat()} to {end_date.isoformat()}"


def _format_month_label(start: str) -> str:
    """Format a month label from a CE period start date."""
    return datetime.fromisoformat(start).strftime("%b %Y")


def _build_spend_baseline_context(
    spend_baseline: SpendBaseline | None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Build dashboard context for the last-30-days CE spend baseline."""
    if spend_baseline is None and error is None:
        return None

    if spend_baseline is None:
        return {"error": error}

    monthly_buckets = [
        {
            "month_label": _format_month_label(bucket.start),
            "window_display": _format_period_display(bucket.start, bucket.end),
            "spend_display": _format_currency(bucket.amount, bucket.unit),
        }
        for bucket in spend_baseline.monthly_buckets
    ]

    return {
        "window_display": _format_period_display(
            spend_baseline.window_start,
            spend_baseline.window_end,
        ),
        "window_days": spend_baseline.window_days,
        "total_spend_display": _format_currency(
            spend_baseline.total_amount,
            spend_baseline.unit,
        ),
        "average_daily_display": _format_currency(
            spend_baseline.average_daily_amount,
            spend_baseline.unit,
        ),
        "bucket_count": len(spend_baseline.monthly_buckets),
        "monthly_buckets": monthly_buckets,
        "error": error,
    }


def _build_coh_context(
    coh_summary: dict[str, Any] | None,
    account_map: Sequence[AccountMapEntry],
    recommendations: Sequence[NormalizedRecommendation],
) -> dict[str, Any] | None:
    """Build dashboard context for COH savings notes and warnings."""
    if coh_summary is None and not recommendations:
        return None

    estimated_monthly_savings = None
    currency_code = None
    if coh_summary is not None:
        estimated_monthly_savings_value = coh_summary.get("estimatedTotalDedupedSavings")
        if isinstance(estimated_monthly_savings_value, (int, float)):
            estimated_monthly_savings = float(estimated_monthly_savings_value)
        if isinstance(coh_summary.get("currencyCode"), str):
            currency_code = coh_summary["currencyCode"]
    if currency_code is None:
        for recommendation in recommendations:
            if recommendation.currency_code:
                currency_code = recommendation.currency_code
                break

    account_lookup = {entry.account_id: entry for entry in account_map}
    recommendation_rows = _build_recommendation_rows(
        recommendations,
        account_lookup=account_lookup,
        currency_code=currency_code,
    )

    return {
        "estimated_monthly_savings": estimated_monthly_savings,
        "currency_code": currency_code,
        "recommendation_count": len(recommendations),
        "top_opportunities": recommendation_rows[:TOP_OPPORTUNITIES_LIMIT],
        "savings_by_category": _build_savings_by_category(
            recommendation_rows,
            currency_code=currency_code,
        ),
        "savings_by_account": _build_savings_by_account(
            recommendation_rows,
            currency_code=currency_code,
        ),
        "savings_by_environment": _build_savings_by_environment(
            recommendation_rows,
            currency_code=currency_code,
        ),
        "notes": [
            (
                "Estimated monthly savings from AWS Cost Optimization Hub use the service's "
                "730-hour monthly normalization."
            ),
            (
                "Recommendation IDs can expire after about 24 hours, so refresh the latest "
                "snapshot before sharing or acting on a stored recommendationId."
            ),
        ],
    }


def _format_currency(amount: float, currency_code: str | None) -> str:
    """Format savings values for dashboard display."""
    if currency_code == "USD":
        return f"${amount:,.2f}"
    if currency_code:
        return f"{amount:,.2f} {currency_code}"
    return f"{amount:,.2f}"


def _build_recommendation_rows(
    recommendations: Sequence[NormalizedRecommendation],
    *,
    account_lookup: dict[str, AccountMapEntry],
    currency_code: str | None,
) -> list[dict[str, Any]]:
    """Flatten normalized recommendations into dashboard-friendly rows."""
    rows: list[dict[str, Any]] = []
    for recommendation in recommendations:
        if recommendation.estimated_monthly_savings is None:
            continue

        account_entry = (
            account_lookup.get(recommendation.account_id)
            if recommendation.account_id is not None
            else None
        )
        environment = account_entry.environment if account_entry is not None else "unknown"
        account_name = (
            account_entry.name
            if account_entry is not None
            else recommendation.account_id or "Unknown account"
        )
        title = (
            recommendation.recommendation.title
            if recommendation.recommendation is not None
            else recommendation.action_type or "Cost Optimization Hub recommendation"
        )
        summary = (
            recommendation.recommendation.summary
            if recommendation.recommendation is not None
            else recommendation.current_resource_summary or recommendation.resource_id
        )
        rows.append(
            {
                "recommendation_id": recommendation.recommendation_id,
                "title": title,
                "summary": summary,
                "category": recommendation.category,
                "category_label": CATEGORY_LABELS.get(
                    recommendation.category,
                    recommendation.category,
                ),
                "account_id": recommendation.account_id or "Unknown",
                "account_name": account_name,
                "environment": environment,
                "environment_label": ENVIRONMENT_LABELS[environment],
                "region": recommendation.region or "N/A",
                "estimated_monthly_savings": recommendation.estimated_monthly_savings,
                "monthly_savings_display": _format_currency(
                    recommendation.estimated_monthly_savings,
                    currency_code,
                ),
            }
        )

    rows.sort(
        key=lambda item: (
            -item["estimated_monthly_savings"],
            item["account_name"].lower(),
            item["recommendation_id"],
        )
    )
    return rows


def _build_savings_by_category(
    recommendation_rows: Sequence[dict[str, Any]],
    *,
    currency_code: str | None,
) -> list[dict[str, Any]]:
    """Aggregate opportunity savings by recommendation category."""
    category_totals: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {"opportunity_count": 0, "estimated_monthly_savings": 0.0, "label": ""}
    )

    for row in recommendation_rows:
        bucket = category_totals[row["category"]]
        bucket["label"] = row["category_label"]
        bucket["opportunity_count"] += 1
        bucket["estimated_monthly_savings"] += row["estimated_monthly_savings"]

    aggregated_rows = list(category_totals.values())
    aggregated_rows.sort(
        key=lambda item: (-item["estimated_monthly_savings"], item["label"].lower())
    )
    for row in aggregated_rows:
        row["monthly_savings_display"] = _format_currency(
            row["estimated_monthly_savings"],
            currency_code,
        )
    return aggregated_rows


def _build_savings_by_account(
    recommendation_rows: Sequence[dict[str, Any]],
    *,
    currency_code: str | None,
) -> list[dict[str, Any]]:
    """Aggregate opportunity savings by AWS account."""
    account_totals: defaultdict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "account_id": "",
            "account_name": "",
            "environment": "unknown",
            "environment_label": ENVIRONMENT_LABELS["unknown"],
            "opportunity_count": 0,
            "estimated_monthly_savings": 0.0,
        }
    )

    for row in recommendation_rows:
        bucket = account_totals[row["account_id"]]
        bucket["account_id"] = row["account_id"]
        bucket["account_name"] = row["account_name"]
        bucket["environment"] = row["environment"]
        bucket["environment_label"] = row["environment_label"]
        bucket["opportunity_count"] += 1
        bucket["estimated_monthly_savings"] += row["estimated_monthly_savings"]

    aggregated_rows = list(account_totals.values())
    aggregated_rows.sort(
        key=lambda item: (
            -item["estimated_monthly_savings"],
            item["account_name"].lower(),
            item["account_id"],
        )
    )
    for row in aggregated_rows:
        row["monthly_savings_display"] = _format_currency(
            row["estimated_monthly_savings"],
            currency_code,
        )
    return aggregated_rows


def _build_savings_by_environment(
    recommendation_rows: Sequence[dict[str, Any]],
    *,
    currency_code: str | None,
) -> list[dict[str, Any]]:
    """Split monthly savings by prod/non-prod classification."""
    environment_totals = {
        environment: {
            "environment": environment,
            "label": ENVIRONMENT_LABELS[environment],
            "opportunity_count": 0,
            "estimated_monthly_savings": 0.0,
        }
        for environment in ENVIRONMENT_LABELS
    }

    for row in recommendation_rows:
        bucket = environment_totals[row["environment"]]
        bucket["opportunity_count"] += 1
        bucket["estimated_monthly_savings"] += row["estimated_monthly_savings"]

    total_savings = sum(item["estimated_monthly_savings"] for item in environment_totals.values())
    aggregated_rows = [
        environment_totals["prod"],
        environment_totals["nonprod"],
        environment_totals["unknown"],
    ]
    if (
        aggregated_rows[-1]["opportunity_count"] == 0
        and aggregated_rows[-1]["estimated_monthly_savings"] == 0
    ):
        aggregated_rows = aggregated_rows[:-1]

    for row in aggregated_rows:
        row["monthly_savings_display"] = _format_currency(
            row["estimated_monthly_savings"],
            currency_code,
        )
        row["share_display"] = (
            f"{(row['estimated_monthly_savings'] / total_savings) * 100:.1f}%"
            if total_savings > 0
            else "0.0%"
        )
    return aggregated_rows


def render_dashboard_html(
    account_map: list[AccountMapEntry],
    *,
    title: str = "FinOps Pack Dashboard",
    subtitle: str = "AWS Organizations account inventory and environment classification.",
    generated_at: str | None = None,
    account_id: str | None = "AWS Organizations",
    region: str = "us-east-1",
    access_report: AccessReport | None = None,
    spend_baseline: SpendBaseline | None = None,
    spend_baseline_error: str | None = None,
    coh_summary: dict[str, Any] | None = None,
    recommendations: Sequence[NormalizedRecommendation] | None = None,
) -> str:
    """Render the dashboard HTML for account inventory."""
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("report.html.j2")
    grouped = _group_accounts(account_map)
    recommendation_list = list(recommendations or [])

    return template.render(
        title=title,
        subtitle=subtitle,
        generated_at=generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        account_id=account_id,
        region=region,
        executive_summary=_build_executive_summary(account_map, access_report),
        spend_baseline_context=_build_spend_baseline_context(
            spend_baseline,
            spend_baseline_error,
        ),
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
        coh_context=_build_coh_context(coh_summary, account_map, recommendation_list),
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
    spend_baseline: SpendBaseline | None = None,
    spend_baseline_error: str | None = None,
    coh_summary: dict[str, Any] | None = None,
    recommendations: Sequence[NormalizedRecommendation] | None = None,
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
            spend_baseline=spend_baseline,
            spend_baseline_error=spend_baseline_error,
            coh_summary=coh_summary,
            recommendations=recommendations,
        ),
        encoding="utf-8",
    )

    stylesheet_path = destination_path.parent / "style.css"
    shutil.copyfile(STATIC_DIR / "style.css", stylesheet_path)
    return destination_path
