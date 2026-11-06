"""Tests for actionable backend state-store failures.

When the Onboarding_State DynamoDB table is unusable (deleted, access revoked,
KMS key unusable, unreachable), every tool must return a structured
``internal_error`` Tool_Result that tells the calling agent what is wrong with
the backend infrastructure and whether retrying can help - never let the
exception escape (which the MCP SDK reports to the agent only as a bare
"Error executing tool <name>").
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from quick_admin_buddy_mcp import server
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ResultStatus, Suite
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.state import (
    DynamoDbStateRepository,
    InMemoryStateRepository,
    StateStoreError,
)

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover - moto is a dev dependency
    mock_aws = None

_TABLE = "OnboardingState"
_REGION = "eu-west-1"
_TENANT = "acme-corp"

CUSTOM_OAUTH = {
    "client_id": "cid-123",
    "client_secret": "super-secret-value",
    "domain_url": "https://graph.example.com",
    "authorization_url": "https://auth.example.com/authorize",
    "token_url": "https://auth.example.com/token",
    "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
}


def _missing_table_error(operation: str = "write") -> StateStoreError:
    return StateStoreError(
        StateStoreError.REASON_TABLE_NOT_FOUND,
        table_name=_TABLE,
        region=_REGION,
        operation=operation,
        error_code="ResourceNotFoundException",
    )


# ---------------------------------------------------------------------------
# Repository: AWS failures are mapped to StateStoreError
# ---------------------------------------------------------------------------


@pytest.mark.skipif(mock_aws is None, reason="moto not installed")
def test_missing_table_raises_table_not_found_on_read_and_write() -> None:
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=_REGION)
        repo = DynamoDbStateRepository(_TABLE, resource=resource)  # table never created

        with pytest.raises(StateStoreError) as read_err:
            repo.get(_TENANT)
        with pytest.raises(StateStoreError) as write_err:
            repo.put_suite(_TENANT, Suite.MICROSOFT_365)

    for err, operation in ((read_err.value, "read"), (write_err.value, "write")):
        assert err.reason == StateStoreError.REASON_TABLE_NOT_FOUND
        assert err.error_code == "ResourceNotFoundException"
        assert err.table_name == _TABLE
        assert err.region == _REGION
        assert err.operation == operation
        assert err.retryable is False


class _RaisingTable:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_item(self, **_: Any) -> dict:
        raise self._exc

    def update_item(self, **_: Any) -> dict:
        raise self._exc


class _Resource:
    def __init__(self, table: _RaisingTable) -> None:
        self._table = table

    def Table(self, _name: str) -> _RaisingTable:  # noqa: N802 - boto3 API name
        return self._table


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "GetItem")


@pytest.mark.parametrize(
    ("exc", "reason", "retryable"),
    [
        (
            _client_error("AccessDeniedException"),
            StateStoreError.REASON_ACCESS_DENIED,
            False,
        ),
        (
            _client_error("AccessDeniedException", "KMS key access denied"),
            StateStoreError.REASON_ENCRYPTION_KEY_UNAVAILABLE,
            False,
        ),
        (
            _client_error("KMSDisabledException"),
            StateStoreError.REASON_ENCRYPTION_KEY_UNAVAILABLE,
            False,
        ),
        (
            _client_error("ProvisionedThroughputExceededException"),
            StateStoreError.REASON_UNAVAILABLE,
            True,
        ),
        (
            EndpointConnectionError(endpoint_url="https://dynamodb"),
            StateStoreError.REASON_UNAVAILABLE,
            True,
        ),
    ],
)
def test_repository_classifies_failures(
    exc: Exception, reason: str, retryable: bool
) -> None:
    repo = DynamoDbStateRepository(_TABLE, resource=_Resource(_RaisingTable(exc)))

    with pytest.raises(StateStoreError) as err:
        repo.get(_TENANT)

    assert err.value.reason == reason
    assert err.value.retryable is retryable
    assert err.value.table_name == _TABLE


# ---------------------------------------------------------------------------
# Service: state-store failures become actionable internal_error results
# ---------------------------------------------------------------------------


class _BrokenState(InMemoryStateRepository):
    """An in-memory repository whose reads and/or writes fail like a missing table."""

    def __init__(self, fail_reads: bool = True, fail_writes: bool = True) -> None:
        super().__init__()
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes

    def get(self, tenant):
        if self.fail_reads:
            raise _missing_table_error("read")
        return super().get(tenant)

    def put_suite(self, tenant, suite):
        if self.fail_writes:
            raise _missing_table_error()
        return super().put_suite(tenant, suite)

    def set_connector_status(self, tenant, key, status, principal):
        if self.fail_writes:
            raise _missing_table_error()
        return super().set_connector_status(tenant, key, status, principal)

    def set_extension_status(self, tenant, key, status, principal):
        if self.fail_writes:
            raise _missing_table_error()
        return super().set_extension_status(tenant, key, status, principal)


def _service(state) -> tuple[OnboardingService, MockQuickAdminClient]:
    quick = MockQuickAdminClient()
    return OnboardingService(ConnectorCatalog(), state, quick), quick


def _assert_missing_table_result(result, status=ResultStatus.INTERNAL_ERROR) -> None:
    assert result.status == status
    assert f"DynamoDB table {_TABLE!r}" in result.message
    assert "does not exist" in result.message
    assert "retrying will not help" in result.message
    assert "CloudFormation stack" in result.message
    detail = result.data["state_store_error"]
    assert detail == {
        "error_type": "state_store_unavailable",
        "reason": "table_not_found",
        "table_name": _TABLE,
        "region": _REGION,
        "error_code": "ResourceNotFoundException",
        "operation": detail["operation"],
        "retryable": False,
    }


def test_set_suite_reports_missing_table_on_read() -> None:
    service, _ = _service(_BrokenState())

    result = service.set_suite(_TENANT, "microsoft_365")

    _assert_missing_table_result(result)
    assert "'microsoft_365' was NOT recorded" in result.message
    assert result.data["requested_suite"] == "microsoft_365"


def test_set_suite_reports_missing_table_on_write() -> None:
    service, _ = _service(_BrokenState(fail_reads=False))

    result = service.set_suite(_TENANT, "microsoft_365")

    _assert_missing_table_result(result)
    assert result.data["state_store_error"]["operation"] == "write"


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.get_configuration(_TENANT),
        lambda s: s.get_summary(_TENANT),
        lambda s: s.list_extensions(_TENANT),
        lambda s: s.set_extension_status(_TENANT, "outlook_addin", "installed"),
        lambda s: s.disable_action_connector(_TENANT, "outlook"),
        lambda s: s.enable_action_connector(
            _TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ),
    ],
)
def test_every_state_reading_tool_reports_missing_table(call) -> None:
    service, quick = _service(_BrokenState())

    result = call(service)

    _assert_missing_table_result(result)
    # No Quick resource is touched when the state cannot even be read.
    assert quick.calls == []


def test_enable_reports_created_resource_when_state_write_fails() -> None:
    state = _BrokenState(fail_reads=False)
    state.fail_writes = False
    state.put_suite(_TENANT, Suite.MICROSOFT_365)
    state.fail_writes = True
    service, quick = _service(state)

    result = service.enable_action_connector(
        _TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
    )

    # A Quick API call already succeeded, so this is the documented
    # post-API persistence failure (server_error), not "nothing was created".
    _assert_missing_table_result(result, ResultStatus.SERVER_ERROR)
    assert quick.calls_for("create_action_connector")
    assert "WAS created in AWS Quick" in result.message
    # The resolved client secret never leaks into the result.
    assert "super-secret-value" not in result.message


def test_disable_reports_deleted_resource_when_state_write_fails() -> None:
    state = _BrokenState(fail_reads=False, fail_writes=False)
    service, quick = _service(state)
    state.put_suite(_TENANT, Suite.MICROSOFT_365)
    assert (
        service.enable_action_connector(
            _TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ).status
        == ResultStatus.SUCCESS
    )
    state.fail_writes = True

    result = service.disable_action_connector(_TENANT, "outlook")

    _assert_missing_table_result(result, ResultStatus.SERVER_ERROR)
    assert quick.calls_for("delete_action_connector")
    assert "WAS deleted in AWS Quick" in result.message


# ---------------------------------------------------------------------------
# Tool layer: the agent receives a structured dict, not an exception
# ---------------------------------------------------------------------------


def test_set_productivity_suite_tool_returns_structured_error(monkeypatch) -> None:
    service, _ = _service(_BrokenState())
    monkeypatch.setenv(server._TENANT_ENV, _TENANT)
    server.set_service(service)
    try:
        result = server._set_productivity_suite_impl("microsoft_365", None)
    finally:
        server.set_service(None)

    assert result["status"] == "internal_error"
    assert "does not exist" in result["message"]
    assert result["data"]["state_store_error"]["reason"] == "table_not_found"
