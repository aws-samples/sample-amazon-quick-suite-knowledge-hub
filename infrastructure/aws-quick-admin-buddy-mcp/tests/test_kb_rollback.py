"""Knowledge-base create rollback and disable state consistency.

A knowledge base is two AWS Quick resources: a data source and the knowledge
base that references it. These tests pin down that:

* a failed ``enable_kb_integration`` after ``CreateDataSource`` succeeded rolls
  the creation back (data source, and the knowledge base when it may exist),
  so a failed enable leaves nothing behind and a retry is not blocked;
* ``disable_kb_integration`` records ``not_configured`` as soon as the knowledge
  base is deleted (or already missing), even when the data source delete fails,
  and reports that leftover data source.
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
from quick_admin_buddy_mcp.state import InMemoryStateRepository, StateStoreError

TENANT = "acme-corp"
OWNER = "workshopuser"
KEY = "google_drive"
KB_RECORD = _record_key(KEY, IntegrationType.KNOWLEDGE_BASE)


def _service(quick: MockQuickAdminClient | None = None):
    state = InMemoryStateRepository()
    quick = quick or MockQuickAdminClient()
    service = OnboardingService(ConnectorCatalog(), state, quick)
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)
    return service, state, quick


def _enable(service: OnboardingService, prefix: str | None = None):
    # user_managed needs no secret, which keeps these tests about the
    # create/rollback sequence rather than credentials.
    return service.enable_kb_integration(
        TENANT, KEY, "user_managed", owner=OWNER, connector_id_prefix=prefix
    )


def _status(state: InMemoryStateRepository):
    record = state.get(TENANT).connectors.get(KB_RECORD)
    return record.status if record is not None else None


def _actions(quick: MockQuickAdminClient) -> list[str]:
    return [c.action for c in quick.calls]


# ---------------------------------------------------------------------------
# enable_kb_integration rollback
# ---------------------------------------------------------------------------


def test_create_kb_api_error_rolls_back_data_source():
    quick = MockQuickAdminClient().set_behavior(
        "create_knowledge_base",
        MockBehavior(mode=Mode.API_ERROR, error_message="InvalidParameterValue"),
    )
    service, state, quick = _service(quick)

    result = _enable(service)

    assert result.status == ResultStatus.SERVER_ERROR
    assert "InvalidParameterValue" in result.message
    assert "rolled back" in result.message
    # The KB was never created, so only the data source is deleted.
    assert _actions(quick) == [
        "create_data_source",
        "create_knowledge_base",
        "delete_data_source",
    ]
    rollback = result.data["rollback"]
    assert rollback["complete"] is True
    assert rollback["data_source_deleted"] is True
    assert rollback["data_source_id"] == f"quickadminbuddy-{KEY}"
    assert "knowledge_base_deleted" not in rollback
    assert _status(state) is None


def test_create_kb_internal_error_rolls_back_kb_and_data_source():
    quick = MockQuickAdminClient().set_behavior(
        "create_knowledge_base", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, quick = _service(quick)

    result = _enable(service, prefix="team-a-")

    assert result.status == ResultStatus.INTERNAL_ERROR
    # After a timeout the KB may exist, so it is deleted before its data source.
    assert _actions(quick)[2:] == ["delete_knowledge_base", "delete_data_source"]
    for call in quick.calls_for("delete_knowledge_base") + quick.calls_for(
        "delete_data_source"
    ):
        assert call.args == (KEY, "team-a-")
    rollback = result.data["rollback"]
    assert rollback["complete"] is True
    assert rollback["knowledge_base_id"] == f"team-a-{KEY}"
    assert _status(state) is None


def test_rollback_treats_missing_kb_as_deleted():
    quick = (
        MockQuickAdminClient()
        .set_behavior("create_knowledge_base", MockBehavior(mode=Mode.INTERNAL_ERROR))
        .set_behavior(
            "delete_knowledge_base",
            MockBehavior(mode=Mode.API_ERROR, error_code="ResourceNotFoundException"),
        )
    )
    service, _, _ = _service(quick)

    result = _enable(service)

    rollback = result.data["rollback"]
    assert rollback["knowledge_base_deleted"] is True
    assert rollback["data_source_deleted"] is True
    assert rollback["complete"] is True


def test_incomplete_rollback_names_the_leftover_data_source():
    quick = (
        MockQuickAdminClient()
        .set_behavior("create_knowledge_base", MockBehavior(mode=Mode.API_ERROR))
        .set_behavior(
            "delete_data_source",
            MockBehavior(mode=Mode.API_ERROR, error_message="ConflictException"),
        )
    )
    service, state, _ = _service(quick)

    result = _enable(service)

    assert result.status == ResultStatus.SERVER_ERROR
    rollback = result.data["rollback"]
    assert rollback["complete"] is False
    assert rollback["data_source_deleted"] is False
    assert "rollback was incomplete" in result.message
    assert f"data source 'quickadminbuddy-{KEY}'" in result.message
    assert _status(state) is None


def test_create_data_source_failure_rolls_back_nothing():
    # Nothing was created, and a same-id resource that already exists is not
    # ours to delete.
    quick = MockQuickAdminClient().set_behavior(
        "create_data_source",
        MockBehavior(mode=Mode.API_ERROR, error_message="ResourceExistsException"),
    )
    service, _, quick = _service(quick)

    result = _enable(service)

    assert result.status == ResultStatus.SERVER_ERROR
    assert _actions(quick) == ["create_data_source"]
    assert "rollback" not in result.data


def test_state_store_failure_after_create_rolls_back_kb():
    service, state, quick = _service()

    def _missing_table(*args, **kwargs):
        raise StateStoreError(
            StateStoreError.REASON_TABLE_NOT_FOUND,
            table_name="OnboardingState",
            region="eu-west-1",
            operation="write",
            error_code="ResourceNotFoundException",
        )

    state.set_connector_status = _missing_table  # type: ignore[assignment]

    result = _enable(service)

    # Rolled back completely, so nothing changed: internal_error.
    assert result.status == ResultStatus.INTERNAL_ERROR
    assert _actions(quick) == [
        "create_data_source",
        "create_knowledge_base",
        "delete_knowledge_base",
        "delete_data_source",
    ]
    assert result.data["rollback"]["complete"] is True
    assert result.data["state_store_error"]["reason"] == "table_not_found"


def test_unexpected_persist_failure_with_incomplete_rollback_is_server_error():
    quick = MockQuickAdminClient().set_behavior(
        "delete_knowledge_base", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, _ = _service(quick)

    def _boom(*args, **kwargs):
        raise RuntimeError("dynamo write failed")

    state.set_connector_status = _boom  # type: ignore[assignment]

    result = _enable(service)

    assert result.status == ResultStatus.SERVER_ERROR
    rollback = result.data["rollback"]
    assert rollback["complete"] is False
    assert rollback["knowledge_base_deleted"] is False
    assert "knowledge base 'quickadminbuddy-google_drive'" in result.message


def test_successful_enable_does_not_roll_back():
    service, state, quick = _service()

    result = _enable(service)

    assert result.status == ResultStatus.SUCCESS
    assert _actions(quick) == ["create_data_source", "create_knowledge_base"]
    assert _status(state) == ConnectorStatus.ENABLED


# ---------------------------------------------------------------------------
# disable_kb_integration: KB deleted => state not_configured
# ---------------------------------------------------------------------------


def _enabled_service(quick: MockQuickAdminClient | None = None):
    service, state, quick = _service(quick)
    state.set_connector_status(TENANT, KB_RECORD, ConnectorStatus.ENABLED, "admin")
    return service, state, quick


def test_disable_records_not_configured_when_data_source_delete_fails():
    quick = MockQuickAdminClient().set_behavior(
        "delete_data_source",
        MockBehavior(mode=Mode.API_ERROR, error_message="ConflictException"),
    )
    service, state, _ = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.SERVER_ERROR
    assert _status(state) == ConnectorStatus.NOT_CONFIGURED
    assert result.data["data_source_deleted"] is False
    assert result.data["data_source_id"] == f"quickadminbuddy-{KEY}"
    assert "ConflictException" in result.message

    # A retry is now a no-op: the knowledge base is disabled.
    again = service.disable_kb_integration(TENANT, KEY)
    assert again.status == ResultStatus.ALREADY_CONFIGURED


def test_disable_records_not_configured_on_data_source_internal_error():
    quick = MockQuickAdminClient().set_behavior(
        "delete_data_source", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, _ = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.SERVER_ERROR
    assert _status(state) == ConnectorStatus.NOT_CONFIGURED


def test_disable_kb_already_missing_counts_as_deleted():
    quick = MockQuickAdminClient().set_behavior(
        "delete_knowledge_base",
        MockBehavior(mode=Mode.API_ERROR, error_code="ResourceNotFoundException"),
    )
    service, state, quick = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.SUCCESS
    assert len(quick.calls_for("delete_data_source")) == 1
    assert _status(state) == ConnectorStatus.NOT_CONFIGURED


def test_disable_missing_data_source_is_success():
    quick = MockQuickAdminClient().set_behavior(
        "delete_data_source",
        MockBehavior(mode=Mode.API_ERROR, error_code="ResourceNotFoundException"),
    )
    service, state, _ = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.SUCCESS
    assert _status(state) == ConnectorStatus.NOT_CONFIGURED


def test_disable_kb_delete_failure_keeps_enabled_and_skips_data_source():
    quick = MockQuickAdminClient().set_behavior(
        "delete_knowledge_base",
        MockBehavior(mode=Mode.API_ERROR, error_message="AccessDeniedException"),
    )
    service, state, quick = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.SERVER_ERROR
    assert quick.calls_for("delete_data_source") == []
    assert _status(state) == ConnectorStatus.ENABLED


def test_disable_kb_delete_internal_error_keeps_enabled():
    quick = MockQuickAdminClient().set_behavior(
        "delete_knowledge_base", MockBehavior(mode=Mode.INTERNAL_ERROR)
    )
    service, state, quick = _enabled_service(quick)

    result = service.disable_kb_integration(TENANT, KEY)

    assert result.status == ResultStatus.INTERNAL_ERROR
    assert quick.calls_for("delete_data_source") == []
    assert _status(state) == ConnectorStatus.ENABLED
