"""Idempotency guarantee tests for OnboardingService.

These example unit tests verify that the three state-changing operations
(``set_suite``, ``enable``, ``disable``) are idempotent: applying each one
TWICE with identical input converges to the same persisted
:class:`OnboardingState`, and the second application reports
``ResultStatus.ALREADY_CONFIGURED``. For ``enable`` and ``disable``, the second
(already-configured) call must NOT re-invoke the underlying Quick Admin API
action (``create_action_connector``).

Covered cases: set_suite with the same suite, enable when already enabled,
disable when already not configured, and re-invoked writes converging.

The deterministic domain layer is exercised with the in-memory
:class:`InMemoryStateRepository` fake and the configurable
:class:`MockQuickAdminClient` mock.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    EnableActionConnectorRequest,
)
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    ResultStatus,
    Suite,
)
from quick_admin_buddy_mcp.quick.mock import MockQuickAdminClient
from quick_admin_buddy_mcp.quick.secrets import InMemorySecretResolver
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"


def _service(quick: MockQuickAdminClient | None = None):
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    return OnboardingService(catalog, state, quick, TEST_SECRETS), state, quick


# ---------------------------------------------------------------------------
# set_suite idempotency (2.3, 9.4)
# ---------------------------------------------------------------------------


def test_set_suite_twice_converges_and_second_is_already_configured():
    service, state, quick = _service()

    first = service.set_suite(TENANT, Suite.MICROSOFT_365.value)
    state_after_first = state.get(TENANT)

    second = service.set_suite(TENANT, Suite.MICROSOFT_365.value)
    state_after_second = state.get(TENANT)

    # First application succeeds; second reports already_configured.
    assert first.status == ResultStatus.SUCCESS
    assert second.status == ResultStatus.ALREADY_CONFIGURED
    assert second.data["suite"] == Suite.MICROSOFT_365.value

    # State converged: the persisted state after the second call equals the
    # state after the first call.
    assert state_after_second == state_after_first
    assert state_after_second.suite == Suite.MICROSOFT_365


# ---------------------------------------------------------------------------
# enable idempotency (3.7, 9.4)
# ---------------------------------------------------------------------------


_CUSTOM_OAUTH = {
    "client_id": "cid",
    "client_secret": "sec",
    "domain_url": "https://d",
    "authorization_url": "https://a",
    "token_url": "https://t",
    "redirect_url": "https://r",
}
_CUSTOM_OAUTH_SECRET = "quick-admin-buddy/test-custom-oauth"

# Credentials come only from Secrets Manager (inline secrets are rejected),
# so tests store them in an in-memory resolver and pass the secret name.
TEST_SECRETS = InMemorySecretResolver(
    {
        _CUSTOM_OAUTH_SECRET: _CUSTOM_OAUTH,
    }
)

_ACTION_KEY = "outlook:action_connector"


def test_enable_twice_converges_and_second_is_already_configured_no_repeat_api():
    service, state, quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    first = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_CUSTOM_OAUTH_SECRET,
            principal="admin@acme",
        ),
    )
    state_after_first = state.get(TENANT)
    api_calls_after_first = len(quick.calls_for("create_action_connector"))

    second = service.enable_action_connector(
        TENANT,
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_CUSTOM_OAUTH_SECRET,
            principal="admin@acme",
        ),
    )
    state_after_second = state.get(TENANT)
    api_calls_after_second = len(quick.calls_for("create_action_connector"))

    # First application enables via the API; second reports already_configured
    # without touching the API again.
    assert first.status == ResultStatus.SUCCESS
    assert second.status == ResultStatus.ALREADY_CONFIGURED
    assert second.data["connector_key"] == "outlook"

    assert api_calls_after_first == 1
    assert api_calls_after_second == api_calls_after_first

    # State converged; the connector stays enabled.
    assert state_after_second == state_after_first
    assert state_after_second.connectors[_ACTION_KEY].status == ConnectorStatus.ENABLED


# ---------------------------------------------------------------------------
# disable idempotency (4.2, 9.4)
# ---------------------------------------------------------------------------


def test_disable_twice_converges_and_second_is_already_configured_no_repeat_api():
    service, state, quick = _service()
    # Start from an enabled action connector so the first disable does real work.
    state.set_connector_status(TENANT, _ACTION_KEY, ConnectorStatus.ENABLED, "admin")

    first = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )
    state_after_first = state.get(TENANT)
    api_calls_after_first = len(quick.calls_for("delete_action_connector"))

    second = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )
    state_after_second = state.get(TENANT)
    api_calls_after_second = len(quick.calls_for("delete_action_connector"))

    # First application deletes via the API; second reports already_configured
    # without touching the API again.
    assert first.status == ResultStatus.SUCCESS
    assert second.status == ResultStatus.ALREADY_CONFIGURED
    assert second.data["connector_key"] == "outlook"

    assert api_calls_after_first == 1
    assert api_calls_after_second == api_calls_after_first

    # State converged; the connector stays not_configured.
    assert state_after_second == state_after_first
    assert (
        state_after_second.connectors[_ACTION_KEY].status
        == ConnectorStatus.NOT_CONFIGURED
    )


def test_disable_already_not_configured_twice_is_idempotent_no_api():
    """Disabling an already-not-configured connector twice never calls the API."""
    service, state, quick = _service()

    first = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )
    state_after_first = state.get(TENANT)

    second = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )
    state_after_second = state.get(TENANT)

    assert first.status == ResultStatus.ALREADY_CONFIGURED
    assert second.status == ResultStatus.ALREADY_CONFIGURED
    # No API action was ever invoked, and state is unchanged/converged.
    assert quick.calls_for("delete_action_connector") == []
    assert state_after_second == state_after_first
