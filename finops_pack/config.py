"""Configuration loader for finops_pack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_CONFIG_FILES = ("config.yaml", "config.yml")


@dataclass
class AppConfig:
    """Application configuration."""

    role_arn: str | None = None
    external_id: str | None = None
    region: str = "us-east-1"
    session_name: str = "finops-pack"
    check_identity: bool = False
    enable_coh: bool = False
    demo_fixture_dir: str = "demo/fixtures"


def _normalize_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Validate top-level config keys."""
    allowed = {
        "role_arn",
        "external_id",
        "region",
        "session_name",
        "check_identity",
        "enable_coh",
        "demo_fixture_dir",
    }
    unknown = set(data.keys()) - allowed
    if unknown:
        raise ValueError(f"Unknown config key(s): {', '.join(sorted(unknown))}")
    return data


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
        return AppConfig()

    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping/object at the top level.")

    normalized = _normalize_keys(raw)
    return AppConfig(**normalized)


def merge_run_config(
    file_config: AppConfig,
    *,
    role_arn: str | None,
    external_id: str | None,
    region: str | None,
    session_name: str | None,
    check_identity: bool,
    enable_coh: bool,
) -> AppConfig:
    """Merge CLI args over file config for the run command."""
    merged = AppConfig(
        role_arn=role_arn if role_arn is not None else file_config.role_arn,
        external_id=external_id if external_id is not None else file_config.external_id,
        region=region if region is not None else file_config.region,
        session_name=session_name if session_name is not None else file_config.session_name,
        check_identity=check_identity or file_config.check_identity,
        enable_coh=enable_coh or file_config.enable_coh,
        demo_fixture_dir=file_config.demo_fixture_dir,
    )

    if not merged.role_arn:
        raise ValueError(
            "role_arn is required. Pass --role-arn or set role_arn in config.yaml."
        )

    return merged
