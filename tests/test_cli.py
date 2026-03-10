import subprocess
import sys


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