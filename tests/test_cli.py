import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import finops_pack.cli as cli
from finops_pack.models import AccountRecord


def test_demo_command_runs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "demo-output"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "\n".join(
            [
                f"demo_fixture_dir: {repo_root / 'demo/fixtures'}",
                f"output_dir: {output_dir}",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "demo", "--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert "Running finops-pack in demo mode" in result.stdout
    assert (output_dir / "accounts.json").exists()
    assert (output_dir / "dashboard.html").exists()


def test_run_requires_role_arn() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "run"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "role_arn is required" in result.stdout


def test_iam_policy_command_prints_policy_json() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "iam-policy", "--mode", "full"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    policy = json.loads(result.stdout)
    actions = {action for statement in policy["Statement"] for action in statement["Action"]}
    assert "cost-optimization-hub:UpdateEnrollmentStatus" in actions


def test_handle_run_enables_cost_optimization_hub(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    session = Mock()
    assume_role_session = Mock(return_value=session)
    enable_coh = Mock(return_value="Active")
    list_accounts = Mock(
        return_value=[
            AccountRecord(
                account_id="123456789012",
                name="prod-core",
                email="prod-core@example.com",
                status="ACTIVE",
            )
        ]
    )

    monkeypatch.setattr(cli, "assume_role_session", assume_role_session)
    monkeypatch.setattr(cli, "enable_cost_optimization_hub", enable_coh)
    monkeypatch.setattr(cli, "list_accounts", list_accounts)

    args = argparse.Namespace(
        command="run",
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        region="us-west-2",
        session_name="test-session",
        check_identity=False,
        enable_coh=True,
        config=None,
        output_dir=str(tmp_path / "output"),
    )

    result = cli.handle_run(args)

    assert result == 0
    assume_role_session.assert_called_once_with(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="us-west-2",
    )
    enable_coh.assert_called_once_with(session, region_name="us-west-2")
    list_accounts.assert_called_once_with(session)

    output = capsys.readouterr().out
    assert "enable_coh=True" in output
    assert "account_count=1" in output

    accounts = json.loads((tmp_path / "output" / "accounts.json").read_text(encoding="utf-8"))
    assert accounts[0]["environment"] == "prod"
    assert accounts[0]["classification_source"] == "regex"
    dashboard_html = (tmp_path / "output" / "dashboard.html").read_text(encoding="utf-8")
    assert "Account Map" in dashboard_html
    assert "prod-core" in dashboard_html
