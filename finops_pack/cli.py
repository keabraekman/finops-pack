"""CLI entry point for finops_pack."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shlex import quote
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.analyzers.account_classification import classify_accounts
from finops_pack.analyzers.schedule_recommendations import (
    ESTIMATED_STATUS,
    NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS,
    build_schedule_recommendation_rows,
)
from finops_pack.aws.assume_role import assume_role_session
from finops_pack.aws.ce_recommendations import (
    DEFAULT_SP_REQUEST,
    collect_rightsizing_recommendations,
    collect_savings_plans_purchase_recommendations,
)
from finops_pack.aws.cost_explorer import (
    RESOURCE_DAILY_WINDOW_DAYS,
    SPEND_BASELINE_WINDOW_DAYS,
    build_resource_cost_series_lookup,
    collect_resource_daily_costs,
    collect_spend_baseline,
    find_resource_cost_series,
    format_resource_cost_series,
)
from finops_pack.aws.cost_optimization_hub import (
    COH_DETAIL_TOP_N,
    collect_top_recommendation_details,
    enable_cost_optimization_hub,
    list_recommendation_summaries,
    list_recommendations,
    normalize_recommendation,
)
from finops_pack.collectors.ec2 import collect_ec2_inventory
from finops_pack.collectors.organizations import list_accounts, load_account_records
from finops_pack.config import ScheduleConfig, load_config, merge_run_config, resolve_regions
from finops_pack.iam_policy_generator import render_policy, write_policy
from finops_pack.models import (
    AccessCheck,
    AccessReport,
    AccountMapEntry,
    AccountRecord,
    ModuleStatus,
    NormalizedRecommendation,
    RegionCoverage,
    SpendBaseline,
)
from finops_pack.prerequisites import (
    CE_RESOURCE_LEVEL_DOC_NOTE,
    CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE,
)
from finops_pack.publish import (
    PublishAsset,
    publish_preview_site,
    publish_report_site_to_s3,
    write_preview_bundle,
)
from finops_pack.render.dashboard import (
    build_dashboard_download_links,
    render_dashboard_html,
    write_dashboard,
)
from finops_pack.render.exporters import CsvExporter, JsonExporter

BILLING_CONTROL_PLANE_REGION = "us-east-1"
ACCESS_DENIED_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "Client.UnauthorizedOperation",
    "OptInRequiredException",
    "UnauthorizedException",
    "UnauthorizedOperation",
}
RECENT_COMPLETED_DAY_OFFSET = 2
RECENT_COMPLETED_DAY_END_OFFSET = 1
RESOURCE_COST_14D_COLUMN = "Resource cost (14d)"
CE_RIGHTSIZING_FALLBACK_MODULE_ID = "ce_rightsizing_fallback"
CE_SAVINGS_PLAN_FALLBACK_MODULE_ID = "ce_savings_plan_fallback"
REPORT_BUNDLE_NAME = "report-bundle.zip"


def _ce_resource_level_doc_guidance() -> str:
    return f"{CE_RESOURCE_LEVEL_DOC_NOTE} {CE_RESOURCE_LEVEL_ENABLEMENT_GUIDANCE}"


def _build_region_coverage(resolved_regions: list[str]) -> RegionCoverage:
    """Create the region coverage payload for this run."""
    if not resolved_regions:
        resolved_regions = [BILLING_CONTROL_PLANE_REGION]
    return RegionCoverage(
        strategy="fixed",
        primary_region=resolved_regions[0],
        regions=resolved_regions,
    )


def _format_schedule_business_hours(schedule: ScheduleConfig) -> str:
    """Render schedule business hours for CLI and summary output."""
    return (
        f"{','.join(schedule.business_hours.days)}@"
        f"{schedule.business_hours.start_hour:02d}:00-"
        f"{schedule.business_hours.end_hour:02d}:00"
    )


def _extract_client_error(exc: ClientError) -> tuple[str, str]:
    """Return normalized error code and message from a botocore ClientError."""
    error = exc.response.get("Error", {})
    code = str(error.get("Code", "Unknown"))
    message = str(error.get("Message", str(exc)))
    return code, message


def _recent_completed_day_window() -> dict[str, str]:
    """Return a stable one-day billing window for access probes."""
    today = datetime.now(UTC).date()
    start = today - timedelta(days=RECENT_COMPLETED_DAY_OFFSET)
    end = today - timedelta(days=RECENT_COMPLETED_DAY_END_OFFSET)
    return {"Start": start.isoformat(), "End": end.isoformat()}


def _get_account_id(session: Any, caller_identity: dict[str, Any] | None = None) -> str | None:
    """Return the caller account ID when it can be determined."""
    if caller_identity is not None:
        account_id = caller_identity.get("Account")
        if isinstance(account_id, str) and account_id:
            return account_id

    try:
        identity: dict[str, Any] = session.client("sts").get_caller_identity()
    except (ClientError, BotoCoreError):
        return None

    account_id = identity.get("Account")
    if isinstance(account_id, str) and account_id:
        return account_id
    return None


def _check_cost_optimization_hub(session: Any, *, account_id: str | None) -> AccessCheck:
    """Best-effort check for Cost Optimization Hub enrollment."""
    try:
        client = session.client(
            "cost-optimization-hub",
            region_name=BILLING_CONTROL_PLANE_REGION,
        )
        response = client.list_enrollment_statuses()
    except ClientError as exc:
        code, message = _extract_client_error(exc)
        if code in ACCESS_DENIED_CODES:
            reason = (
                "Could not determine Cost Optimization Hub enrollment because "
                f"ListEnrollmentStatuses was denied ({code}). {message}"
            )
        else:
            reason = f"Cost Optimization Hub enrollment check failed ({code}). {message}"
        return AccessCheck(
            check_id="cost_optimization_hub",
            label="COH enabled?",
            enabled=None,
            reason=reason,
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )
    except BotoCoreError as exc:
        return AccessCheck(
            check_id="cost_optimization_hub",
            label="COH enabled?",
            enabled=None,
            reason=f"Cost Optimization Hub enrollment check failed: {exc}",
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )

    items = response.get("items", [])
    matched_item = None
    if account_id is not None:
        matched_item = next(
            (item for item in items if item.get("accountId") == account_id),
            None,
        )
    if matched_item is None and len(items) == 1:
        matched_item = items[0]

    if matched_item is None:
        return AccessCheck(
            check_id="cost_optimization_hub",
            label="COH enabled?",
            enabled=None,
            reason=(
                "Cost Optimization Hub enrollment status was not returned for the current account."
            ),
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )

    status = matched_item.get("status")
    if status == "Active":
        return AccessCheck(
            check_id="cost_optimization_hub",
            label="COH enabled?",
            status="ACTIVE",
            enabled=True,
            reason="Cost Optimization Hub enrollment status is Active.",
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )
    if status == "Inactive":
        return AccessCheck(
            check_id="cost_optimization_hub",
            label="COH enabled?",
            enabled=False,
            reason=(
                "Cost Optimization Hub enrollment status is Inactive. "
                "Recommendations stay unavailable until the account is enrolled."
            ),
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )

    return AccessCheck(
        check_id="cost_optimization_hub",
        label="COH enabled?",
        enabled=None,
        reason=f"Cost Optimization Hub returned an unrecognized enrollment status: {status}",
        checked_in_region=BILLING_CONTROL_PLANE_REGION,
    )


def _check_cost_explorer(session: Any) -> AccessCheck:
    """Best-effort check for Cost Explorer readiness."""
    try:
        client = session.client("ce", region_name=BILLING_CONTROL_PLANE_REGION)
        client.get_cost_and_usage(
            TimePeriod=_recent_completed_day_window(),
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
    except ClientError as exc:
        code, message = _extract_client_error(exc)
        if code in ACCESS_DENIED_CODES:
            enabled = None
            reason = (
                "Could not determine Cost Explorer readiness because "
                f"GetCostAndUsage was denied ({code}). {message}"
            )
        elif code == "DataUnavailableException":
            enabled = False
            reason = "Cost Explorer data is not available yet for a recent completed day."
        else:
            enabled = None
            reason = f"Cost Explorer readiness check failed ({code}). {message}"
        return AccessCheck(
            check_id="cost_explorer",
            label="CE enabled?",
            enabled=enabled,
            reason=reason,
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )
    except BotoCoreError as exc:
        return AccessCheck(
            check_id="cost_explorer",
            label="CE enabled?",
            enabled=None,
            reason=f"Cost Explorer readiness check failed: {exc}",
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )

    return AccessCheck(
        check_id="cost_explorer",
        label="CE enabled?",
        status="ACTIVE",
        enabled=True,
        reason="Cost Explorer returned billing data for a recent completed day.",
        checked_in_region=BILLING_CONTROL_PLANE_REGION,
    )


def _check_resource_level_costs(session: Any) -> AccessCheck:
    """Best-effort check for Cost Explorer resource-level daily data."""
    try:
        client = session.client("ce", region_name=BILLING_CONTROL_PLANE_REGION)
        client.get_cost_and_usage_with_resources(
            TimePeriod=_recent_completed_day_window(),
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
    except ClientError as exc:
        code, message = _extract_client_error(exc)
        if code in ACCESS_DENIED_CODES:
            enabled = None
            reason = (
                "Could not determine resource-level Cost Explorer readiness because "
                f"GetCostAndUsageWithResources was denied ({code}). {message}"
            )
        elif code == "DataUnavailableException":
            enabled = False
            reason = (
                "Resource-level daily cost data is not enabled or has not populated "
                f"for the last 14 days. {CE_RESOURCE_LEVEL_DOC_NOTE}"
            )
        else:
            enabled = None
            reason = f"Resource-level Cost Explorer check failed ({code}). {message}"
        return AccessCheck(
            check_id="resource_level_costs",
            label="resource-level enabled?",
            enabled=enabled,
            reason=reason,
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )
    except BotoCoreError as exc:
        return AccessCheck(
            check_id="resource_level_costs",
            label="resource-level enabled?",
            enabled=None,
            reason=f"Resource-level Cost Explorer check failed: {exc}",
            checked_in_region=BILLING_CONTROL_PLANE_REGION,
        )

    return AccessCheck(
        check_id="resource_level_costs",
        label="resource-level enabled?",
        status="ACTIVE",
        enabled=True,
        reason="Resource-level daily cost data is queryable for a recent completed day.",
        checked_in_region=BILLING_CONTROL_PLANE_REGION,
    )


def _module_from_check(check: AccessCheck) -> ModuleStatus:
    """Convert an access check into a module readiness status."""
    labels = {
        "cost_optimization_hub": "Cost Optimization Hub module",
        "cost_explorer": "Cost Explorer module",
        "resource_level_costs": "Resource-level cost module",
    }
    return ModuleStatus(
        module_id=check.check_id,
        label=labels.get(check.check_id, check.label),
        status="ACTIVE" if check.enabled is True else "DEGRADED",
        reason=check.reason,
    )


def _build_access_report(
    session: Any,
    *,
    region_coverage: RegionCoverage,
    caller_identity: dict[str, Any] | None = None,
) -> AccessReport:
    """Collect best-effort AWS prerequisite status for billing modules."""
    account_id = _get_account_id(session, caller_identity)
    checks = [
        _check_cost_optimization_hub(session, account_id=account_id),
        _check_cost_explorer(session),
        _check_resource_level_costs(session),
    ]
    return AccessReport(
        account_id=account_id,
        region_coverage=region_coverage,
        checks=checks,
        modules=[_module_from_check(check) for check in checks],
    )


def _append_optional_fallback_modules(
    access_report: AccessReport,
    *,
    enable_ce_rightsizing_fallback: bool,
    enable_ce_savings_plan_fallback: bool,
) -> None:
    """Append optional CE fallback modules to the access report when enabled."""
    check_map = {check.check_id: check for check in access_report.checks}
    cost_explorer_check = check_map.get("cost_explorer")

    module_specs = [
        (
            enable_ce_rightsizing_fallback,
            CE_RIGHTSIZING_FALLBACK_MODULE_ID,
            "CE rightsizing fallback module",
        ),
        (
            enable_ce_savings_plan_fallback,
            CE_SAVINGS_PLAN_FALLBACK_MODULE_ID,
            "CE Savings Plans fallback module",
        ),
    ]
    for enabled, module_id, label in module_specs:
        if not enabled:
            continue

        if cost_explorer_check is None:
            status = "DEGRADED"
            reason = "Cost Explorer prerequisite status was not available for this run."
        elif cost_explorer_check.enabled is True:
            status = "ACTIVE"
            reason = (
                "Optional fallback is enabled. Cost Explorer is queryable and can be used "
                "if Cost Optimization Hub needs a secondary path."
            )
        else:
            status = "DEGRADED"
            reason = (
                "Optional fallback is enabled but blocked because Cost Explorer is not ready. "
                f"{cost_explorer_check.reason}"
            )

        access_report.modules.append(
            ModuleStatus(
                module_id=module_id,
                label=label,
                status=status,
                reason=reason,
            )
        )


def _format_enabled(enabled: bool | None) -> str:
    """Render tri-state booleans for console output."""
    if enabled is True:
        return "yes"
    if enabled is False:
        return "no"
    return "unknown"


def _print_region_coverage(region_coverage: RegionCoverage) -> None:
    """Emit region coverage details to stdout."""
    print(f"region_discovery_strategy={region_coverage.strategy}")
    print(f"region_coverage={','.join(region_coverage.regions)}")


def _print_schedule_config(schedule: ScheduleConfig) -> None:
    """Emit schedule configuration details to stdout."""
    print(f"schedule_timezone={schedule.timezone}")
    print(f"schedule_business_hours={_format_schedule_business_hours(schedule)}")


def _print_access_report(access_report: AccessReport) -> None:
    """Emit access report details to stdout."""
    check_map = {check.check_id: check for check in access_report.checks}
    print(f"coh_enabled={_format_enabled(check_map['cost_optimization_hub'].enabled)}")
    print(f"ce_enabled={_format_enabled(check_map['cost_explorer'].enabled)}")
    print(f"resource_level_enabled={_format_enabled(check_map['resource_level_costs'].enabled)}")
    for module in access_report.modules:
        print(f"module_{module.module_id}={module.status}: {module.reason}")


def _print_ce_spend_summary(
    spend_baseline_path: Path,
    spend_baseline_snapshot: dict[str, Any],
    spend_baseline: SpendBaseline | None,
) -> None:
    """Emit Cost Explorer spend baseline summary lines to stdout."""
    print(f"ce_total_spend_path={spend_baseline_path}")
    if spend_baseline is not None:
        print(f"ce_total_spend_last_30_days={spend_baseline.total_amount}")
        print(f"ce_average_daily_spend={spend_baseline.average_daily_amount}")
        print(f"ce_spend_baseline_bucket_count={len(spend_baseline.monthly_buckets)}")

    spend_baseline_error = spend_baseline_snapshot.get("error")
    if isinstance(spend_baseline_error, str) and spend_baseline_error:
        print(f"ce_total_spend_error={spend_baseline_error}")


def _print_ce_resource_daily_summary(
    resource_daily_path: Path,
    resource_daily_snapshot: dict[str, Any],
) -> None:
    """Emit resource-level Cost Explorer collection details to stdout."""
    print(f"ce_resource_daily_path={resource_daily_path}")
    print(
        f"ce_resource_daily_time_period_count={resource_daily_snapshot.get('timePeriodCount', 0)}"
    )
    print(f"ce_resource_daily_group_count={resource_daily_snapshot.get('groupCount', 0)}")

    resource_daily_error = resource_daily_snapshot.get("error")
    if isinstance(resource_daily_error, str) and resource_daily_error:
        print(f"ce_resource_daily_error={resource_daily_error}")


def _collect_ce_rightsizing_snapshot(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Collect and persist optional CE rightsizing fallback recommendations."""
    raw_dir = _raw_output_dir(output_dir)
    rightsizing_path = raw_dir / "ce_rightsizing_recommendations.json"

    try:
        rightsizing_snapshot = collect_rightsizing_recommendations(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        rightsizing_snapshot = {
            "operation": "GetRightsizingRecommendation",
            "request": {
                "Service": "AmazonEC2",
                "Configuration": {
                    "RecommendationTarget": "SAME_INSTANCE_FAMILY",
                    "BenefitsConsidered": True,
                },
                "PageSize": 20,
            },
            "pages": [],
            "items": [],
            "recommendationCount": 0,
            "error": str(exc),
        }

    _write_json_snapshot(rightsizing_path, rightsizing_snapshot)
    return rightsizing_path, rightsizing_snapshot


def _print_ce_rightsizing_summary(
    rightsizing_path: Path,
    rightsizing_snapshot: dict[str, Any],
) -> None:
    """Emit optional CE rightsizing fallback output details to stdout."""
    print(f"ce_rightsizing_fallback_path={rightsizing_path}")
    print(
        "ce_rightsizing_fallback_recommendation_count="
        f"{rightsizing_snapshot.get('recommendationCount', 0)}"
    )
    rightsizing_error = rightsizing_snapshot.get("error")
    if isinstance(rightsizing_error, str) and rightsizing_error:
        print(f"ce_rightsizing_fallback_error={rightsizing_error}")


def _collect_ce_savings_plan_snapshot(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Collect and persist optional CE Savings Plans fallback recommendations."""
    raw_dir = _raw_output_dir(output_dir)
    savings_plan_path = raw_dir / "ce_savings_plan_recommendations.json"

    try:
        savings_plan_snapshot = collect_savings_plans_purchase_recommendations(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        savings_plan_snapshot = {
            "operation": "GetSavingsPlansPurchaseRecommendation",
            "startGenerationResponse": None,
            "startGenerationError": None,
            "request": DEFAULT_SP_REQUEST,
            "pages": [],
            "items": [],
            "recommendationCount": 0,
            "detailCount": 0,
            "details": [],
            "error": str(exc),
        }

    _write_json_snapshot(savings_plan_path, savings_plan_snapshot)
    return savings_plan_path, savings_plan_snapshot


def _print_ce_savings_plan_summary(
    savings_plan_path: Path,
    savings_plan_snapshot: dict[str, Any],
) -> None:
    """Emit optional CE Savings Plans fallback output details to stdout."""
    print(f"ce_savings_plan_fallback_path={savings_plan_path}")
    print(
        "ce_savings_plan_fallback_recommendation_count="
        f"{savings_plan_snapshot.get('recommendationCount', 0)}"
    )
    print(f"ce_savings_plan_fallback_detail_count={savings_plan_snapshot.get('detailCount', 0)}")
    savings_plan_error = savings_plan_snapshot.get("error")
    if isinstance(savings_plan_error, str) and savings_plan_error:
        print(f"ce_savings_plan_fallback_error={savings_plan_error}")


def _load_account_records_best_effort(
    session: Any,
    *,
    current_account_id: str | None,
) -> tuple[list[AccountRecord], str | None]:
    """Load Organizations accounts or fall back to the current account."""
    try:
        return list_accounts(session), None
    except RuntimeError as exc:
        fallback_account_id = current_account_id or "unknown"
        return (
            [
                AccountRecord(
                    account_id=fallback_account_id,
                    name="Current account",
                    status="ACTIVE",
                )
            ],
            str(exc),
        )


def _raw_output_dir(output_dir: Path) -> Path:
    """Return the raw snapshot directory rooted alongside the configured output dir."""
    return _artifact_output_dir(output_dir) / "raw"


def _normalized_output_dir(output_dir: Path) -> Path:
    """Return the normalized output directory rooted alongside the configured output dir."""
    return _artifact_output_dir(output_dir) / "normalized"


def _artifact_output_dir(output_dir: Path) -> Path:
    """Return the stable artifact root for raw, normalized, and summary outputs."""
    return output_dir if output_dir.name == "out" else output_dir.parent / "out"


def _write_json_snapshot(destination: Path, payload: dict[str, Any]) -> Path:
    """Write snapshot JSON with stable formatting."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return destination


def _collect_ec2_inventory_snapshot(
    session: Any,
    *,
    output_dir: Path,
    account_records: list[AccountRecord],
    regions: list[str],
    role_arn: str,
    external_id: str | None,
    session_name: str,
    current_account_id: str | None,
) -> tuple[Path, dict[str, Any]]:
    """Collect and persist best-effort EC2 inventory."""
    raw_dir = _raw_output_dir(output_dir)
    inventory_path = raw_dir / "ec2_inventory.json"
    inventory_snapshot = collect_ec2_inventory(
        session,
        account_records=account_records,
        regions=regions,
        role_arn=role_arn,
        external_id=external_id,
        session_name=session_name,
        current_account_id=current_account_id,
    )
    _write_json_snapshot(inventory_path, inventory_snapshot)
    return inventory_path, inventory_snapshot


def _print_ec2_inventory_summary(inventory_path: Path, inventory_snapshot: dict[str, Any]) -> None:
    """Emit EC2 inventory output details to stdout."""
    print(f"ec2_inventory_path={inventory_path}")
    print(f"ec2_inventory_instance_count={inventory_snapshot.get('itemCount', 0)}")
    print(f"ec2_inventory_error_count={inventory_snapshot.get('errorCount', 0)}")


def _merge_module_collection_status(
    access_report: AccessReport,
    *,
    module_id: str,
    reasons: list[str],
) -> None:
    """Mark a module degraded when downstream collection hits an error."""
    module = next((item for item in access_report.modules if item.module_id == module_id), None)
    if module is None or not reasons:
        return

    module.status = "DEGRADED"
    merged_reasons = [module.reason, *reasons]
    module.reason = "; ".join(
        reason for reason in dict.fromkeys(merged_reasons) if isinstance(reason, str) and reason
    )


def _collect_ce_spend_baseline(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, dict[str, Any], SpendBaseline | None]:
    """Collect and persist the Cost Explorer spend baseline snapshot."""
    raw_dir = _raw_output_dir(output_dir)
    spend_baseline_path = raw_dir / "ce_total_spend.json"

    try:
        spend_baseline_snapshot, spend_baseline = collect_spend_baseline(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        spend_baseline_snapshot = {
            "operation": "GetCostAndUsage",
            "request": {
                "TimePeriod": {
                    "Start": (
                        datetime.now(UTC).date() - timedelta(days=SPEND_BASELINE_WINDOW_DAYS)
                    ).isoformat(),
                    "End": datetime.now(UTC).date().isoformat(),
                },
                "Granularity": "MONTHLY",
                "Metrics": ["UnblendedCost"],
            },
            "pages": [],
            "resultsByTime": [],
            "bucketCount": 0,
            "windowDays": SPEND_BASELINE_WINDOW_DAYS,
            "totalAmount": None,
            "averageDailyAmount": None,
            "unit": None,
            "error": str(exc),
        }
        spend_baseline = None

    _write_json_snapshot(spend_baseline_path, spend_baseline_snapshot)
    return spend_baseline_path, spend_baseline_snapshot, spend_baseline


def _collect_ce_resource_daily_snapshot(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Collect and persist the optional resource-level daily Cost Explorer snapshot."""
    raw_dir = _raw_output_dir(output_dir)
    resource_daily_path = raw_dir / "ce_resource_daily.json"

    try:
        resource_daily_snapshot = collect_resource_daily_costs(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        resource_daily_snapshot = {
            "operation": "GetCostAndUsageWithResources",
            "request": {
                "TimePeriod": {
                    "Start": (
                        datetime.now(UTC).date() - timedelta(days=RESOURCE_DAILY_WINDOW_DAYS)
                    ).isoformat(),
                    "End": datetime.now(UTC).date().isoformat(),
                },
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost"],
                "Filter": {
                    "Dimensions": {
                        "Key": "SERVICE",
                        "Values": ["Amazon Elastic Compute Cloud - Compute"],
                    }
                },
                "GroupBy": [{"Type": "DIMENSION", "Key": "RESOURCE_ID"}],
            },
            "pages": [],
            "resultsByTime": [],
            "timePeriodCount": 0,
            "groupCount": 0,
            "windowDays": RESOURCE_DAILY_WINDOW_DAYS,
            "error": str(exc),
        }

    _write_json_snapshot(resource_daily_path, resource_daily_snapshot)
    return resource_daily_path, resource_daily_snapshot


def _collect_coh_raw_snapshots(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    """Collect and persist raw Cost Optimization Hub summary and recommendation payloads."""
    raw_dir = _raw_output_dir(output_dir)
    summaries_path = raw_dir / "coh_summaries.json"
    recommendations_path = raw_dir / "coh_recommendations.json"

    try:
        summaries_snapshot = list_recommendation_summaries(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        summaries_snapshot = {
            "operation": "ListRecommendationSummaries",
            "request": {},
            "pages": [],
            "items": [],
            "itemCount": 0,
            "estimatedTotalDedupedSavings": None,
            "currencyCode": None,
            "groupBy": None,
            "metrics": None,
            "error": str(exc),
        }

    try:
        recommendations_snapshot = list_recommendations(
            session,
            region_name=region_name,
            rate_limit_safe_mode=rate_limit_safe_mode,
        )
    except RuntimeError as exc:
        recommendations_snapshot = {
            "operation": "ListRecommendations",
            "request": {"includeAllRecommendations": True},
            "pages": [],
            "items": [],
            "itemCount": 0,
            "error": str(exc),
        }

    _write_json_snapshot(summaries_path, summaries_snapshot)
    _write_json_snapshot(recommendations_path, recommendations_snapshot)
    return (
        summaries_path,
        summaries_snapshot,
        recommendations_path,
        recommendations_snapshot,
    )


def _merge_coh_collection_status(
    access_report: AccessReport,
    *,
    summaries_snapshot: dict[str, Any],
    recommendations_snapshot: dict[str, Any],
    detail_errors: list[str] | None = None,
) -> None:
    """Mark the COH module degraded when the collector itself is blocked."""
    collector_reasons = [
        message
        for message in (
            summaries_snapshot.get("error"),
            recommendations_snapshot.get("error"),
        )
        if isinstance(message, str) and message
    ]
    if detail_errors:
        collector_reasons.extend(detail_errors)
    _merge_module_collection_status(
        access_report,
        module_id="cost_optimization_hub",
        reasons=collector_reasons,
    )


def _print_coh_collection_summary(
    summaries_path: Path,
    summaries_snapshot: dict[str, Any],
    recommendations_path: Path,
    recommendations_snapshot: dict[str, Any],
) -> None:
    """Emit COH collector summary lines to stdout."""
    print(f"coh_summaries_path={summaries_path}")
    print(f"coh_recommendations_path={recommendations_path}")
    print(f"coh_summary_count={summaries_snapshot.get('itemCount', 0)}")
    print(
        "coh_estimated_total_deduped_savings="
        f"{summaries_snapshot.get('estimatedTotalDedupedSavings')}"
    )
    print(f"coh_recommendation_count={recommendations_snapshot.get('itemCount', 0)}")

    summaries_error = summaries_snapshot.get("error")
    recommendations_error = recommendations_snapshot.get("error")
    if isinstance(summaries_error, str) and summaries_error:
        print(f"coh_summaries_error={summaries_error}")
    if isinstance(recommendations_error, str) and recommendations_error:
        print(f"coh_recommendations_error={recommendations_error}")


def _collect_coh_normalized_recommendations(
    session: Any,
    *,
    recommendations_snapshot: dict[str, Any],
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
    top_n: int = COH_DETAIL_TOP_N,
    rate_limit_safe_mode: bool = False,
) -> tuple[Path, list[NormalizedRecommendation], list[str]]:
    """Fetch top COH recommendation details, normalize them, and persist the result."""
    normalized_dir = _normalized_output_dir(output_dir)
    normalized_path = normalized_dir / "recommendations.json"

    detail_pairs, detail_errors = collect_top_recommendation_details(
        session,
        recommendations_snapshot=recommendations_snapshot,
        top_n=top_n,
        region_name=region_name,
        rate_limit_safe_mode=rate_limit_safe_mode,
    )
    normalized_recommendations = [
        normalize_recommendation(detail, list_item=list_item) for list_item, detail in detail_pairs
    ]
    JsonExporter().export(normalized_recommendations, normalized_path)
    return normalized_path, normalized_recommendations, detail_errors


def _build_coh_csv_rows(
    recommendations: list[NormalizedRecommendation],
    *,
    resource_cost_lookup: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Map normalized recommendations into the CSV export shape."""
    rows: list[dict[str, Any]] = []
    for recommendation in recommendations:
        row = {
            "resourceId": recommendation.resource_id or "",
            "accountId": recommendation.account_id or "",
            "type": (
                recommendation.current_resource_type
                or recommendation.recommended_resource_type
                or recommendation.category
            ),
            "action": recommendation.action_type or "",
            "estSavings": (
                ""
                if recommendation.estimated_monthly_savings is None
                else recommendation.estimated_monthly_savings
            ),
            "region": recommendation.region or "",
        }
        if resource_cost_lookup:
            row[RESOURCE_COST_14D_COLUMN] = format_resource_cost_series(
                find_resource_cost_series(
                    resource_cost_lookup,
                    resource_arn=recommendation.resource_arn,
                    resource_id=recommendation.resource_id,
                )
            )
        rows.append(row)
    return rows


def _export_coh_recommendations(
    recommendations: list[NormalizedRecommendation],
    *,
    output_dir: Path,
    resource_daily_snapshot: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write CSV and JSON recommendation exports for the current run."""
    csv_path = output_dir / "exports.csv"
    json_path = output_dir / "exports.json"

    resource_cost_lookup = (
        build_resource_cost_series_lookup(resource_daily_snapshot)
        if resource_daily_snapshot is not None
        else {}
    )
    csv_rows = _build_coh_csv_rows(
        recommendations,
        resource_cost_lookup=resource_cost_lookup,
    )
    fieldnames = ["resourceId", "accountId", "type", "action", "estSavings", "region"]
    if any(row.get(RESOURCE_COST_14D_COLUMN) for row in csv_rows):
        fieldnames.append(RESOURCE_COST_14D_COLUMN)

    CsvExporter(fieldnames=fieldnames).export(csv_rows, csv_path)
    JsonExporter().export(recommendations, json_path)
    return csv_path, json_path


def _print_coh_normalized_summary(
    normalized_path: Path,
    normalized_count: int,
    detail_errors: list[str],
) -> None:
    """Emit normalized COH output details to stdout."""
    print(f"coh_normalized_recommendations_path={normalized_path}")
    print(f"coh_normalized_recommendation_count={normalized_count}")
    for error in detail_errors:
        print(f"coh_detail_error={error}")


def _print_coh_export_summary(csv_path: Path, json_path: Path) -> None:
    """Emit export file paths to stdout."""
    print(f"coh_csv_export_path={csv_path}")
    print(f"coh_json_export_path={json_path}")


def _export_schedule_recommendations(
    inventory_snapshot: dict[str, Any],
    *,
    output_dir: Path,
    schedule: ScheduleConfig,
    resource_daily_snapshot: dict[str, Any] | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    """Write schedule recommendation CSV output for stoppable EC2 candidates."""
    schedule_dir = _artifact_output_dir(output_dir) / "schedule"
    csv_path = schedule_dir / "schedule_recs.csv"
    rows = build_schedule_recommendation_rows(
        inventory_snapshot,
        schedule=schedule,
        resource_daily_snapshot=resource_daily_snapshot,
    )
    fieldnames = [
        "accountId",
        "accountName",
        "region",
        "instanceId",
        "instanceArn",
        "name",
        "state",
        "instanceType",
        "platform",
        "launchTime",
        "scheduleTimezone",
        "businessHours",
        "offHoursRatio",
        "costWindowDays",
        "recentAvgDailyCost",
        "estimatedOffHoursDailySavingsLow",
        "estimatedOffHoursDailySavings",
        "estimatedOffHoursDailySavingsHigh",
        RESOURCE_COST_14D_COLUMN,
        "estimationStatus",
        "estimationReason",
        "candidateReason",
    ]
    CsvExporter(fieldnames=fieldnames).export(rows, csv_path)
    return csv_path, rows


def _print_schedule_recommendation_summary(
    schedule_csv_path: Path,
    schedule_rows: list[dict[str, Any]],
) -> None:
    """Emit schedule recommendation output details to stdout."""
    estimated_count = sum(
        1 for row in schedule_rows if row.get("estimationStatus") == ESTIMATED_STATUS
    )
    needs_opt_in_count = sum(
        1
        for row in schedule_rows
        if row.get("estimationStatus") == NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS
    )
    print(f"schedule_recs_path={schedule_csv_path}")
    print(f"schedule_recommendation_count={len(schedule_rows)}")
    print(f"schedule_estimated_count={estimated_count}")
    print(f"schedule_needs_ce_resource_level_opt_in_count={needs_opt_in_count}")


def _build_summary_payload(
    *,
    region: str,
    schedule: ScheduleConfig,
    rate_limit_safe_mode: bool,
    account_map: list[AccountMapEntry],
    access_report: AccessReport,
    ec2_inventory_snapshot: dict[str, Any] | None = None,
    schedule_recommendations: list[dict[str, Any]] | None = None,
    ce_rightsizing_snapshot: dict[str, Any] | None = None,
    ce_savings_plan_snapshot: dict[str, Any] | None = None,
    spend_baseline: SpendBaseline | None = None,
    resource_daily_snapshot: dict[str, Any] | None = None,
    summaries_snapshot: dict[str, Any] | None = None,
    recommendations_snapshot: dict[str, Any] | None = None,
    normalized_recommendations: list[NormalizedRecommendation] | None = None,
    detail_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Build the diff-friendly totals artifact for the current run."""
    environment_counts = {"prod": 0, "nonprod": 0, "unknown": 0}
    for account in account_map:
        environment_counts[account.environment] += 1

    normalized_recommendation_list = normalized_recommendations or []
    schedule_recommendation_list = schedule_recommendations or []
    total_normalized_monthly_savings = round(
        sum(item.estimated_monthly_savings or 0.0 for item in normalized_recommendation_list),
        2,
    )
    schedule_estimated_count = sum(
        1 for row in schedule_recommendation_list if row.get("estimationStatus") == ESTIMATED_STATUS
    )
    schedule_needs_opt_in_count = sum(
        1
        for row in schedule_recommendation_list
        if row.get("estimationStatus") == NEEDS_CE_RESOURCE_LEVEL_OPT_IN_STATUS
    )

    return {
        "run": {
            "account_id": access_report.account_id,
            "region": region,
            "schedule": {
                "timezone": schedule.timezone,
                "business_hours": {
                    "days": schedule.business_hours.days,
                    "start_hour": schedule.business_hours.start_hour,
                    "end_hour": schedule.business_hours.end_hour,
                },
            },
            "rate_limit_safe_mode": rate_limit_safe_mode,
        },
        "accounts": {
            "total": len(account_map),
            "prod": environment_counts["prod"],
            "nonprod": environment_counts["nonprod"],
            "unknown": environment_counts["unknown"],
        },
        "findings": {
            "total": 0,
        },
        "inventory": {
            "ec2_instance_count": (
                0 if ec2_inventory_snapshot is None else ec2_inventory_snapshot.get("itemCount", 0)
            ),
            "ec2_inventory_error_count": (
                0 if ec2_inventory_snapshot is None else ec2_inventory_snapshot.get("errorCount", 0)
            ),
        },
        "schedule_recommendations": {
            "recommendation_count": len(schedule_recommendation_list),
            "estimated_count": schedule_estimated_count,
            "needs_ce_resource_level_opt_in_count": schedule_needs_opt_in_count,
        },
        "fallbacks": {
            "ce_rightsizing_recommendation_count": (
                0
                if ce_rightsizing_snapshot is None
                else ce_rightsizing_snapshot.get("recommendationCount", 0)
            ),
            "ce_savings_plan_recommendation_count": (
                0
                if ce_savings_plan_snapshot is None
                else ce_savings_plan_snapshot.get("recommendationCount", 0)
            ),
            "ce_savings_plan_detail_count": (
                0
                if ce_savings_plan_snapshot is None
                else ce_savings_plan_snapshot.get("detailCount", 0)
            ),
        },
        "ce": {
            "spend_baseline_total": (
                None if spend_baseline is None else spend_baseline.total_amount
            ),
            "spend_baseline_unit": None if spend_baseline is None else spend_baseline.unit,
            "spend_baseline_bucket_count": (
                0 if spend_baseline is None else len(spend_baseline.monthly_buckets)
            ),
            "resource_daily_collected": resource_daily_snapshot is not None,
            "resource_daily_time_period_count": (
                0
                if resource_daily_snapshot is None
                else resource_daily_snapshot.get("timePeriodCount", 0)
            ),
            "resource_daily_group_count": (
                0
                if resource_daily_snapshot is None
                else resource_daily_snapshot.get("groupCount", 0)
            ),
        },
        "coh": {
            "summary_count": (
                0 if summaries_snapshot is None else summaries_snapshot.get("itemCount", 0)
            ),
            "recommendation_count": (
                0
                if recommendations_snapshot is None
                else recommendations_snapshot.get("itemCount", 0)
            ),
            "normalized_recommendation_count": len(normalized_recommendation_list),
            "detail_error_count": len(detail_errors or []),
            "estimated_total_deduped_savings": (
                None
                if summaries_snapshot is None
                else summaries_snapshot.get("estimatedTotalDedupedSavings")
            ),
            "normalized_estimated_monthly_savings": total_normalized_monthly_savings,
        },
        "access_report": {
            "check_count": len(access_report.checks),
            "degraded_check_count": len(
                [check for check in access_report.checks if check.status == "DEGRADED"]
            ),
            "module_count": len(access_report.modules),
            "degraded_module_count": len(
                [module for module in access_report.modules if module.status == "DEGRADED"]
            ),
        },
    }


def _write_summary_output(
    output_dir: Path,
    *,
    region: str,
    schedule: ScheduleConfig,
    rate_limit_safe_mode: bool,
    account_map: list[AccountMapEntry],
    access_report: AccessReport,
    ec2_inventory_snapshot: dict[str, Any] | None = None,
    schedule_recommendations: list[dict[str, Any]] | None = None,
    ce_rightsizing_snapshot: dict[str, Any] | None = None,
    ce_savings_plan_snapshot: dict[str, Any] | None = None,
    spend_baseline: SpendBaseline | None = None,
    resource_daily_snapshot: dict[str, Any] | None = None,
    summaries_snapshot: dict[str, Any] | None = None,
    recommendations_snapshot: dict[str, Any] | None = None,
    normalized_recommendations: list[NormalizedRecommendation] | None = None,
    detail_errors: list[str] | None = None,
) -> Path:
    """Persist the summary totals artifact under out/summary.json."""
    summary_path = _artifact_output_dir(output_dir) / "summary.json"
    payload = _build_summary_payload(
        region=region,
        schedule=schedule,
        rate_limit_safe_mode=rate_limit_safe_mode,
        account_map=account_map,
        access_report=access_report,
        ec2_inventory_snapshot=ec2_inventory_snapshot,
        schedule_recommendations=schedule_recommendations,
        ce_rightsizing_snapshot=ce_rightsizing_snapshot,
        ce_savings_plan_snapshot=ce_savings_plan_snapshot,
        spend_baseline=spend_baseline,
        resource_daily_snapshot=resource_daily_snapshot,
        summaries_snapshot=summaries_snapshot,
        recommendations_snapshot=recommendations_snapshot,
        normalized_recommendations=normalized_recommendations,
        detail_errors=detail_errors,
    )
    return _write_json_snapshot(summary_path, payload)


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="finops-pack",
        description="Starter CLI for the finops_pack project.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run finops-pack against AWS.",
    )
    run_parser.add_argument(
        "--role-arn",
        help="AWS IAM role ARN to assume.",
    )
    run_parser.add_argument(
        "--external-id",
        help="External ID to use when assuming the role.",
    )
    run_parser.add_argument(
        "--region",
        help="AWS region to use (default: us-east-1).",
    )
    run_parser.add_argument(
        "--session-name",
        help="STS session name (default: finops-pack).",
    )
    run_parser.add_argument(
        "--check-identity",
        action="store_true",
        help="Call STS GetCallerIdentity after assuming the role.",
    )
    run_parser.add_argument(
        "--config",
        help="Optional path to config.yaml.",
    )
    run_parser.add_argument(
        "--output-dir",
        help="Directory where generated reports and JSON artifacts are written.",
    )
    run_parser.add_argument(
        "--report-bucket",
        help="Optional S3 bucket where report artifacts are published.",
    )
    run_parser.add_argument(
        "--report-client-id",
        help="Client identifier used in the S3 report prefix: client-id/run-id/.",
    )
    run_parser.add_argument(
        "--report-retention-days",
        type=int,
        help="Days to retain older report prefixes in S3 before deletion (default: 7).",
    )
    run_parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Force fully local output even when report_bucket/report_client_id are configured.",
    )
    run_parser.add_argument(
        "--rate-limit-safe-mode",
        action="store_true",
        help="Reduce request burstiness and retry throttled Cost Optimization Hub calls.",
    )
    run_parser.add_argument(
        "--enable-coh",
        action="store_true",
        help=(
            "Enable Cost Optimization Hub in the target account. Requires extra IAM permissions."
        ),
    )
    run_parser.add_argument(
        "--collect-ce-resource-daily",
        action="store_true",
        help=(
            "Collect optional Cost Explorer resource-level daily EC2 spend for the last 14 "
            "completed days and store it under out/raw/ce_resource_daily.json."
        ),
    )
    run_parser.add_argument(
        "--enable-ce-rightsizing-fallback",
        action="store_true",
        help=(
            "Collect optional Cost Explorer GetRightsizingRecommendation snapshots as a "
            "disabled-by-default fallback path. Cost Optimization Hub remains primary."
        ),
    )
    run_parser.add_argument(
        "--enable-ce-savings-plan-fallback",
        action="store_true",
        help=(
            "Collect optional Cost Explorer Savings Plans purchase recommendation snapshots "
            "as a disabled-by-default fallback path. Cost Optimization Hub remains primary."
        ),
    )

    demo_parser = subparsers.add_parser(
        "demo",
        help="Run finops-pack in demo mode using fixture data.",
    )
    demo_parser.add_argument(
        "--config",
        help="Optional path to config.yaml.",
    )
    demo_parser.add_argument(
        "--output-dir",
        help="Directory where generated demo artifacts are written.",
    )

    policy_parser = subparsers.add_parser(
        "iam-policy",
        help="Emit a starter IAM policy JSON document.",
    )
    policy_parser.add_argument(
        "--mode",
        choices=("min", "full"),
        default="min",
        help="Template variant to emit (default: min).",
    )
    policy_parser.add_argument(
        "--output",
        help="Optional file path to write the generated policy JSON.",
    )

    return parser


def _write_account_outputs(
    account_map: list[AccountMapEntry],
    *,
    output_dir: Path,
    region: str,
    account_id: str,
    access_report: AccessReport,
    spend_baseline: SpendBaseline | None = None,
    spend_baseline_error: str | None = None,
    coh_summary: dict[str, Any] | None = None,
    recommendations: list[NormalizedRecommendation] | None = None,
    schedule_recommendations: list[dict[str, Any]] | None = None,
    privacy_context: dict[str, str] | None = None,
    download_links: list[dict[str, str]] | None = None,
) -> tuple[Path, Path, Path]:
    """Write JSON and HTML artifacts for classified accounts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    accounts_path = output_dir / "accounts.json"
    access_report_path = output_dir / "access_report.json"
    dashboard_path = output_dir / "dashboard.html"

    JsonExporter().export(account_map, accounts_path)
    access_report_path.write_text(
        json.dumps(asdict(access_report), indent=2) + "\n", encoding="utf-8"
    )
    write_dashboard(
        account_map,
        dashboard_path,
        account_id=account_id,
        region=region,
        access_report=access_report,
        spend_baseline=spend_baseline,
        spend_baseline_error=spend_baseline_error,
        coh_summary=coh_summary,
        recommendations=recommendations,
        schedule_recommendations=schedule_recommendations,
        privacy_context=privacy_context,
        download_links=download_links,
    )

    return accounts_path, access_report_path, dashboard_path


def _build_dashboard_download_targets(
    *,
    accounts_path: Path,
    access_report_path: Path,
    summary_path: Path,
    bundle_path: Path | None = None,
    coh_csv_export_path: Path | None = None,
    coh_json_export_path: Path | None = None,
    schedule_recs_path: Path | None = None,
) -> list[tuple[str, str, Path]]:
    """Build labeled dashboard download targets."""
    targets: list[tuple[str, str, Path]] = []

    if bundle_path is not None:
        targets.append(
            (
                "Download All",
                "Zipped preview bundle with the report HTML and linked artifacts.",
                bundle_path,
            )
        )

    targets.extend(
        [
            (
                "Accounts JSON",
                "Normalized account inventory with environment classification metadata.",
                accounts_path,
            ),
            (
                "Access Report JSON",
                "Billing prerequisite checks, module readiness, and region coverage.",
                access_report_path,
            ),
            (
                "Summary JSON",
                "Diff-friendly run totals for accounts, COH collection, and scheduling outputs.",
                summary_path,
            ),
        ]
    )

    if coh_csv_export_path is not None:
        targets.append(
            (
                "COH Export CSV",
                "Flattened Cost Optimization Hub recommendations for spreadsheet workflows.",
                coh_csv_export_path,
            )
        )

    if coh_json_export_path is not None:
        targets.append(
            (
                "COH Export JSON",
                "Full normalized Cost Optimization Hub recommendation payloads.",
                coh_json_export_path,
            )
        )

    if schedule_recs_path is not None:
        targets.append(
            (
                "Schedule CSV",
                "Non-prod EC2 stop-schedule candidates with savings estimates.",
                schedule_recs_path,
            )
        )

    return targets


def _build_s3_publish_assets(
    *,
    preview_dir: Path,
    preview_download_targets: list[tuple[str, str, Path]],
) -> list[PublishAsset]:
    """Build the S3 upload manifest from the local preview site."""
    assets = [
        PublishAsset(
            source_path=preview_dir / "style.css",
            object_name="style.css",
        )
    ]

    for label, description, target in preview_download_targets:
        if target.name == REPORT_BUNDLE_NAME:
            continue
        assets.append(
            PublishAsset(
                source_path=target,
                object_name=target.relative_to(preview_dir).as_posix(),
                label=label,
                description=description,
                include_in_index=True,
            )
        )

    return assets


def _build_report_privacy_context(
    *,
    upload_enabled: bool,
    no_upload: bool,
    report_bucket: str | None,
    report_client_id: str | None,
    report_retention_days: int,
) -> dict[str, str]:
    """Build the privacy and retention disclosure shown at the top of the report."""
    if upload_enabled and report_bucket and report_client_id:
        presigned_days = min(report_retention_days, 7)
        return {
            "mode_label": "Operator bucket upload",
            "mode_variant": "primary",
            "storage_location": (
                "Artifacts are published to an operator-managed S3 bucket under "
                f"s3://{report_bucket}/{report_client_id}/<run-id>/."
            ),
            "access_model": (
                "The shared report and downloads use presigned URLs that expire after up to "
                f"{presigned_days} day(s)."
            ),
            "retention_policy": (
                "Older report prefixes in that bucket are deleted after "
                f"{report_retention_days} day(s)."
            ),
        }

    if no_upload:
        access_model = "Cloud upload was disabled for this run with --no-upload."
    else:
        access_model = "No S3 upload configuration was provided for this run."

    return {
        "mode_label": "Local-only output",
        "mode_variant": "default",
        "storage_location": "Artifacts stay in the local output and out directories only.",
        "access_model": access_model,
        "retention_policy": "Retention is local and manual because nothing was uploaded.",
    }


def handle_run(args: argparse.Namespace) -> int:
    """Handle the run subcommand."""
    file_config = load_config(args.config)
    resolved = merge_run_config(
        file_config,
        role_arn=args.role_arn,
        external_id=args.external_id,
        region=args.region,
        session_name=args.session_name,
        check_identity=args.check_identity,
        enable_coh=args.enable_coh,
        rate_limit_safe_mode=args.rate_limit_safe_mode,
        collect_ce_resource_daily=args.collect_ce_resource_daily,
        enable_ce_rightsizing_fallback=args.enable_ce_rightsizing_fallback,
        enable_ce_savings_plan_fallback=args.enable_ce_savings_plan_fallback,
        output_dir=args.output_dir,
        report_bucket=args.report_bucket,
        report_client_id=args.report_client_id,
        report_retention_days=args.report_retention_days,
    )
    if resolved.role_arn is None:
        raise RuntimeError("role_arn is required after config resolution.")
    region_coverage = _build_region_coverage(resolve_regions(resolved))
    upload_enabled = bool(
        not args.no_upload and resolved.report_bucket and resolved.report_client_id
    )
    privacy_context = _build_report_privacy_context(
        upload_enabled=upload_enabled,
        no_upload=args.no_upload,
        report_bucket=resolved.report_bucket,
        report_client_id=resolved.report_client_id,
        report_retention_days=resolved.report_retention_days,
    )

    session = assume_role_session(
        role_arn=resolved.role_arn,
        external_id=resolved.external_id,
        session_name=resolved.session_name,
        region_name=resolved.region,
    )

    print("Running finops-pack in AWS mode")
    print(f"role_arn={resolved.role_arn}")
    print(f"external_id={resolved.external_id}")
    print(f"region={resolved.region}")
    print(f"session_name={resolved.session_name}")
    print(f"enable_coh={resolved.enable_coh}")
    print(f"collect_ce_resource_daily={resolved.collect_ce_resource_daily}")
    print(f"enable_ce_rightsizing_fallback={resolved.enable_ce_rightsizing_fallback}")
    print(f"enable_ce_savings_plan_fallback={resolved.enable_ce_savings_plan_fallback}")
    print(f"rate_limit_safe_mode={resolved.rate_limit_safe_mode}")
    if upload_enabled and resolved.report_bucket and resolved.report_client_id:
        print(f"report_bucket={resolved.report_bucket}")
        print(f"report_client_id={resolved.report_client_id}")
        print(f"report_retention_days={resolved.report_retention_days}")
    elif args.no_upload:
        print("upload_enabled=False")
    _print_region_coverage(region_coverage)
    _print_schedule_config(resolved.schedule)

    caller_identity: dict[str, Any] | None = None
    if resolved.check_identity:
        sts = session.client("sts")
        caller_identity = sts.get_caller_identity()
        print(json.dumps(caller_identity, indent=2, default=str))

    if resolved.enable_coh:
        status = enable_cost_optimization_hub(
            session,
            region_name=resolved.region,
            rate_limit_safe_mode=resolved.rate_limit_safe_mode,
        )
        print(f"cost_optimization_hub_status={status}")

    access_report = _build_access_report(
        session,
        region_coverage=region_coverage,
        caller_identity=caller_identity,
    )
    _append_optional_fallback_modules(
        access_report,
        enable_ce_rightsizing_fallback=resolved.enable_ce_rightsizing_fallback,
        enable_ce_savings_plan_fallback=resolved.enable_ce_savings_plan_fallback,
    )
    output_dir = Path(resolved.output_dir)
    (
        spend_baseline_path,
        spend_baseline_snapshot,
        spend_baseline,
    ) = _collect_ce_spend_baseline(
        session,
        output_dir=output_dir,
        region_name=BILLING_CONTROL_PLANE_REGION,
        rate_limit_safe_mode=resolved.rate_limit_safe_mode,
    )
    resource_daily_path: Path | None = None
    resource_daily_snapshot: dict[str, Any] | None = None
    if resolved.collect_ce_resource_daily:
        resource_daily_path, resource_daily_snapshot = _collect_ce_resource_daily_snapshot(
            session,
            output_dir=output_dir,
            region_name=BILLING_CONTROL_PLANE_REGION,
            rate_limit_safe_mode=resolved.rate_limit_safe_mode,
        )
    (
        coh_summaries_path,
        coh_summaries_snapshot,
        coh_recommendations_path,
        coh_recommendations_snapshot,
    ) = _collect_coh_raw_snapshots(
        session,
        output_dir=output_dir,
        region_name=BILLING_CONTROL_PLANE_REGION,
        rate_limit_safe_mode=resolved.rate_limit_safe_mode,
    )
    (
        coh_normalized_path,
        coh_normalized_recommendations,
        coh_detail_errors,
    ) = _collect_coh_normalized_recommendations(
        session,
        recommendations_snapshot=coh_recommendations_snapshot,
        output_dir=output_dir,
        region_name=BILLING_CONTROL_PLANE_REGION,
        rate_limit_safe_mode=resolved.rate_limit_safe_mode,
    )
    coh_csv_export_path, coh_json_export_path = _export_coh_recommendations(
        coh_normalized_recommendations,
        output_dir=output_dir,
        resource_daily_snapshot=resource_daily_snapshot,
    )
    ce_rightsizing_path: Path | None = None
    ce_rightsizing_snapshot: dict[str, Any] | None = None
    if resolved.enable_ce_rightsizing_fallback:
        ce_rightsizing_path, ce_rightsizing_snapshot = _collect_ce_rightsizing_snapshot(
            session,
            output_dir=output_dir,
            region_name=BILLING_CONTROL_PLANE_REGION,
            rate_limit_safe_mode=resolved.rate_limit_safe_mode,
        )
    ce_savings_plan_path: Path | None = None
    ce_savings_plan_snapshot: dict[str, Any] | None = None
    if resolved.enable_ce_savings_plan_fallback:
        ce_savings_plan_path, ce_savings_plan_snapshot = _collect_ce_savings_plan_snapshot(
            session,
            output_dir=output_dir,
            region_name=BILLING_CONTROL_PLANE_REGION,
            rate_limit_safe_mode=resolved.rate_limit_safe_mode,
        )
    _merge_coh_collection_status(
        access_report,
        summaries_snapshot=coh_summaries_snapshot,
        recommendations_snapshot=coh_recommendations_snapshot,
        detail_errors=coh_detail_errors,
    )
    spend_baseline_error = spend_baseline_snapshot.get("error")
    if isinstance(spend_baseline_error, str) and spend_baseline_error:
        _merge_module_collection_status(
            access_report,
            module_id="cost_explorer",
            reasons=[spend_baseline_error],
        )
    if resource_daily_snapshot is not None:
        resource_daily_error = resource_daily_snapshot.get("error")
        if isinstance(resource_daily_error, str) and resource_daily_error:
            _merge_module_collection_status(
                access_report,
                module_id="resource_level_costs",
                reasons=[resource_daily_error],
            )
    if ce_rightsizing_snapshot is not None:
        rightsizing_error = ce_rightsizing_snapshot.get("error")
        if isinstance(rightsizing_error, str) and rightsizing_error:
            _merge_module_collection_status(
                access_report,
                module_id=CE_RIGHTSIZING_FALLBACK_MODULE_ID,
                reasons=[rightsizing_error],
            )
    if ce_savings_plan_snapshot is not None:
        savings_plan_error = ce_savings_plan_snapshot.get("error")
        if isinstance(savings_plan_error, str) and savings_plan_error:
            _merge_module_collection_status(
                access_report,
                module_id=CE_SAVINGS_PLAN_FALLBACK_MODULE_ID,
                reasons=[savings_plan_error],
            )
    _print_access_report(access_report)
    _print_ce_spend_summary(
        spend_baseline_path,
        spend_baseline_snapshot,
        spend_baseline,
    )
    if resource_daily_path is not None and resource_daily_snapshot is not None:
        _print_ce_resource_daily_summary(resource_daily_path, resource_daily_snapshot)
    _print_coh_collection_summary(
        coh_summaries_path,
        coh_summaries_snapshot,
        coh_recommendations_path,
        coh_recommendations_snapshot,
    )
    _print_coh_normalized_summary(
        coh_normalized_path,
        len(coh_normalized_recommendations),
        coh_detail_errors,
    )
    _print_coh_export_summary(coh_csv_export_path, coh_json_export_path)
    if ce_rightsizing_path is not None and ce_rightsizing_snapshot is not None:
        _print_ce_rightsizing_summary(ce_rightsizing_path, ce_rightsizing_snapshot)
    if ce_savings_plan_path is not None and ce_savings_plan_snapshot is not None:
        _print_ce_savings_plan_summary(ce_savings_plan_path, ce_savings_plan_snapshot)

    account_records, account_collection_error = _load_account_records_best_effort(
        session,
        current_account_id=access_report.account_id,
    )
    ec2_inventory_path, ec2_inventory_snapshot = _collect_ec2_inventory_snapshot(
        session,
        output_dir=output_dir,
        account_records=account_records,
        regions=region_coverage.regions,
        role_arn=resolved.role_arn,
        external_id=resolved.external_id,
        session_name=resolved.session_name,
        current_account_id=access_report.account_id,
    )
    schedule_recs_path, schedule_recommendations = _export_schedule_recommendations(
        ec2_inventory_snapshot,
        output_dir=output_dir,
        schedule=resolved.schedule,
        resource_daily_snapshot=resource_daily_snapshot,
    )
    if isinstance(account_collection_error, str) and account_collection_error:
        print(f"account_collection_error={account_collection_error}")
    _print_ec2_inventory_summary(ec2_inventory_path, ec2_inventory_snapshot)
    _print_schedule_recommendation_summary(schedule_recs_path, schedule_recommendations)
    account_map = classify_accounts(
        account_records,
        prod_account_ids=resolved.prod_account_ids,
        nonprod_account_ids=resolved.nonprod_account_ids,
    )
    account_output_label = (
        "AWS Organizations"
        if account_collection_error is None
        else access_report.account_id or "Current account"
    )
    summary_path = _write_summary_output(
        output_dir,
        region=resolved.region,
        schedule=resolved.schedule,
        rate_limit_safe_mode=resolved.rate_limit_safe_mode,
        account_map=account_map,
        access_report=access_report,
        ec2_inventory_snapshot=ec2_inventory_snapshot,
        schedule_recommendations=schedule_recommendations,
        ce_rightsizing_snapshot=ce_rightsizing_snapshot,
        ce_savings_plan_snapshot=ce_savings_plan_snapshot,
        spend_baseline=spend_baseline,
        resource_daily_snapshot=resource_daily_snapshot,
        summaries_snapshot=coh_summaries_snapshot,
        recommendations_snapshot=coh_recommendations_snapshot,
        normalized_recommendations=coh_normalized_recommendations,
        detail_errors=coh_detail_errors,
    )
    output_dashboard_path = output_dir / "dashboard.html"
    preview_dir = _artifact_output_dir(output_dir)
    preview_bundle_path = preview_dir / REPORT_BUNDLE_NAME
    output_download_targets = _build_dashboard_download_targets(
        accounts_path=output_dir / "accounts.json",
        access_report_path=output_dir / "access_report.json",
        summary_path=summary_path,
        bundle_path=preview_bundle_path,
        coh_csv_export_path=coh_csv_export_path,
        coh_json_export_path=coh_json_export_path,
        schedule_recs_path=schedule_recs_path,
    )
    output_download_links = build_dashboard_download_links(
        output_dashboard_path,
        output_download_targets,
    )
    accounts_path, access_report_path, dashboard_path = _write_account_outputs(
        account_map,
        output_dir=output_dir,
        region=resolved.region,
        account_id=account_output_label,
        access_report=access_report,
        spend_baseline=spend_baseline,
        spend_baseline_error=(
            spend_baseline_error if isinstance(spend_baseline_error, str) else None
        ),
        coh_summary=coh_summaries_snapshot,
        recommendations=coh_normalized_recommendations,
        schedule_recommendations=schedule_recommendations,
        privacy_context=privacy_context,
        download_links=output_download_links,
    )
    preview_download_targets = _build_dashboard_download_targets(
        accounts_path=preview_dir / "downloads" / "accounts.json",
        access_report_path=preview_dir / "downloads" / "access_report.json",
        summary_path=summary_path,
        bundle_path=preview_bundle_path,
        coh_csv_export_path=preview_dir / "downloads" / coh_csv_export_path.name,
        coh_json_export_path=preview_dir / "downloads" / coh_json_export_path.name,
        schedule_recs_path=schedule_recs_path,
    )
    preview_download_links = build_dashboard_download_links(
        preview_dir / "index.html",
        preview_download_targets,
    )
    preview_html = render_dashboard_html(
        account_map,
        account_id=account_output_label,
        region=resolved.region,
        access_report=access_report,
        spend_baseline=spend_baseline,
        spend_baseline_error=(
            spend_baseline_error if isinstance(spend_baseline_error, str) else None
        ),
        coh_summary=coh_summaries_snapshot,
        recommendations=coh_normalized_recommendations,
        schedule_recommendations=schedule_recommendations,
        privacy_context=privacy_context,
        download_links=preview_download_links,
    )
    preview_path = publish_preview_site(
        preview_dir=preview_dir,
        html=preview_html,
        stylesheet_source=dashboard_path.parent / "style.css",
        asset_copies=[
            (accounts_path, preview_dir / "downloads" / accounts_path.name),
            (access_report_path, preview_dir / "downloads" / access_report_path.name),
            (coh_csv_export_path, preview_dir / "downloads" / coh_csv_export_path.name),
            (coh_json_export_path, preview_dir / "downloads" / coh_json_export_path.name),
        ],
    )
    write_preview_bundle(
        preview_dir=preview_dir,
        destination=preview_bundle_path,
    )
    print(f"account_count={len(account_map)}")
    print(f"accounts_path={accounts_path}")
    print(f"access_report_path={access_report_path}")
    print(f"dashboard_path={dashboard_path}")
    print(f"summary_path={summary_path}")
    print(f"preview_path={preview_path}")
    print(f"preview_command=cd {quote(str(preview_dir))} && python -m http.server")
    if upload_enabled and resolved.report_bucket and resolved.report_client_id:
        published_report = publish_report_site_to_s3(
            session=session,
            bucket=resolved.report_bucket,
            client_id=resolved.report_client_id,
            retention_days=resolved.report_retention_days,
            preview_dir=preview_dir,
            assets=_build_s3_publish_assets(
                preview_dir=preview_dir,
                preview_download_targets=preview_download_targets,
            ),
            build_index_html=lambda download_links, stylesheet_path: render_dashboard_html(
                account_map,
                account_id=account_output_label,
                region=resolved.region,
                access_report=access_report,
                spend_baseline=spend_baseline,
                spend_baseline_error=(
                    spend_baseline_error if isinstance(spend_baseline_error, str) else None
                ),
                coh_summary=coh_summaries_snapshot,
                recommendations=coh_normalized_recommendations,
                schedule_recommendations=schedule_recommendations,
                privacy_context=privacy_context,
                download_links=download_links,
                stylesheet_path=stylesheet_path,
            ),
        )
        print(f"Report URL: {published_report.report_url}")

    return 0


def handle_demo(args: argparse.Namespace) -> int:
    """Handle the demo subcommand."""
    file_config = load_config(args.config)
    fixture_dir = Path(file_config.demo_fixture_dir)
    output_dir = Path(args.output_dir or file_config.output_dir)
    region_coverage = _build_region_coverage(resolve_regions(file_config))
    privacy_context = _build_report_privacy_context(
        upload_enabled=False,
        no_upload=True,
        report_bucket=file_config.report_bucket,
        report_client_id=file_config.report_client_id,
        report_retention_days=file_config.report_retention_days,
    )
    access_report = AccessReport(
        account_id="Demo Fixture",
        region_coverage=region_coverage,
    )
    account_records = load_account_records(fixture_dir / "accounts.json")
    account_map = classify_accounts(
        account_records,
        prod_account_ids=file_config.prod_account_ids,
        nonprod_account_ids=file_config.nonprod_account_ids,
    )
    summary_path = _write_summary_output(
        output_dir,
        region=file_config.region,
        schedule=file_config.schedule,
        rate_limit_safe_mode=file_config.rate_limit_safe_mode,
        account_map=account_map,
        access_report=access_report,
    )
    output_dashboard_path = output_dir / "dashboard.html"
    preview_dir = _artifact_output_dir(output_dir)
    preview_bundle_path = preview_dir / REPORT_BUNDLE_NAME
    output_download_links = build_dashboard_download_links(
        output_dashboard_path,
        _build_dashboard_download_targets(
            accounts_path=output_dir / "accounts.json",
            access_report_path=output_dir / "access_report.json",
            summary_path=summary_path,
            bundle_path=preview_bundle_path,
        ),
    )
    accounts_path, access_report_path, dashboard_path = _write_account_outputs(
        account_map,
        output_dir=output_dir,
        region=file_config.region,
        account_id="Demo Fixture",
        access_report=access_report,
        privacy_context=privacy_context,
        download_links=output_download_links,
    )
    preview_download_links = build_dashboard_download_links(
        preview_dir / "index.html",
        _build_dashboard_download_targets(
            accounts_path=preview_dir / "downloads" / "accounts.json",
            access_report_path=preview_dir / "downloads" / "access_report.json",
            summary_path=summary_path,
            bundle_path=preview_bundle_path,
        ),
    )
    preview_html = render_dashboard_html(
        account_map,
        account_id="Demo Fixture",
        region=file_config.region,
        access_report=access_report,
        privacy_context=privacy_context,
        download_links=preview_download_links,
    )
    preview_path = publish_preview_site(
        preview_dir=preview_dir,
        html=preview_html,
        stylesheet_source=dashboard_path.parent / "style.css",
        asset_copies=[
            (accounts_path, preview_dir / "downloads" / accounts_path.name),
            (access_report_path, preview_dir / "downloads" / access_report_path.name),
        ],
    )
    write_preview_bundle(
        preview_dir=preview_dir,
        destination=preview_bundle_path,
    )

    print("Running finops-pack in demo mode")
    print(f"fixture_dir={fixture_dir}")
    _print_region_coverage(region_coverage)
    _print_schedule_config(file_config.schedule)
    print(f"account_count={len(account_map)}")
    print(f"accounts_path={accounts_path}")
    print(f"access_report_path={access_report_path}")
    print(f"dashboard_path={dashboard_path}")
    print(f"summary_path={summary_path}")
    print(f"preview_path={preview_path}")
    print(f"preview_command=cd {quote(str(preview_dir))} && python -m http.server")

    return 0


def handle_iam_policy(args: argparse.Namespace) -> int:
    """Handle the iam-policy subcommand."""
    if args.output:
        output_path = write_policy(args.mode, args.output)
        print(f"wrote_policy={output_path}")
        return 0

    print(render_policy(args.mode), end="")
    return 0


def main() -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "run":
            return handle_run(args)
        if args.command == "demo":
            return handle_demo(args)
        if args.command == "iam-policy":
            return handle_iam_policy(args)

        parser.error(f"Unknown command: {args.command}")
        return 2
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
