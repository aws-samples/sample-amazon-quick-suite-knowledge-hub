"""Unit/integration tests for the MCP server tool layer.

These tests exercise the tool methods end-to-end through the domain service,
but without AWS or the network: :class:`quick_admin_buddy_mcp.server.OnboardingTools`
is built on an :class:`OnboardingService` composed on an
:class:`InMemoryStateRepository` and a :class:`MockQuickAdminClient`.

They call the tool methods directly rather than going through the MCP
transport, and assert each returns a sanitized ``dict`` (a :class:`ToolResult`
projection) with a valid status for representative cases.
"""

from __future__ import annotations

import pytest

from quick_admin_buddy_mcp import server
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    EnableActionConnectorRequest,
    EnableKnowledgeBaseRequest,
    ShareActionConnectorRequest,
    ShareKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ResultStatus
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.quick.secrets import InMemorySecretResolver
from quick_admin_buddy_mcp.state import InMemoryStateRepository

# The full set of valid Result_Status string values a tool may return.
_VALID_STATUSES = {s.value for s in ResultStatus}

# The shared tenant the tools are bound to in this module.
_DEV_TENANT = "test-tenant"


@pytest.fixture
def in_memory_service() -> OnboardingService:
    """A service backed by in-memory state and a mock Quick Admin client."""
    return OnboardingService(
        ConnectorCatalog(),
        InMemoryStateRepository(),
        MockQuickAdminClient(),
        InMemorySecretResolver(
            {
                _KB_SECRET_NAME: {
                    "client_id": "c",
                    "tenant_id": "t",
                    "kms_key_arn": "arn:aws:kms:eu-west-1:1:key/x",
                    "certificate_thumbprint": "AB12",
                    "domain_url": "https://contoso.sharepoint.com",
                },
                _CUSTOM_OAUTH_SECRET: _CUSTOM_OAUTH,
            }
        ),
    )


_KB_SECRET_NAME = "quick-admin-buddy/sharepoint-kb-admin"  # nosec B105 - test fixture secret name, not a credential


@pytest.fixture
def tools(in_memory_service) -> server.OnboardingTools:
    """The tool layer, bound to the in-memory service and the shared tenant."""
    return server.OnboardingTools(in_memory_service, _DEV_TENANT)


def _assert_tool_result(result: dict) -> None:
    """Assert a tool return value is a sanitized Tool_Result dict."""
    assert isinstance(result, dict)
    assert set(result.keys()) == {"status", "message", "data"}
    assert result["status"] in _VALID_STATUSES
    assert isinstance(result["message"], str) and result["message"]
    assert isinstance(result["data"], dict)


def test_list_connector_catalog_returns_all_fourteen_connectors(tools) -> None:
    """No suite filter → success with all fourteen catalog entries (Req 1.1)."""
    result = tools.list_connector_catalog()

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert len(result["data"]["connectors"]) == 14


def test_list_connector_catalog_invalid_suite_is_validation_failure(tools) -> None:
    """An unknown suite filter → validation_failure naming the value (Req 1.3)."""
    result = tools.list_connector_catalog("not_a_suite")

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_set_productivity_suite_returns_success(tools) -> None:
    """Setting a suite on fresh state → success with the stored suite (Req 2.1)."""
    result = tools.set_productivity_suite("microsoft_365")

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["suite"] == "microsoft_365"


_CUSTOM_OAUTH = {
    "client_id": "cid",
    "client_secret": "sec",
    "domain_url": "https://d",
    "authorization_url": "https://a",
    "token_url": "https://t",
    "redirect_url": "https://r",
}
_CUSTOM_OAUTH_SECRET = "quick-admin-buddy/test-custom-oauth"


def test_enable_action_connector_success_after_suite_set(tools) -> None:
    """Enabling a valid action connector after the suite is set → success."""
    tools.set_productivity_suite("microsoft_365")

    result = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_CUSTOM_OAUTH_SECRET,
            config=None,
        ),
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["connector_key"] == "outlook"
    assert result["data"]["integration_type"] == "action_connector"


def test_enable_kb_integration_success_after_suite_set(tools) -> None:
    """Enabling a knowledge base integration after the suite is set → success."""
    tools.set_productivity_suite("microsoft_365")

    # Knowledge bases use the KB auth types (user_managed / admin_managed), not
    # the OAuth app methods used by action connectors.
    result = tools.enable_kb_integration(
        EnableKnowledgeBaseRequest(
            connector_key="sharepoint",
            auth_type="admin_managed",
            auth_params=None,
            secret_id=_KB_SECRET_NAME,
            config={
                "sites": ["https://contoso.sharepoint.com/sites/kb"],
            },
            owner="workshopuser",
        ),
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["integration_type"] == "knowledge_base"


def test_enable_action_connector_missing_params_reports_contract(tools) -> None:
    """Missing auth params → validation_failure carrying the secret contract."""
    tools.set_productivity_suite("microsoft_365")

    result = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            auth_params={"client_id": "cid"},
            secret_id=None,
            config=None,
        ),
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value
    assert "client_secret" in result["data"]["missing_params"]
    assert result["data"]["secret_contract"]["auth_type"] == "custom_oauth_app"


def test_disable_action_connector_unknown_key_is_validation_failure(tools) -> None:
    """Disabling an unknown connector key → validation_failure."""
    result = tools.disable_action_connector(
        DisableActionConnectorRequest(connector_key="nope")
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_share_action_connector_impl_success(tools) -> None:
    """Sharing an existing action connector → success via the mock client."""
    result = tools.share_action_connector(
        ShareActionConnectorRequest(
            connector_key="outlook", share_with="workshopuser", connector_id_prefix=None
        ),
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["shared_with"] == "workshopuser"


def test_share_kb_integration_impl_unsupported_is_validation_failure(tools) -> None:
    """Sharing a KB on a connector without KB support → validation_failure."""
    result = tools.share_kb_integration(
        ShareKnowledgeBaseRequest(
            connector_key="outlook", share_with="workshopuser", connector_id_prefix=None
        ),
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_get_tenant_configuration_returns_success_with_all_connectors(tools) -> None:
    """Reading configuration → success projecting all fourteen connectors (Req 5.2)."""
    result = tools.get_tenant_configuration()

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert len(result["data"]["connectors"]) == 14


def test_get_onboarding_summary_returns_success_with_four_stages(tools) -> None:
    """Summary → success with all four stage groups (Req 6.3)."""
    result = tools.get_onboarding_summary()

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert set(result["data"]["stages"].keys()) == {
        "productivity suite",
        "productivity connectors",
        "enterprise knowledge",
        "other connectors",
    }


def test_enable_action_connector_client_secret_not_echoed(tools) -> None:
    """The client_secret auth parameter never appears in the result."""
    tools.set_productivity_suite("microsoft_365")

    secret = "do-not-leak-this-client-secret"  # nosec B105 - deliberate dummy value used to assert redaction, not a real credential
    params = {**_CUSTOM_OAUTH, "client_secret": secret}
    result = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            auth_params=params,
            secret_id=None,
            config=None,
        ),
    )

    _assert_tool_result(result)
    assert secret not in server_json(result)


def server_json(result: dict) -> str:
    """Serialize a result dict to JSON for substring assertions."""
    import json

    return json.dumps(result)
