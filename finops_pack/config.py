"""Configuration loader for finops_pack."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml  # type: ignore[import-untyped]

DEFAULT_CONFIG_FILES = ("config.yaml", "config.yml")
DEFAULT_BUSINESS_DAYS = ["mon", "tue", "wed", "thu", "fri"]
VALID_SCHEDULE_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


@dataclass
class BusinessHours:
    """Business-hours schedule window."""

    days: list[str] = field(default_factory=lambda: list(DEFAULT_BUSINESS_DAYS))
    start_hour: int = 9
    end_hour: int = 17

    def __post_init__(self) -> None:
        normalized_days = [day.strip().lower() for day in self.days]
        if not normalized_days:
            raise ValueError("schedule.business_hours.days must not be empty.")
        invalid_days = [day for day in normalized_days if day not in VALID_SCHEDULE_DAYS]
        if invalid_days:
            raise ValueError(
                "schedule.business_hours.days must only contain: "
                + ", ".join(sorted(VALID_SCHEDULE_DAYS))
            )
        if self.start_hour < 0 or self.start_hour > 23:
            raise ValueError("schedule.business_hours.start_hour must be between 0 and 23.")
        if self.end_hour < 1 or self.end_hour > 24:
            raise ValueError("schedule.business_hours.end_hour must be between 1 and 24.")
        if self.end_hour <= self.start_hour:
            raise ValueError("schedule.business_hours.end_hour must be greater than start_hour.")
        self.days = normalized_days


@dataclass
class ScheduleConfig:
    """Schedule configuration for business-hours-aware workflows."""

    timezone: str = "UTC"
    business_hours: BusinessHours = field(default_factory=BusinessHours)

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"schedule.timezone is not a valid IANA timezone: {self.timezone}"
            ) from exc


@dataclass
class AppConfig:
    """Application configuration."""

    role_arn: str | None = None
    external_id: str | None = None
    region: str = "us-east-1"
    regions: list[str] = field(default_factory=list)
    session_name: str = "finops-pack"
    check_identity: bool = False
    enable_coh: bool = False
    collect_ce_resource_daily: bool = False
    rate_limit_safe_mode: bool = False
    output_dir: str = "output"
    demo_fixture_dir: str = "demo/fixtures"
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    prod_account_ids: list[str] = field(default_factory=list)
    nonprod_account_ids: list[str] = field(default_factory=list)


def _normalize_string_list(value: Any, *, key: str) -> list[str]:
    """Normalize config values that must be lists of strings."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")
    return value


