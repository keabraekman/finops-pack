from pathlib import Path
from typing import Any

import pytest

from finops_pack.iam_policy_generator import (
    PolicyMode,
    generate_policy,
    policy_template_path,
    render_policy,
    write_policy,
)


def _collect_actions(policy: dict[str, Any]) -> set[str]:
    return {
        action
        for statement in policy["Statement"]
        for action in statement["Action"]
    }


@pytest.mark.parametrize("mode", ["min", "full"])
def test_policy_template_snapshots_match_generated_output(mode: PolicyMode) -> None:
    assert policy_template_path(mode).read_text(encoding="utf-8") == render_policy(mode)


def test_generate_policy_full_adds_optional_permissions() -> None:
    min_actions = _collect_actions(generate_policy("min"))
    full_actions = _collect_actions(generate_policy("full"))

    assert "cost-optimization-hub:UpdateEnrollmentStatus" not in min_actions
    assert "cost-optimization-hub:UpdateEnrollmentStatus" in full_actions
    assert "iam:CreateServiceLinkedRole" in full_actions
    assert "iam:PutRolePolicy" in full_actions


def test_write_policy_writes_requested_mode(tmp_path: Path) -> None:
    output_path = tmp_path / "generated-policy.json"

    written_path = write_policy("full", output_path)

    assert written_path == output_path
    assert output_path.read_text(encoding="utf-8") == render_policy("full")
