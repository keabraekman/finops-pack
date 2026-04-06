"""Fixture loading helpers for the `finops-pack demo` command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finops_pack.analysis.account_classification import classify_accounts
from finops_pack.domain.models import (
    AccessReport,
    AccountMapEntry,
    ActionOpportunity,
    RegionCoverage,
    SpendBaseline,
)
from finops_pack.integrations.aws.collectors.organizations import load_account_records
from finops_pack.orchestration.config import AppConfig, BusinessHours, ScheduleConfig
from finops_pack.reporting.export_schema import validate_export_recommendations_payload


@dataclass
class DemoFixtureBundle:
    """Structured fixture inputs used to render the local demo dashboard."""

    account_map: list[AccountMapEntry]
    access_report: AccessReport
    generated_at: str
    client_id: str | None
    run_id: str
    region: str
    schedule: ScheduleConfig
    account_label: str
    summary_payload: dict[str, Any] | None
    comparison_context: dict[str, Any] | None
    spend_baseline: SpendBaseline | None
    spend_baseline_error: str | None
    spend_baseline_snapshot: dict[str, Any] | None
    coh_summary: dict[str, Any] | None
    recommendations: list[Any]
    schedule_recommendations: list[dict[str, Any]]
    native_actions: list[ActionOpportunity]


def _load_optional_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_account_map(
    fixture_dir: Path,
    *,
    prod_account_ids: list[str],
    nonprod_account_ids: list[str],
) -> list[AccountMapEntry]:
    path = fixture_dir / "accounts.json"
    raw = _load_optional_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a top-level array.")

    if raw and all(isinstance(item, dict) and "environment" in item for item in raw):
        return [AccountMapEntry(**item) for item in raw]

    account_records = load_account_records(path)
    return classify_accounts(
        account_records,
        prod_account_ids=prod_account_ids,
        nonprod_account_ids=nonprod_account_ids,
    )


def _load_access_report(
    fixture_dir: Path,
    *,
    fallback_region_coverage: RegionCoverage,
    fallback_account_label: str,
) -> AccessReport:
    raw = _load_optional_json(fixture_dir / "access_report.json")
    if not isinstance(raw, dict):
        return AccessReport(
            account_id=fallback_account_label,
            region_coverage=fallback_region_coverage,
        )

    if raw.get("region_coverage") is None:
        raw["region_coverage"] = {
            "strategy": fallback_region_coverage.strategy,
            "primary_region": fallback_region_coverage.primary_region,
            "regions": fallback_region_coverage.regions,
        }
    if raw.get("account_id") is None:
        raw["account_id"] = fallback_account_label
    return AccessReport(**raw)


def parse_spend_baseline_snapshot(snapshot: dict[str, Any] | None) -> SpendBaseline | None:
    """Parse a persisted CE baseline snapshot into the SpendBaseline model."""
    if not isinstance(snapshot, dict):
        return None

    request = snapshot.get("request")
    if not isinstance(request, dict):
        return None
    time_period = request.get("TimePeriod")
    if not isinstance(time_period, dict):
        return None

    window_start = time_period.get("Start")
    window_end = time_period.get("End")
    if not isinstance(window_start, str) or not isinstance(window_end, str):
        return None

    total_amount = snapshot.get("totalAmount")
    average_daily_amount = snapshot.get("averageDailyAmount")
    unit = snapshot.get("unit") or "USD"
    if not isinstance(total_amount, (int, float)) or not isinstance(
        average_daily_amount, (int, float)
    ):
        return None
    if not isinstance(unit, str) or not unit:
        unit = "USD"

    raw_results = snapshot.get("resultsByTime")
    monthly_buckets: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for result in raw_results:
            if not isinstance(result, dict):
                continue
            bucket_period = result.get("TimePeriod")
            if not isinstance(bucket_period, dict):
                continue
            bucket_start = bucket_period.get("Start")
            bucket_end = bucket_period.get("End")
            if not isinstance(bucket_start, str) or not isinstance(bucket_end, str):
                continue

            total = result.get("Total")
            if not isinstance(total, dict):
                continue
            unblended_cost = total.get("UnblendedCost")
            if not isinstance(unblended_cost, dict):
                continue
            raw_amount = unblended_cost.get("Amount")
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError):
                continue
            bucket_unit = unblended_cost.get("Unit")
            monthly_buckets.append(
                {
                    "start": bucket_start,
                    "end": bucket_end,
                    "amount": round(amount, 2),
                    "unit": bucket_unit if isinstance(bucket_unit, str) and bucket_unit else unit,
                }
            )

    return SpendBaseline(
        window_start=window_start,
        window_end=window_end,
        window_days=int(snapshot.get("windowDays") or 30),
        total_amount=round(float(total_amount), 2),
        average_daily_amount=round(float(average_daily_amount), 2),
        unit=unit,
        monthly_buckets=monthly_buckets,
    )


def _load_recommendations(fixture_dir: Path) -> list[Any]:
    raw = _load_optional_json(fixture_dir / "exports.json")
    if raw is None:
        raw = _load_optional_json(fixture_dir / "recommendations.json")
    if raw is None:
        return []
    return validate_export_recommendations_payload(raw)


def _load_schedule_recommendations(fixture_dir: Path) -> list[dict[str, Any]]:
    raw = _load_optional_json(fixture_dir / "schedule_recs.json")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("schedule_recs.json must contain a top-level array.")
    return [item for item in raw if isinstance(item, dict)]


def _load_native_actions(fixture_dir: Path) -> list[ActionOpportunity]:
    raw = _load_optional_json(fixture_dir / "native_actions.json")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("native_actions.json must contain a top-level array.")
    return [ActionOpportunity(**item) for item in raw if isinstance(item, dict)]


def _load_schedule(
    summary_payload: dict[str, Any] | None,
    fallback: ScheduleConfig,
) -> ScheduleConfig:
    if not isinstance(summary_payload, dict):
        return fallback
    run = summary_payload.get("run")
    if not isinstance(run, dict):
        return fallback
    raw_schedule = run.get("schedule")
    if not isinstance(raw_schedule, dict):
        return fallback
    raw_business_hours = raw_schedule.get("business_hours")
    try:
        return ScheduleConfig(
            timezone=str(raw_schedule.get("timezone") or fallback.timezone),
            business_hours=(
                BusinessHours(**raw_business_hours)
                if isinstance(raw_business_hours, dict)
                else fallback.business_hours
            ),
        )
    except ValueError:
        return fallback


def load_demo_fixture_bundle(
    fixture_dir: str | Path,
    *,
    config: AppConfig,
    fallback_region_coverage: RegionCoverage,
) -> DemoFixtureBundle:
    """Load demo fixtures from disk into typed runtime objects."""
    fixture_path = Path(fixture_dir)
    summary_payload = _load_optional_json(fixture_path / "summary.json")
    if summary_payload is not None and not isinstance(summary_payload, dict):
        raise ValueError("summary.json must contain a top-level object.")

    account_map = _load_account_map(
        fixture_path,
        prod_account_ids=config.prod_account_ids,
        nonprod_account_ids=config.nonprod_account_ids,
    )

    run_summary = summary_payload.get("run", {}) if isinstance(summary_payload, dict) else {}
    fallback_account_label = "Demo Fixture"
    if isinstance(run_summary, dict):
        fallback_account_label = str(run_summary.get("account_id") or fallback_account_label)

    access_report = _load_access_report(
        fixture_path,
        fallback_region_coverage=fallback_region_coverage,
        fallback_account_label=fallback_account_label,
    )

    generated_at = (
        str(run_summary.get("generated_at"))
        if isinstance(run_summary, dict) and run_summary.get("generated_at")
        else datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    client_id = config.client_id
    if client_id is None and isinstance(run_summary, dict):
        raw_client_id = run_summary.get("client_id")
        client_id = str(raw_client_id) if raw_client_id else None
    run_id = (
        str(run_summary.get("run_id"))
        if isinstance(run_summary, dict) and run_summary.get("run_id")
        else "demo-fixture"
    )
    region = (
        str(run_summary.get("region"))
        if isinstance(run_summary, dict) and run_summary.get("region")
        else config.region
    )
    schedule = _load_schedule(summary_payload, config.schedule)

    spend_baseline_snapshot = _load_optional_json(fixture_path / "ce_total_spend.json")
    spend_baseline = parse_spend_baseline_snapshot(spend_baseline_snapshot)
    spend_baseline_error = None
    if isinstance(spend_baseline_snapshot, dict):
        raw_error = spend_baseline_snapshot.get("error")
        if isinstance(raw_error, str) and raw_error:
            spend_baseline_error = raw_error

    comparison_context = None
    if isinstance(summary_payload, dict):
        raw_comparison = summary_payload.get("comparison")
        if isinstance(raw_comparison, dict):
            comparison_context = raw_comparison

    return DemoFixtureBundle(
        account_map=account_map,
        access_report=access_report,
        generated_at=generated_at,
        client_id=client_id,
        run_id=run_id,
        region=region,
        schedule=schedule,
        account_label=access_report.account_id or fallback_account_label,
        summary_payload=summary_payload,
        comparison_context=comparison_context,
        spend_baseline=spend_baseline,
        spend_baseline_error=spend_baseline_error,
        spend_baseline_snapshot=(
            spend_baseline_snapshot if isinstance(spend_baseline_snapshot, dict) else None
        ),
        coh_summary=_load_optional_json(fixture_path / "coh_summaries.json"),
        recommendations=_load_recommendations(fixture_path),
        schedule_recommendations=_load_schedule_recommendations(fixture_path),
        native_actions=_load_native_actions(fixture_path),
    )
