"""Unit tests for OnboardingService error classification and message tone.

* A Quick API error (returned by a Create*/Delete* action) → ``server_error``
  with a non-empty, actionable message that surfaces the API error for
  troubleshooting.
* An internal/network/timeout failure → ``internal_error`` with a non-empty,
  actionable message.
* Every non-success / non-already-configured result states a cause and a remedy;
  every success / already-configured result reads as a confirmation.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService, _record_key
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    IntegrationType,
    ResultStatus,
    Suite,
)
from quick_admin_buddy_mcp.quick.mock import MockBehavior, MockQuickAdminClient, Mode
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"

CUSTOM_OAUTH = {
    "client_id": "cid",
    "client_secret": "sec",
    "domain_url": "https://d",
    "authorization_url": "https://a",
    "token_url": "https://t",
    "redirect_url": "https://r",
}

_ACTION_KEY = _record_key("outlook", IntegrationType.ACTION_CONNECTOR)


def _service(quick: MockQuickAdminClient | None = None):
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    return OnboardingService(catalog, state, quick), state, quick


# ---------------------------------------------------------------------------
# Quick API errors -> server_error
# ---------------------------------------------------------------------------


def test_enable_api_error_from_create_action_connector_is_server_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_action_connector",
        MockBehavior(mode=Mode.API_ERROR, error_message="quick api rejected request"),
    )
    service, state, quick = _service(quick)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert "AWS Quick API" in result.message
    assert "retry" in result.message.lower()
    assert result.data["connector_key"] == "outlook"
    assert state.get(TENANT).connectors == {}


def test_disable_api_error_from_delete_path_is_server_error():
    quick = MockQuickAdminClient()
    quick.set_behavior("delete_action_connector", MockBehavior(mode=Mode.API_ERROR))
    service, state, quick = _service(quick)
    state.set_connector_status(TENANT, _ACTION_KEY, ConnectorStatus.ENABLED, "admin")

    result = service.disable_action_connector(TENANT, "outlook")

    assert result.status == ResultStatus.SERVER_ERROR
    assert "AWS Quick API" in result.message
    assert state.get(TENANT).connectors[_ACTION_KEY].status == ConnectorStatus.ENABLED


# ---------------------------------------------------------------------------
# Internal / network errors -> internal_error
# ---------------------------------------------------------------------------


def test_enable_internal_error_from_create_action_connector_is_internal_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "create_action_connector",
        MockBehavior(mode=Mode.INTERNAL_ERROR, error_message="connection timed out"),
    )
    service, state, quick = _service(quick)
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.enable_action_connector(
        TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
    )

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert "internal error" in result.message.lower()
    assert "retry" in result.message.lower()
    assert state.get(TENANT).connectors == {}


def test_disable_internal_error_from_delete_path_is_internal_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "delete_action_connector", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, quick = _service(quick)
    state.set_connector_status(TENANT, _ACTION_KEY, ConnectorStatus.ENABLED, "admin")

    result = service.disable_action_connector(TENANT, "outlook")

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert "internal error" in result.message.lower()
    assert state.get(TENANT).connectors[_ACTION_KEY].status == ConnectorStatus.ENABLED


# ---------------------------------------------------------------------------
# Message-presence + tone sweep across every status
# ---------------------------------------------------------------------------

_REMEDY_MARKERS = (
    "retry",
    "provide",
    "set ",
    "disable",
    "enable",
    "change",
    "complete",
    "review",
)


def _representative_results():
    """Yield ``(label, result)`` covering one result per Result_Status."""
    catalog = ConnectorCatalog()

    # success — enable action connector happy path.
    s_state = InMemoryStateRepository()
    s_state.put_suite(TENANT, Suite.MICROSOFT_365)
    s_service = OnboardingService(catalog, s_state, MockQuickAdminClient())
    yield (
        "success",
        s_service.enable_action_connector(
            TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ),
    )

    # already_configured — enabling an already-enabled action connector.
    ac_state = InMemoryStateRepository()
    ac_state.put_suite(TENANT, Suite.MICROSOFT_365)
    ac_state.set_connector_status(TENANT, _ACTION_KEY, ConnectorStatus.ENABLED, "admin")
    ac_service = OnboardingService(catalog, ac_state, MockQuickAdminClient())
    yield (
        "already_configured",
        ac_service.enable_action_connector(
            TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ),
    )

    # validation_failure — unknown connector key.
    vf_state = InMemoryStateRepository()
    vf_state.put_suite(TENANT, Suite.MICROSOFT_365)
    vf_service = OnboardingService(catalog, vf_state, MockQuickAdminClient())
    yield (
        "validation_failure",
        vf_service.enable_action_connector(
            TENANT, "totally_unknown", "custom_oauth_app", CUSTOM_OAUTH
        ),
    )

    # suite_conflict — switch suite while a gated connector is enabled.
    sc_state = InMemoryStateRepository()
    sc_state.put_suite(TENANT, Suite.MICROSOFT_365)
    sc_state.set_connector_status(TENANT, _ACTION_KEY, ConnectorStatus.ENABLED, "admin")
    sc_service = OnboardingService(catalog, sc_state, MockQuickAdminClient())
    yield "suite_conflict", sc_service.set_suite(TENANT, Suite.GOOGLE_WORKSPACE.value)

    # server_error — Quick API error on create.
    se_quick = MockQuickAdminClient()
    se_quick.set_behavior("create_action_connector", MockBehavior(mode=Mode.API_ERROR))
    se_state = InMemoryStateRepository()
    se_state.put_suite(TENANT, Suite.MICROSOFT_365)
    se_service = OnboardingService(catalog, se_state, se_quick)
    yield (
        "server_error",
        se_service.enable_action_connector(
            TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ),
    )

    # internal_error — internal failure on create.
    ie_quick = MockQuickAdminClient()
    ie_quick.set_behavior(
        "create_action_connector", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    ie_state = InMemoryStateRepository()
    ie_state.put_suite(TENANT, Suite.MICROSOFT_365)
    ie_service = OnboardingService(catalog, ie_state, ie_quick)
    yield (
        "internal_error",
        ie_service.enable_action_connector(
            TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH
        ),
    )


_CONFIRMATION_STATUSES = {ResultStatus.SUCCESS, ResultStatus.ALREADY_CONFIGURED}


def test_every_representative_result_carries_a_nonempty_message():
    for label, result in _representative_results():
        assert result.message and result.message.strip(), (
            f"{label} result had an empty message"
        )


def test_confirmation_messages_read_as_confirmations():
    for label, result in _representative_results():
        if result.status in _CONFIRMATION_STATUSES:
            lowered = result.message.lower()
            assert "error" not in lowered and "failed" not in lowered, (
                f"{label} confirmation message reads like a failure: {result.message!r}"
            )
            assert any(
                word in lowered
                for word in ("enabled", "disabled", "set", "already", "changed")
            ), f"{label} message does not read as a confirmation: {result.message!r}"


def test_non_confirmation_messages_state_cause_and_remedy():
    for label, result in _representative_results():
        if result.status not in _CONFIRMATION_STATUSES:
            lowered = result.message.lower()
            assert any(marker in lowered for marker in _REMEDY_MARKERS), (
                f"{label} message does not state a remedy/next action: {result.message!r}"
            )
