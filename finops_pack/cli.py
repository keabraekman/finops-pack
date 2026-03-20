"""CLI entry point for finops_pack."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from finops_pack.analyzers.account_classification import classify_accounts
from finops_pack.aws.assume_role import assume_role_session
from finops_pack.aws.cost_optimization_hub import (
    enable_cost_optimization_hub,
    list_recommendation_summaries,
    list_recommendations,
)
from finops_pack.collectors.organizations import list_accounts, load_account_records
from finops_pack.config import load_config, merge_run_config, resolve_regions
from finops_pack.iam_policy_generator import render_policy, write_policy
from finops_pack.models import (
    AccessCheck,
    AccessReport,
    AccountMapEntry,
    ModuleStatus,
    RegionCoverage,
)
from finops_pack.render.dashboard import write_dashboard
from finops_pack.render.exporters import JsonExporter

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


def _build_region_coverage(resolved_regions: list[str]) -> RegionCoverage:
    """Create the region coverage payload for this run."""
    if not resolved_regions:
        resolved_regions = [BILLING_CONTROL_PLANE_REGION]
    return RegionCoverage(
        strategy="fixed",
        primary_region=resolved_regions[0],
        regions=resolved_regions,
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
                "for the last 14 days."
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


def _print_access_report(access_report: AccessReport) -> None:
    """Emit access report details to stdout."""
    check_map = {check.check_id: check for check in access_report.checks}
    print(f"coh_enabled={_format_enabled(check_map['cost_optimization_hub'].enabled)}")
    print(f"ce_enabled={_format_enabled(check_map['cost_explorer'].enabled)}")
    print(f"resource_level_enabled={_format_enabled(check_map['resource_level_costs'].enabled)}")
    for module in access_report.modules:
        print(f"module_{module.module_id}={module.status}: {module.reason}")


def _raw_output_dir(output_dir: Path) -> Path:
    """Return the raw snapshot directory rooted alongside the configured output dir."""
    raw_root = output_dir if output_dir.name == "out" else output_dir.parent / "out"
    return raw_root / "raw"


def _write_json_snapshot(destination: Path, payload: dict[str, Any]) -> Path:
    """Write snapshot JSON with stable formatting."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return destination


def _collect_coh_raw_snapshots(
    session: Any,
    *,
    output_dir: Path,
    region_name: str = BILLING_CONTROL_PLANE_REGION,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    """Collect and persist raw Cost Optimization Hub summary and recommendation payloads."""
    raw_dir = _raw_output_dir(output_dir)
    summaries_path = raw_dir / "coh_summaries.json"
    recommendations_path = raw_dir / "coh_recommendations.json"

    try:
        summaries_snapshot = list_recommendation_summaries(session, region_name=region_name)
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
        recommendations_snapshot = list_recommendations(session, region_name=region_name)
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
) -> None:
    """Mark the COH module degraded when the collector itself is blocked."""
    module = next(
        (item for item in access_report.modules if item.module_id == "cost_optimization_hub"),
        None,
    )
    if module is None:
        return

    collector_reasons = [
        message
        for message in (
            summaries_snapshot.get("error"),
            recommendations_snapshot.get("error"),
        )
        if isinstance(message, str) and message
    ]
    if not collector_reasons:
        return

    module.status = "DEGRADED"
    reasons = [module.reason, *collector_reasons]
    module.reason = "; ".join(dict.fromkeys(reasons))


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
        "--enable-coh",
        action="store_true",
        help=(
            "Enable Cost Optimization Hub in the target account. Requires extra IAM permissions."
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
    )

    return accounts_path, access_report_path, dashboard_path


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
        output_dir=args.output_dir,
    )
    if resolved.role_arn is None:
        raise RuntimeError("role_arn is required after config resolution.")
    region_coverage = _build_region_coverage(resolve_regions(resolved))

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
    _print_region_coverage(region_coverage)

    caller_identity: dict[str, Any] | None = None
    if resolved.check_identity:
        sts = session.client("sts")
        caller_identity = sts.get_caller_identity()
        print(json.dumps(caller_identity, indent=2, default=str))

    if resolved.enable_coh:
        status = enable_cost_optimization_hub(session, region_name=resolved.region)
        print(f"cost_optimization_hub_status={status}")

    access_report = _build_access_report(
        session,
        region_coverage=region_coverage,
        caller_identity=caller_identity,
    )
    output_dir = Path(resolved.output_dir)
    (
        coh_summaries_path,
        coh_summaries_snapshot,
        coh_recommendations_path,
        coh_recommendations_snapshot,
    ) = _collect_coh_raw_snapshots(
        session,
        output_dir=output_dir,
        region_name=BILLING_CONTROL_PLANE_REGION,
    )
    _merge_coh_collection_status(
        access_report,
        summaries_snapshot=coh_summaries_snapshot,
        recommendations_snapshot=coh_recommendations_snapshot,
    )
    _print_access_report(access_report)
    _print_coh_collection_summary(
        coh_summaries_path,
        coh_summaries_snapshot,
        coh_recommendations_path,
        coh_recommendations_snapshot,
    )

    account_records = list_accounts(session)
    account_map = classify_accounts(
        account_records,
        prod_account_ids=resolved.prod_account_ids,
        nonprod_account_ids=resolved.nonprod_account_ids,
    )
    accounts_path, access_report_path, dashboard_path = _write_account_outputs(
        account_map,
        output_dir=output_dir,
        region=resolved.region,
        account_id="AWS Organizations",
        access_report=access_report,
    )
    print(f"account_count={len(account_map)}")
    print(f"accounts_path={accounts_path}")
    print(f"access_report_path={access_report_path}")
    print(f"dashboard_path={dashboard_path}")

    return 0


def handle_demo(args: argparse.Namespace) -> int:
    """Handle the demo subcommand."""
    file_config = load_config(args.config)
    fixture_dir = Path(file_config.demo_fixture_dir)
    output_dir = Path(args.output_dir or file_config.output_dir)
    region_coverage = _build_region_coverage(resolve_regions(file_config))
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
    accounts_path, access_report_path, dashboard_path = _write_account_outputs(
        account_map,
        output_dir=output_dir,
        region=file_config.region,
        account_id="Demo Fixture",
        access_report=access_report,
    )

    print("Running finops-pack in demo mode")
    print(f"fixture_dir={fixture_dir}")
    _print_region_coverage(region_coverage)
    print(f"account_count={len(account_map)}")
    print(f"accounts_path={accounts_path}")
    print(f"access_report_path={access_report_path}")
    print(f"dashboard_path={dashboard_path}")

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
