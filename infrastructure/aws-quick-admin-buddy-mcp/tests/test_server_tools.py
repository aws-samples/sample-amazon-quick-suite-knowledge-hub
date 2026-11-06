"""Unit/integration tests for the MCP server tool layer.

These tests exercise the six tool implementations end-to-end through the domain
service, but without AWS or the network: they override the runtime service with
an :class:`OnboardingService` composed on an
:class:`InMemoryStateRepository` and a :class:`MockQuickAdminClient` via
:func:`quick_admin_buddy_mcp.server.set_service`.

They call the plain ``_..._impl`` functions directly (passing ``ctx=None`` so
tenant resolution uses the dev-fallback env var) rather than unwrapping the
registered MCP tool objects, and assert each returns a sanitized ``dict``
(a :class:`ToolResult` projection) with a valid status for representative cases.
"""

from __future__ import annotations

import pytest

from quick_admin_buddy_mcp import server
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ResultStatus
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.quick.secrets import InMemorySecretResolver
from quick_admin_buddy_mcp.state import InMemoryStateRepository

# The full set of valid Result_Status string values a tool may return.
_VALID_STATUSES = {s.value for s in ResultStatus}

# The shared tenant used by _resolve_tenant for every test in this module.
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
                }
            }
        ),
    )


_KB_SECRET_NAME = "quick-admin-buddy/sharepoint-kb-admin"  # nosec B105 - test fixture secret name, not a credential


@pytest.fixture(autouse=True)
def wire_server(in_memory_service, monkeypatch) -> None:
    """Point the tools at the in-memory service and configure the shared tenant.

    Autouse so every test in this module runs against the injected service and a
    resolvable tenant. The override is cleared after each test.
    """
    monkeypatch.setenv(server._TENANT_ENV, _DEV_TENANT)
    server.set_service(in_memory_service)
    try:
        yield
    finally:
        server.set_service(None)


def _assert_tool_result(result: dict) -> None:
    """Assert a tool return value is a sanitized Tool_Result dict."""
    assert isinstance(result, dict)
    assert set(result.keys()) == {"status", "message", "data"}
    assert result["status"] in _VALID_STATUSES
    assert isinstance(result["message"], str) and result["message"]
    assert isinstance(result["data"], dict)


def test_list_connector_catalog_returns_all_fourteen_connectors() -> None:
    """No suite filter → success with all fourteen catalog entries (Req 1.1)."""
    result = server._list_connector_catalog_impl()

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert len(result["data"]["connectors"]) == 14


def test_list_connector_catalog_invalid_suite_is_validation_failure() -> None:
    """An unknown suite filter → validation_failure naming the value (Req 1.3)."""
    result = server._list_connector_catalog_impl("not_a_suite")

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_set_productivity_suite_returns_success() -> None:
    """Setting a suite on fresh state → success with the stored suite (Req 2.1)."""
    result = server._set_productivity_suite_impl("microsoft_365", ctx=None)

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


def test_enable_action_connector_success_after_suite_set() -> None:
    """Enabling a valid action connector after the suite is set → success."""
    server._set_productivity_suite_impl("microsoft_365", ctx=None)

    result = server._enable_action_connector_impl(
        "outlook", "custom_oauth_app", _CUSTOM_OAUTH, None, None, ctx=None
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["connector_key"] == "outlook"
    assert result["data"]["integration_type"] == "action_connector"


def test_enable_kb_integration_success_after_suite_set() -> None:
    """Enabling a knowledge base integration after the suite is set → success."""
    server._set_productivity_suite_impl("microsoft_365", ctx=None)

    # Knowledge bases use the KB auth types (user_managed / admin_managed), not
    # the OAuth app methods used by action connectors.
    result = server._enable_kb_integration_impl(
        "sharepoint",
        "admin_managed",
        None,
        _KB_SECRET_NAME,
        {
            "sites": ["https://contoso.sharepoint.com/sites/kb"],
        },
        owner="workshopuser",
        ctx=None,
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["integration_type"] == "knowledge_base"


def test_enable_action_connector_missing_params_reports_contract() -> None:
    """Missing auth params → validation_failure carrying the secret contract."""
    server._set_productivity_suite_impl("microsoft_365", ctx=None)

    result = server._enable_action_connector_impl(
        "outlook", "custom_oauth_app", {"client_id": "cid"}, None, None, ctx=None
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value
    assert "client_secret" in result["data"]["missing_params"]
    assert result["data"]["secret_contract"]["auth_type"] == "custom_oauth_app"


def test_disable_action_connector_unknown_key_is_validation_failure() -> None:
    """Disabling an unknown connector key → validation_failure."""
    result = server._disable_action_connector_impl("nope", ctx=None)

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_share_action_connector_impl_success() -> None:
    """Sharing an existing action connector → success via the mock client."""
    result = server._share_action_connector_impl(
        "outlook", "workshopuser", None, ctx=None
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert result["data"]["shared_with"] == "workshopuser"


def test_share_kb_integration_impl_unsupported_is_validation_failure() -> None:
    """Sharing a KB on a connector without KB support → validation_failure."""
    result = server._share_kb_integration_impl(
        "outlook", "workshopuser", None, ctx=None
    )

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.VALIDATION_FAILURE.value


def test_get_tenant_configuration_returns_success_with_all_connectors() -> None:
    """Reading configuration → success projecting all fourteen connectors (Req 5.2)."""
    result = server._get_tenant_configuration_impl(ctx=None)

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert len(result["data"]["connectors"]) == 14


def test_get_onboarding_summary_returns_success_with_four_stages() -> None:
    """Summary → success with all four stage groups (Req 6.3)."""
    result = server._get_onboarding_summary_impl(ctx=None)

    _assert_tool_result(result)
    assert result["status"] == ResultStatus.SUCCESS.value
    assert set(result["data"]["stages"].keys()) == {
        "productivity suite",
        "productivity connectors",
        "enterprise knowledge",
        "other connectors",
    }


def test_enable_action_connector_client_secret_not_echoed() -> None:
    """The client_secret auth parameter never appears in the result."""
    server._set_productivity_suite_impl("microsoft_365", ctx=None)

    secret = "do-not-leak-this-client-secret"  # nosec B105 - deliberate dummy value used to assert redaction, not a real credential
    params = {**_CUSTOM_OAUTH, "client_secret": secret}
    result = server._enable_action_connector_impl(
        "outlook", "custom_oauth_app", params, None, None, ctx=None
    )

    _assert_tool_result(result)
    assert secret not in server_json(result)


def test_resolve_tenant_returns_shared_tenant_ignoring_context() -> None:
    """Tenant is the configured shared value; the request context is ignored."""
    assert server._resolve_tenant(None) == _DEV_TENANT


def test_resolve_tenant_defaults_when_env_absent(monkeypatch) -> None:
    """With no ONBOARDING_TENANT set, resolution falls back to the 'shared'
    default rather than raising — every caller still maps to one tenant."""
    monkeypatch.delenv(server._TENANT_ENV, raising=False)
    assert server._resolve_tenant(None) == server._DEFAULT_TENANT


def server_json(result: dict) -> str:
    """Serialize a result dict to JSON for substring assertions."""
    import json

    return json.dumps(result)
