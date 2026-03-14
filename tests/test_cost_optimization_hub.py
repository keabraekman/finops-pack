from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from finops_pack.aws.cost_optimization_hub import enable_cost_optimization_hub


def test_enable_cost_optimization_hub_updates_enrollment_status() -> None:
    client = Mock()
    client.update_enrollment_status.return_value = {"status": "Active"}
    session = Mock()
    session.client.return_value = client

    status = enable_cost_optimization_hub(session, region_name="us-west-2")

    assert status == "Active"
    session.client.assert_called_once_with("cost-optimization-hub", region_name="us-west-2")
    client.update_enrollment_status.assert_called_once_with(status="Active")


def test_enable_cost_optimization_hub_wraps_client_errors() -> None:
    client = Mock()
    client.update_enrollment_status.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "UpdateEnrollmentStatus",
    )
    session = Mock()
    session.client.return_value = client

    with pytest.raises(RuntimeError, match="Failed to update Cost Optimization Hub"):
        enable_cost_optimization_hub(session)
