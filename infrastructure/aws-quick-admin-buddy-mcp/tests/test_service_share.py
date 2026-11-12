"""Domain-service tests for sharing an existing connector / knowledge base.

Covers ``share_action_connector`` and ``share_kb_integration``:
  1. unknown key                 -> validation_failure; no API call
  2. capability unsupported      -> validation_failure; no API call
  3. empty principal             -> validation_failure; no API call
  4. invalid id prefix           -> validation_failure; no API call
  5. happy path                  -> success; calls share_* with (key, principal, prefix)
  6. API / internal errors       -> server_error / internal_error

Unlike disable, sharing does NOT require the integration to be ``enabled`` in
local state - the resource is assumed to already exist in AWS Quick.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    ShareActionConnectorRequest,
    ShareKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ResultStatus
from quick_admin_buddy_mcp.quick.mock import MockBehavior, MockQuickAdminClient, Mode
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"


def _service(quick: MockQuickAdminClient | None = None):
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    return OnboardingService(catalog, state, quick), state, quick


# ---------------------------------------------------------------------------
# 1. Unknown key
# ---------------------------------------------------------------------------


def test_share_action_unknown_key_is_validation_failure_no_api():
    service, _state, quick = _service()
    result = service.share_action_connector(
        TENANT,
        ShareActionConnectorRequest(connector_key="nope", share_with="workshopuser"),
    )
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "nope" in result.message
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 2. Capability unsupported (outlook has no knowledge base)
# ---------------------------------------------------------------------------


def test_share_kb_unsupported_capability_is_validation_failure_no_api():
    service, _state, quick = _service()
    result = service.share_kb_integration(
        TENANT,
        ShareKnowledgeBaseRequest(connector_key="outlook", share_with="workshopuser"),
    )
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 3. Empty principal
# ---------------------------------------------------------------------------


def test_share_action_empty_principal_is_validation_failure_no_api():
    service, _state, quick = _service()
    result = service.share_action_connector(
        TENANT, ShareActionConnectorRequest(connector_key="outlook", share_with="   ")
    )
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 4. Invalid id prefix
# ---------------------------------------------------------------------------


def test_share_action_invalid_prefix_is_validation_failure_no_api():
    service, _state, quick = _service()
    result = service.share_action_connector(
        TENANT,
        ShareActionConnectorRequest(
            connector_key="outlook",
            share_with="workshopuser",
            connector_id_prefix="bad prefix!",
        ),
    )
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert quick.calls == []


# ---------------------------------------------------------------------------
# 5. Happy path - no enabled state required
# ---------------------------------------------------------------------------


def test_share_action_happy_path_calls_api_without_enabled_state():
    service, _state, quick = _service()
    result = service.share_action_connector(
        TENANT,
        ShareActionConnectorRequest(connector_key="outlook", share_with="workshopuser"),
    )
    assert result.status == ResultStatus.SUCCESS
    assert result.data["shared_with"] == "workshopuser"
    calls = quick.calls_for("share_action_connector")
    assert len(calls) == 1
    # positional args: (key, principal, id_prefix)
    assert calls[0].args == ("outlook", "workshopuser", None)


def test_share_kb_happy_path_passes_prefix_and_full_arn():
    service, _state, quick = _service()
    arn = "arn:aws:quicksight:us-east-1:999999999999:group/default/analysts"
    result = service.share_kb_integration(
        TENANT,
        ShareKnowledgeBaseRequest(
            connector_key="sharepoint",
            share_with=arn,
            connector_id_prefix="my-company-",
        ),
    )
    assert result.status == ResultStatus.SUCCESS
    calls = quick.calls_for("share_knowledge_base")
    assert len(calls) == 1
    assert calls[0].args == ("sharepoint", arn, "my-company-")


# ---------------------------------------------------------------------------
# 6. API / internal error classification
# ---------------------------------------------------------------------------


def test_share_action_api_error_is_server_error():
    quick = MockQuickAdminClient()
    quick.set_behavior("share_action_connector", MockBehavior(mode=Mode.API_ERROR))
    service, _state, _ = _service(quick)
    result = service.share_action_connector(
        TENANT,
        ShareActionConnectorRequest(connector_key="outlook", share_with="workshopuser"),
    )
    assert result.status == ResultStatus.SERVER_ERROR


def test_share_kb_internal_error_is_internal_error():
    quick = MockQuickAdminClient()
    quick.set_behavior("share_knowledge_base", MockBehavior(mode=Mode.INTERNAL_ERROR))
    service, _state, _ = _service(quick)
    result = service.share_kb_integration(
        TENANT,
        ShareKnowledgeBaseRequest(
            connector_key="sharepoint", share_with="workshopuser"
        ),
    )
    assert result.status == ResultStatus.INTERNAL_ERROR
