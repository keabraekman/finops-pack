from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

import finops_pack.aws.assume_role as assume_role_module


def test_assume_role_uses_sts_and_returns_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sts_client = Mock()
    sts_client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "access-key",
            "SecretAccessKey": "secret-key",
            "SessionToken": "session-token",
        }
    }
    boto3_client = Mock(return_value=sts_client)

    monkeypatch.setattr(assume_role_module.boto3, "client", boto3_client)

    credentials = assume_role_module.assume_role(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="us-west-2",
    )

    boto3_client.assert_called_once_with("sts", region_name="us-west-2")
    sts_client.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123456789012:role/TestRole",
        RoleSessionName="test-session",
        ExternalId="external-id",
    )
    assert credentials == {
        "aws_access_key_id": "access-key",
        "aws_secret_access_key": "secret-key",
        "aws_session_token": "session-token",
    }


def test_assume_role_session_builds_boto3_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = object()
    session_ctor = Mock(return_value=fake_session)
    assume_role = Mock(
        return_value={
            "aws_access_key_id": "access-key",
            "aws_secret_access_key": "secret-key",
            "aws_session_token": "session-token",
        }
    )

    monkeypatch.setattr(assume_role_module, "assume_role", assume_role)
    monkeypatch.setattr(assume_role_module.boto3, "Session", session_ctor)

    session = assume_role_module.assume_role_session(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="eu-west-1",
    )

    assert session is fake_session
    assume_role.assert_called_once_with(
        role_arn="arn:aws:iam::123456789012:role/TestRole",
        external_id="external-id",
        session_name="test-session",
        region_name="eu-west-1",
    )
    session_ctor.assert_called_once_with(
        aws_access_key_id="access-key",
        aws_secret_access_key="secret-key",
        aws_session_token="session-token",
        region_name="eu-west-1",
    )


def test_assume_role_wraps_sts_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "AssumeRole",
    )
    sts_client = Mock()
    sts_client.assume_role.side_effect = error

    monkeypatch.setattr(assume_role_module.boto3, "client", Mock(return_value=sts_client))

    with pytest.raises(RuntimeError, match="Failed to assume role"):
        assume_role_module.assume_role("arn:aws:iam::123456789012:role/TestRole")
