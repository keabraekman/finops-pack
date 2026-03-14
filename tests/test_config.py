from pathlib import Path

import pytest

from finops_pack.config import AppConfig, load_config, merge_run_config


def test_load_config_returns_defaults_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = load_config()

    assert cfg.role_arn is None
    assert cfg.external_id is None
    assert cfg.region == "us-east-1"
    assert cfg.session_name == "finops-pack"
    assert cfg.check_identity is False
    assert cfg.enable_coh is False
    assert cfg.demo_fixture_dir == "demo/fixtures"


def test_load_config_from_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                "role_arn: arn:aws:iam::123456789012:role/TestRole",
                "external_id: abc123",
                "region: us-west-2",
                "session_name: test-session",
                "check_identity: true",
                "enable_coh: true",
                "demo_fixture_dir: demo/fixtures",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(str(config_file))

    assert cfg.role_arn == "arn:aws:iam::123456789012:role/TestRole"
    assert cfg.external_id == "abc123"
    assert cfg.region == "us-west-2"
    assert cfg.session_name == "test-session"
    assert cfg.check_identity is True
    assert cfg.enable_coh is True
    assert cfg.demo_fixture_dir == "demo/fixtures"


def test_merge_run_config_prefers_cli_values() -> None:
    file_cfg = AppConfig(
        role_arn="arn:from:file",
        external_id="file-external-id",
        region="us-west-2",
        session_name="from-file",
        check_identity=False,
        enable_coh=False,
        demo_fixture_dir="demo/fixtures",
    )

    merged = merge_run_config(
        file_cfg,
        role_arn="arn:from:cli",
        external_id="cli-external-id",
        region="eu-west-1",
        session_name="from-cli",
        check_identity=True,
        enable_coh=True,
    )

    assert merged.role_arn == "arn:from:cli"
    assert merged.external_id == "cli-external-id"
    assert merged.region == "eu-west-1"
    assert merged.session_name == "from-cli"
    assert merged.check_identity is True
    assert merged.enable_coh is True


def test_merge_run_config_requires_role_arn() -> None:
    file_cfg = AppConfig()

    with pytest.raises(ValueError, match="role_arn is required"):
        merge_run_config(
            file_cfg,
            role_arn=None,
            external_id=None,
            region=None,
            session_name=None,
            check_identity=False,
            enable_coh=False,
        )
