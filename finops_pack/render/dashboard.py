"""Dashboard rendering helpers for account inventory output."""

from __future__ import annotations

import shutil
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from os.path import relpath
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from finops_pack.models import (
    AccessReport,
    AccountMapEntry,
    NormalizedRecommendation,
    SpendBaseline,
)
from finops_pack.prerequisites import (
    CE_RESOURCE_LEVEL_DOC_NOTE,
    CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE,
    COH_IMPORT_NOTE,
    OPTIONAL_CE_FALLBACK_NOTE,
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


def _resolve_coh_total_display(coh_context: dict[str, Any] | None) -> str | None:
    """Choose a COH total savings display for executive summary cards."""
    if coh_context is None:
        return None

    estimated_monthly_savings = coh_context.get("estimated_monthly_savings")
    if isinstance(estimated_monthly_savings, (int, float)):
        return _format_currency(
            float(estimated_monthly_savings),
            coh_context.get("currency_code"),
        )

    savings_by_category = coh_context.get("savings_by_category", [])
    if not isinstance(savings_by_category, list):
        return None

    total = sum(
        float(category["estimated_monthly_savings"])
        for category in savings_by_category
        if isinstance(category, dict)
        and isinstance(category.get("estimated_monthly_savings"), (int, float))
    )
    if total <= 0:
        return None

    return _format_currency(total, coh_context.get("currency_code"))


def _build_executive_summary_cards(
    account_map: Sequence[AccountMapEntry],
    *,
    spend_baseline_context: dict[str, Any] | None,
    coh_context: dict[str, Any] | None,
    schedule_context: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Build top-level executive summary cards for the dashboard."""
    grouped = _group_accounts(list(account_map))
    cards = [
        {
            "label": "Accounts Classified",
            "value": str(len(account_map)),
            "meta": (
                f"{len(grouped['prod'])} prod · {len(grouped['nonprod'])} non-prod · "
                f"{len(grouped['unknown'])} review"
            ),
        }
    ]

    if spend_baseline_context is not None:
        if spend_baseline_context.get("error") and not spend_baseline_context.get(
            "monthly_buckets"
        ):
            cards.append(
                {
                    "label": "Spend Baseline",
                    "value": "Unavailable",
                    "meta": str(spend_baseline_context["error"]),
                }
            )
        else:
            cards.append(
                {
                    "label": "Last 30d Spend",
                    "value": str(spend_baseline_context["total_spend_display"]),
                    "meta": str(spend_baseline_context["window_display"]),
                }
            )

    coh_total_display = _resolve_coh_total_display(coh_context)
    if coh_context is not None and coh_total_display is not None:
        cards.append(
            {
                "label": "COH Savings",
                "value": f"{coh_total_display} / month",
                "meta": f"{coh_context['recommendation_count']} normalized recommendations",
            }
        )

    if schedule_context is not None:
        cards.append(
            {
                "label": "Schedule Savings",
                "value": f"{schedule_context['total_likely_display']} / day",
                "meta": (
                    f"{schedule_context['recommendation_count']} non-prod candidates · "
                    f"range {schedule_context['total_low_display']} to "
                    f"{schedule_context['total_high_display']} / day"
                ),
            }
        )

    return cards


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


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _render_schedule_estimation_status(status: str | None) -> str:
    if not status:
        return "Unknown"
    return status[:1].upper() + status[1:]


def _build_schedule_context(
    schedule_recommendations: Sequence[dict[str, Any]] | None,
    account_map: Sequence[AccountMapEntry],
) -> dict[str, Any] | None:
    """Build dashboard context for non-prod EC2 stop-schedule candidates."""
    if not schedule_recommendations:
        return None

    account_lookup = {entry.account_id: entry for entry in account_map}
    rows: list[dict[str, Any]] = []
    total_low = 0.0
    total_likely = 0.0
    total_high = 0.0
    estimated_count = 0
    needs_opt_in_count = 0

    for raw_row in schedule_recommendations:
        if not isinstance(raw_row, dict):
            continue
        account_id = raw_row.get("accountId")
        if not isinstance(account_id, str) or not account_id:
            continue

        account_entry = account_lookup.get(account_id)
        if account_entry is None or account_entry.environment != "nonprod":
            continue

        likely_savings = _coerce_float(raw_row.get("estimatedOffHoursDailySavings"))
        low_savings = _coerce_float(raw_row.get("estimatedOffHoursDailySavingsLow"))
        high_savings = _coerce_float(raw_row.get("estimatedOffHoursDailySavingsHigh"))
        if likely_savings is not None and low_savings is None:
            low_savings = round(likely_savings * 0.7, 2)
        if likely_savings is not None and high_savings is None:
            high_savings = round(likely_savings, 2)

        raw_estimation_status = raw_row.get("estimationStatus")
        estimation_status = raw_estimation_status if isinstance(raw_estimation_status, str) else ""
        if likely_savings is not None:
            estimated_count += 1
            total_low += low_savings or 0.0
            total_likely += likely_savings
            total_high += high_savings or 0.0
        elif estimation_status == "needs CE resource-level opt-in":
            needs_opt_in_count += 1

        off_hours_ratio = _coerce_float(raw_row.get("offHoursRatio"))
        rows.append(
            {
                "account_id": account_id,
                "account_name": account_entry.name,
                "instance_id": raw_row.get("instanceId", ""),
                "instance_arn": raw_row.get("instanceArn", ""),
                "instance_name": raw_row.get("name", ""),
                "instance_type": raw_row.get("instanceType", ""),
                "platform": raw_row.get("platform", ""),
                "region": raw_row.get("region", ""),
                "off_hours_ratio_display": (
                    f"{off_hours_ratio * 100:.1f}%" if off_hours_ratio is not None else "n/a"
                ),
                "resource_cost_display": raw_row.get("Resource cost (14d)", ""),
                "low_display": (
                    _format_currency(low_savings, "USD") if low_savings is not None else "TBD"
                ),
                "likely_display": (
                    _format_currency(likely_savings, "USD") if likely_savings is not None else "TBD"
                ),
                "high_display": (
                    _format_currency(high_savings, "USD") if high_savings is not None else "TBD"
                ),
                "likely_savings": likely_savings,
                "estimation_status": _render_schedule_estimation_status(estimation_status),
                "estimation_reason": raw_row.get("estimationReason", ""),
            }
        )

    if not rows:
        return None

    rows.sort(
        key=lambda item: (
            -(item["likely_savings"] if item["likely_savings"] is not None else -1.0),
            item["account_name"].lower(),
            item["instance_id"],
        )
    )

    return {
        "recommendation_count": len(rows),
        "estimated_count": estimated_count,
        "needs_opt_in_count": needs_opt_in_count,
        "total_low": total_low,
        "total_likely": total_likely,
        "total_high": total_high,
        "total_low_display": _format_currency(total_low, "USD"),
        "total_likely_display": _format_currency(total_likely, "USD"),
        "total_high_display": _format_currency(total_high, "USD"),
        "rows": rows,
        "notes": [
            "Non-prod accounts only.",
            "Bands use low = likely x 0.7 and high = likely x 1.0.",
        ],
    }


def _format_enabled_label(enabled: bool | None) -> str:
    if enabled is True:
        return "Yes"
    if enabled is False:
        return "No"
    return "Unknown"


def _build_prerequisites_context(access_report: AccessReport | None) -> dict[str, Any] | None:
    """Build dashboard context for prerequisite readiness checks."""
    if access_report is None or not access_report.checks:
        return None

    items: list[dict[str, Any]] = []
    for check in access_report.checks:
        doc_note = None
        guidance = None
        if check.check_id == "resource_level_costs" and check.enabled is False:
            doc_note = CE_RESOURCE_LEVEL_DOC_NOTE
            guidance = CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE
        items.append(
            {
                "label": check.label,
                "status": check.status,
                "enabled_label": _format_enabled_label(check.enabled),
                "reason": check.reason,
                "checked_in_region": check.checked_in_region,
                "doc_note": doc_note,
                "guidance": guidance,
            }
        )

    return {"items": items}


def _build_remediation_context(
    access_report: AccessReport | None,
    *,
    has_coh_recommendations: bool,
) -> dict[str, Any] | None:
    """Build an actionable remediation checklist from degraded prerequisites."""
    if access_report is None:
        return None

    steps: list[dict[str, str]] = []
    seen_titles: set[str] = set()

    def add_step(title: str, detail: str) -> None:
        if title in seen_titles:
            return
        seen_titles.add(title)
        steps.append({"title": title, "detail": detail})

    check_map = {check.check_id: check for check in access_report.checks}
    coh_check = check_map.get("cost_optimization_hub")
    cost_explorer_check = check_map.get("cost_explorer")
    resource_level_check = check_map.get("resource_level_costs")

    if coh_check is not None:
        if coh_check.enabled is False:
            add_step(
                "Enable Cost Optimization Hub",
                (
                    "Rerun with --enable-coh or enroll the account manually so COH can remain "
                    f"the primary recommendation source. {COH_IMPORT_NOTE}"
                ),
            )
        elif coh_check.enabled is None and "denied" in coh_check.reason.lower():
            add_step(
                "Grant Cost Optimization Hub read access",
                (
                    "Add cost-optimization-hub:ListEnrollmentStatuses, "
                    "cost-optimization-hub:ListRecommendationSummaries, and "
                    "cost-optimization-hub:ListRecommendations to the target role."
                ),
            )

    if cost_explorer_check is not None:
        if cost_explorer_check.enabled is False:
            add_step(
                "Wait for Cost Explorer baseline data",
                (
                    "Cost Explorer did not have billing data for a recent completed "
                    "day yet. Retry after billing data populates."
                ),
            )
        elif cost_explorer_check.enabled is None and "denied" in cost_explorer_check.reason.lower():
            add_step(
                "Grant Cost Explorer baseline permissions",
                "Add ce:GetCostAndUsage to the target role so baseline spend collection can run.",
            )

    if resource_level_check is not None:
        if resource_level_check.enabled is False:
            add_step(
                "Enable Cost Explorer resource-level daily data",
                f"{CE_RESOURCE_LEVEL_DOC_NOTE} {CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE}",
            )
        elif (
            resource_level_check.enabled is None and "denied" in resource_level_check.reason.lower()
        ):
            add_step(
                "Grant resource-level Cost Explorer permissions",
                (
                    "Add ce:GetCostAndUsageWithResources to the target role. "
                    f"{CE_RESOURCE_LEVEL_DOC_NOTE}"
                ),
            )

    if coh_check is not None and (coh_check.enabled is not True or not has_coh_recommendations):
        add_step("Optional fallback modules", OPTIONAL_CE_FALLBACK_NOTE)

    if not steps:
        steps.append(
            {
                "title": "No immediate remediation detected",
                "detail": "All tracked prerequisites look active for this run.",
            }
        )

    return {"steps": steps}


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


def _build_savings_by_lever_context(
    coh_context: dict[str, Any] | None,
    schedule_context: dict[str, Any] | None,
) -> list[dict[str, str | int]] | None:
    """Combine schedule and COH savings into a single dashboard table."""
    rows: list[dict[str, str | int]] = []

    if schedule_context is not None:
        rows.append(
            {
                "lever": "Non-Prod EC2 Schedule",
                "source": "Schedule",
                "opportunity_count": schedule_context["recommendation_count"],
                "savings_display": f"{schedule_context['total_likely_display']} / day",
                "detail": (
                    f"Range {schedule_context['total_low_display']} to "
                    f"{schedule_context['total_high_display']} / day"
                ),
            }
        )

    if coh_context is not None:
        for category in coh_context["savings_by_category"]:
            rows.append(
                {
                    "lever": str(category["label"]),
                    "source": "COH",
                    "opportunity_count": int(category["opportunity_count"]),
                    "savings_display": f"{category['monthly_savings_display']} / month",
                    "detail": "AWS Cost Optimization Hub normalized monthly estimate",
                }
            )

    return rows or None


def build_dashboard_download_links(
    dashboard_path: str | Path,
    download_targets: Sequence[tuple[str, str, str | Path]],
) -> list[dict[str, str]]:
    """Build dashboard download links with paths relative to the dashboard location."""
    dashboard_output = Path(dashboard_path)
    links: list[dict[str, str]] = []

    for label, description, target in download_targets:
        target_path = Path(target)
        links.append(
            {
                "label": label,
                "description": description,
                "filename": target_path.name,
                "format": target_path.suffix.lstrip(".").upper() or "FILE",
                "href": Path(
                    relpath(
                        target_path,
                        start=dashboard_output.parent,
                    )
                ).as_posix(),
            }
        )

    return links


def render_dashboard_html(
    account_map: list[AccountMapEntry],
    *,
    title: str = "FinOps Pack Dashboard",
    subtitle: str = "AWS Organizations account inventory and environment classification.",
    stylesheet_path: str | None = None,
    generated_at: str | None = None,
    account_id: str | None = "AWS Organizations",
    region: str = "us-east-1",
    access_report: AccessReport | None = None,
    spend_baseline: SpendBaseline | None = None,
    spend_baseline_error: str | None = None,
    coh_summary: dict[str, Any] | None = None,
    recommendations: Sequence[NormalizedRecommendation] | None = None,
    schedule_recommendations: Sequence[dict[str, Any]] | None = None,
    download_links: Sequence[dict[str, str]] | None = None,
) -> str:
    """Render the dashboard HTML for account inventory."""
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = environment.get_template("report.html.j2")
    grouped = _group_accounts(account_map)
    recommendation_list = list(recommendations or [])
    spend_baseline_context = _build_spend_baseline_context(
        spend_baseline,
        spend_baseline_error,
    )
    coh_context = _build_coh_context(coh_summary, account_map, recommendation_list)
    schedule_context = _build_schedule_context(schedule_recommendations, account_map)
    prerequisites_context = _build_prerequisites_context(access_report)
    remediation_context = _build_remediation_context(
        access_report,
        has_coh_recommendations=bool(recommendation_list),
    )

    return template.render(
        title=title,
        subtitle=subtitle,
        stylesheet_path=stylesheet_path,
        generated_at=generated_at or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        account_id=account_id,
        region=region,
        executive_summary=_build_executive_summary(account_map, access_report),
        executive_summary_cards=_build_executive_summary_cards(
            account_map,
            spend_baseline_context=spend_baseline_context,
            coh_context=coh_context,
            schedule_context=schedule_context,
        ),
        savings_by_lever=_build_savings_by_lever_context(
            coh_context,
            schedule_context,
        ),
        spend_baseline_context=spend_baseline_context,
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
        coh_context=coh_context,
        schedule_context=schedule_context,
        prerequisites_context=prerequisites_context,
        remediation_context=remediation_context,
        download_links=list(download_links or []),
        show_findings_section=False,
        show_recommendations_section=False,
    )


def write_dashboard(
    account_map: list[AccountMapEntry],
    destination: str | Path,
    *,
    stylesheet_path: str | None = None,
    account_id: str | None = "AWS Organizations",
    region: str = "us-east-1",
    access_report: AccessReport | None = None,
    spend_baseline: SpendBaseline | None = None,
    spend_baseline_error: str | None = None,
    coh_summary: dict[str, Any] | None = None,
    recommendations: Sequence[NormalizedRecommendation] | None = None,
    schedule_recommendations: Sequence[dict[str, Any]] | None = None,
    download_links: Sequence[dict[str, str]] | None = None,
) -> Path:
    """Write the account dashboard HTML and its stylesheet."""
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        render_dashboard_html(
            account_map,
            stylesheet_path=stylesheet_path,
            account_id=account_id,
            region=region,
            access_report=access_report,
            spend_baseline=spend_baseline,
            spend_baseline_error=spend_baseline_error,
            coh_summary=coh_summary,
            recommendations=recommendations,
            schedule_recommendations=schedule_recommendations,
            download_links=download_links,
        ),
        encoding="utf-8",
    )

    stylesheet_path = destination_path.parent / "style.css"
    shutil.copyfile(STATIC_DIR / "style.css", stylesheet_path)
    return destination_path
