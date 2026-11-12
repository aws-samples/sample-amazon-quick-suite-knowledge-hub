"""Unit tests for the disable method pairs.

Covers ``disable_action_connector`` and ``disable_kb_integration``:
  1. unknown key                      -> validation_failure; no API call
  2. capability unsupported           -> validation_failure; no API call
  3. not enabled / absent             -> already_configured; no API call
  4. enabled -> delete + persist      -> success; API/internal/persistence errors

Action-connector disable calls ``delete_action_connector``; knowledge-base
disable calls ``delete_knowledge_base``.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    DisableKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.service import OnboardingService, _record_key
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    IntegrationType,
    ResultStatus,
)
from quick_admin_buddy_mcp.quick.mock import MockBehavior, MockQuickAdminClient, Mode
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"


def _service(quick: MockQuickAdminClient | None = None):
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    return OnboardingService(catalog, state, quick), state, quick


def _enable_action(state, key: str) -> None:
    state.set_connector_status(
        TENANT,
        _record_key(key, IntegrationType.ACTION_CONNECTOR),
        ConnectorStatus.ENABLED,
        "admin",
    )


# ---------------------------------------------------------------------------
# 1. Unknown key
# ---------------------------------------------------------------------------


def test_disable_action_unknown_key_is_validation_failure_no_api():
    service, state, quick = _service()

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="nope")
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "nope" in result.message
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 2. Capability unsupported
# ---------------------------------------------------------------------------


def test_disable_kb_on_action_only_connector_is_validation_failure():
    service, state, quick = _service()

    result = service.disable_kb_integration(
        TENANT, DisableKnowledgeBaseRequest(connector_key="gmail")
    )

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "does not support a knowledge base" in result.message
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 3. Not enabled / absent
# ---------------------------------------------------------------------------


def test_disable_action_absent_is_already_configured_no_api():
    service, state, quick = _service()

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )

    assert result.status == ResultStatus.ALREADY_CONFIGURED
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 4. Enabled -> delete + persist
# ---------------------------------------------------------------------------


def test_disable_action_enabled_deletes_via_api_and_records_not_configured():
    service, state, quick = _service()
    _enable_action(state, "outlook")

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )

    assert result.status == ResultStatus.SUCCESS
    assert len(quick.calls_for("delete_action_connector")) == 1
    record = state.get(TENANT).connectors[
        _record_key("outlook", IntegrationType.ACTION_CONNECTOR)
    ]
    assert record.status == ConnectorStatus.NOT_CONFIGURED


def test_disable_kb_enabled_deletes_via_delete_knowledge_base():
    service, state, quick = _service()
    state.set_connector_status(
        TENANT,
        _record_key("sharepoint", IntegrationType.KNOWLEDGE_BASE),
        ConnectorStatus.ENABLED,
        "admin",
    )

    result = service.disable_kb_integration(
        TENANT, DisableKnowledgeBaseRequest(connector_key="sharepoint")
    )

    assert result.status == ResultStatus.SUCCESS
    assert len(quick.calls_for("delete_knowledge_base")) == 1
    assert quick.calls_for("delete_action_connector") == []


def test_disable_action_api_error_surfaces_server_error_no_state_change():
    quick = MockQuickAdminClient()
    quick.set_behavior("delete_action_connector", MockBehavior(mode=Mode.API_ERROR))
    service, state, quick = _service(quick)
    _enable_action(state, "outlook")

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert (
        state.get(TENANT)
        .connectors[_record_key("outlook", IntegrationType.ACTION_CONNECTOR)]
        .status
        == ConnectorStatus.ENABLED
    )


def test_disable_action_internal_error_surfaces_internal_error():
    quick = MockQuickAdminClient()
    quick.set_behavior(
        "delete_action_connector", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, quick = _service(quick)
    _enable_action(state, "outlook")

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert (
        state.get(TENANT)
        .connectors[_record_key("outlook", IntegrationType.ACTION_CONNECTOR)]
        .status
        == ConnectorStatus.ENABLED
    )


def test_disable_action_persistence_failure_after_delete_is_server_error():
    service, state, quick = _service()
    _enable_action(state, "outlook")

    def _boom(*args, **kwargs):
        raise RuntimeError("dynamo write failed")

    state.set_connector_status = _boom  # type: ignore[assignment]

    result = service.disable_action_connector(
        TENANT, DisableActionConnectorRequest(connector_key="outlook")
    )

    assert result.status == ResultStatus.SERVER_ERROR
    assert "persist" in result.message.lower()
    assert quick.calls_for("delete_action_connector")