def _normalize_region_list(value: Any) -> list[str]:
    """Normalize configured region coverage values."""
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("regions must be a list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_region in value:
        region = raw_region.strip()
        if not region:
            raise ValueError("regions must not contain empty values.")
        if region in seen:
            continue
        seen.add(region)
        normalized.append(region)

    return normalized


def _normalize_business_hours(value: Any) -> BusinessHours:
    """Normalize configured business-hours values."""
    if value is None:
        return BusinessHours()
    if not isinstance(value, dict):
        raise ValueError("schedule.business_hours must be a mapping/object.")

    allowed = {"days", "start_hour", "end_hour"}
    unknown = set(value.keys()) - allowed
    if unknown:
        raise ValueError("Unknown schedule.business_hours key(s): " + ", ".join(sorted(unknown)))

    raw_days = value.get("days")
    if raw_days is None:
        days = list(DEFAULT_BUSINESS_DAYS)
    elif not isinstance(raw_days, list) or any(not isinstance(item, str) for item in raw_days):
        raise ValueError("schedule.business_hours.days must be a list of strings.")
    else:
        days = raw_days

    start_hour = value.get("start_hour", 9)
    if not isinstance(start_hour, int) or isinstance(start_hour, bool):
        raise ValueError("schedule.business_hours.start_hour must be an integer.")

    end_hour = value.get("end_hour", 17)
    if not isinstance(end_hour, int) or isinstance(end_hour, bool):
        raise ValueError("schedule.business_hours.end_hour must be an integer.")

    return BusinessHours(
        days=days,
        start_hour=start_hour,
        end_hour=end_hour,
    )


def _normalize_schedule(value: Any) -> ScheduleConfig:
    """Normalize configured schedule values."""
    if value is None:
        return ScheduleConfig()
    if not isinstance(value, dict):
        raise ValueError("schedule must be a mapping/object.")

    allowed = {"timezone", "business_hours"}
    unknown = set(value.keys()) - allowed
    if unknown:
        raise ValueError("Unknown schedule key(s): " + ", ".join(sorted(unknown)))

    timezone = value.get("timezone", "UTC")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("schedule.timezone must be a non-empty string.")

    return ScheduleConfig(
        timezone=timezone.strip(),
        business_hours=_normalize_business_hours(value.get("business_hours")),
    )


def _merge_regions(primary_region: str, configured_regions: list[str]) -> list[str]:
    """Return an ordered, de-duplicated region coverage list."""
    regions: list[str] = []
    seen: set[str] = set()

    for region in [primary_region, *configured_regions]:
        if not region or region in seen:
            continue
        seen.add(region)
        regions.append(region)

    return regions


def _validate_config(config: AppConfig) -> AppConfig:
    """Validate merged configuration values."""
    overlaps = set(config.prod_account_ids) & set(config.nonprod_account_ids)
    if overlaps:
        raise ValueError(
            "prod_account_ids and nonprod_account_ids cannot overlap: "
            + ", ".join(sorted(overlaps))
        )
    if not config.region:
        raise ValueError("region must not be empty.")
    if config.regions and config.region not in config.regions:
        raise ValueError("region must be included in regions when regions is set.")
    if not config.output_dir:
        raise ValueError("output_dir must not be empty.")
    return config


def _normalize_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Validate top-level config keys."""
    allowed = {
        "role_arn",
        "external_id",
        "region",
        "regions",
        "session_name",
        "check_identity",
        "enable_coh",
        "collect_ce_resource_daily",
        "rate_limit_safe_mode",
        "output_dir",
        "demo_fixture_dir",
        "schedule",
        "prod_account_ids",
        "nonprod_account_ids",
    }
    unknown = set(data.keys()) - allowed
    if unknown:
        raise ValueError(f"Unknown config key(s): {', '.join(sorted(unknown))}")

    normalized = dict(data)
    normalized["prod_account_ids"] = _normalize_string_list(
        normalized.get("prod_account_ids"),
        key="prod_account_ids",
    )
    normalized["nonprod_account_ids"] = _normalize_string_list(
        normalized.get("nonprod_account_ids"),
        key="nonprod_account_ids",
    )
    normalized["regions"] = _normalize_region_list(normalized.get("regions"))
    normalized["schedule"] = _normalize_schedule(normalized.get("schedule"))
    return normalized


def find_config_file(explicit_path: str | None = None) -> Path | None:
    """Find an optional config file."""
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    for name in DEFAULT_CONFIG_FILES:
        path = Path(name)
        if path.exists():
            return path

    return None


def load_config(explicit_path: str | None = None) -> AppConfig:
    """Load config from YAML if present, otherwise return defaults."""
    config_path = find_config_file(explicit_path)

    if config_path is None:
        return _validate_config(AppConfig())

    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping/object at the top level.")

    normalized = _normalize_keys(raw)
    return _validate_config(AppConfig(**normalized))


def merge_run_config(
    file_config: AppConfig,
    *,
    role_arn: str | None,
    external_id: str | None,
    region: str | None,
    session_name: str | None,
    check_identity: bool,
    enable_coh: bool,
    rate_limit_safe_mode: bool,
    collect_ce_resource_daily: bool,
    output_dir: str | None,
) -> AppConfig:
    """Merge CLI args over file config for the run command."""
    merged_region = region if region is not None else file_config.region
    merged_regions = file_config.regions
    if region is not None and file_config.regions:
        merged_regions = _merge_regions(region, file_config.regions)

    merged = _validate_config(
        AppConfig(
            role_arn=role_arn if role_arn is not None else file_config.role_arn,
            external_id=external_id if external_id is not None else file_config.external_id,
            region=merged_region,
            regions=merged_regions,
            session_name=session_name if session_name is not None else file_config.session_name,
            check_identity=check_identity or file_config.check_identity,
            enable_coh=enable_coh or file_config.enable_coh,
            collect_ce_resource_daily=(
                collect_ce_resource_daily or file_config.collect_ce_resource_daily
            ),
            rate_limit_safe_mode=rate_limit_safe_mode or file_config.rate_limit_safe_mode,
            output_dir=output_dir if output_dir is not None else file_config.output_dir,
            demo_fixture_dir=file_config.demo_fixture_dir,
            schedule=file_config.schedule,
            prod_account_ids=file_config.prod_account_ids,
            nonprod_account_ids=file_config.nonprod_account_ids,
        )
    )

    if not merged.role_arn:
        raise ValueError("role_arn is required. Pass --role-arn or set role_arn in config.yaml.")

    return merged


def resolve_regions(config: AppConfig) -> list[str]:
    """Resolve the ordered list of regions covered by this run."""
    if config.regions:
        return _merge_regions(config.region, config.regions)
    return [config.region]
