import argparse
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

import finops_pack.cli as cli


def test_demo_command_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "finops_pack", "demo"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Running finops-pack in demo mode" in result.stdout


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
    actions = {
        action
        for statement in policy["Statement"]
        for action in statement["Action"]
    }
    assert "cost-optimization-hub:UpdateEnrollmentStatus" in actions


def test_handle_run_enables_cost_optimization_hub(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = Mock()
    assume_role_session = Mock(return_value=session)
    enable_coh = Mock(return_value="Active")

    monkeypatch.setattr(cli, "assume_role_session", assume_role_session)
    monkeypatch.setattr(cli, "enable_cost_optimization_hub", enable_coh)

    args = argparse.Namespace(
        command="run",
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        region="us-west-2",
        session_name="test-session",
        check_identity=False,
        enable_coh=True,
        config=None,
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
    assert "enable_coh=True" in capsys.readouterr().out
