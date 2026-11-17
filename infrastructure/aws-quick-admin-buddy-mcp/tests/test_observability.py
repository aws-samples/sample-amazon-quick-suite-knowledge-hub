"""Tests for the per-tool-call structured log line and the AuthType dispatch."""

from __future__ import annotations

import json
import logging

import pytest
from botocore.exceptions import ClientError

from quick_admin_buddy_mcp import server
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import EnableActionConnectorRequest
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import AuthType
from quick_admin_buddy_mcp.observability import LOGGER_NAME
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.quick.boto_client import (
    _AUTH_CONFIG_BUILDERS,
    BotoQuickAdminClient,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminInternalError,
)
from quick_admin_buddy_mcp.quick.secrets import InMemorySecretResolver
from quick_admin_buddy_mcp.state import InMemoryStateRepository

_SECRET_VALUE = "a-long-client-secret-value-that-must-never-be-logged-0123"
_SECRET_NAME = "quick-admin-buddy/test-outlook"


@pytest.fixture
def service():
    secrets = InMemorySecretResolver(
        {
            _SECRET_NAME: {
                "client_id": "cid-123",
                "client_secret": _SECRET_VALUE,
                "domain_url": "https://graph.example.com",
                "authorization_url": "https://auth.example.com/authorize",
                "token_url": "https://auth.example.com/token",
                "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
            }
        }
    )
    svc = OnboardingService(
        ConnectorCatalog(), InMemoryStateRepository(), MockQuickAdminClient(), secrets
    )
    return svc


@pytest.fixture
def tools(service) -> server.OnboardingTools:
    return server.OnboardingTools(service, "test-tenant")


def _tool_lines(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == LOGGER_NAME and '"event": "tool_call"' in r.getMessage()
    ]


def test_each_tool_call_writes_one_structured_line(tools, service, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    tools.set_productivity_suite("microsoft_365")
    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_SECRET_NAME,
            share_with="workshopuser",
        ),
    )

    lines = _tool_lines(caplog)
    assert [line["tool"] for line in lines] == [
        "set_productivity_suite",
        "enable_action_connector",
    ]
    enable = lines[1]
    assert enable["connector_key"] == "outlook"
    assert enable["status"] == "success"
    assert enable["aws_request_ids"] == []  # the mock client makes no AWS calls
    assert isinstance(enable["duration_ms"], int)
    # No arguments, messages, or secret material are logged.
    raw = caplog.text
    assert _SECRET_VALUE not in raw
    assert _SECRET_NAME not in raw
    assert "workshopuser" not in raw
    assert "message" not in enable


def test_log_line_carries_error_type(tools, service, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            auth_params={"client_secret": _SECRET_VALUE},
        ),
    )

    (line,) = _tool_lines(caplog)
    assert line["status"] == "validation_failure"
    assert line["error_type"] == "inline_secret_not_allowed"
    assert _SECRET_VALUE not in caplog.text


class _FakeQuickSight:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def describe_action_connector(self, **kwargs):
        meta = {"ResponseMetadata": {"RequestId": "req-ok-1"}}
        if self.fail:
            raise ClientError(
                {
                    "Error": {"Code": "ResourceNotFoundException", "Message": "x"},
                    "ResponseMetadata": {"RequestId": "req-err-2"},
                },
                "DescribeActionConnector",
            )
        return {"ActionConnector": {"Key": kwargs["Key"]}, **meta}


@pytest.mark.parametrize(
    "fail,expected", [(False, ["req-ok-1"]), (True, ["req-err-2"])]
)
def test_aws_request_ids_are_collected_per_call(caplog, fail, expected):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    client = BotoQuickAdminClient(
        client=_FakeQuickSight(fail),
        region_name="eu-west-1",
        aws_account_id="123456789012",
    )

    def call() -> dict:
        try:
            client.describe_action_connector("outlook")
        except QuickAdminApiError:
            return {"status": "server_error", "data": {}}
        return {"status": "success", "data": {}}

    from quick_admin_buddy_mcp.observability import logged_tool_call

    logged_tool_call("probe", call, connector_key="outlook")

    (line,) = _tool_lines(caplog)
    assert line["aws_request_ids"] == expected


def test_auth_config_builders_cover_every_action_connector_auth_type():
    assert set(_AUTH_CONFIG_BUILDERS) == {
        AuthType.CUSTOM_OAUTH_APP,
        AuthType.SERVICE_TO_SERVICE_OAUTH,
        AuthType.API_KEY,
    }


@pytest.mark.parametrize("auth_type", ["user_managed", "not-an-auth-type", None])
def test_auth_config_without_builder_fails_fast(auth_type):
    with pytest.raises(QuickAdminInternalError):
        BotoQuickAdminClient._authentication_config(
            {"auth_type": auth_type, "auth_params": {}}
        )
