"""Unit tests for OnboardingService.get_configuration and get_summary.

These cover the example branches called out in the design's testing strategy for
the two read-only tools (Requirements 5 and 6) using the in-memory
:class:`InMemoryStateRepository` fake and the configurable
:class:`MockQuickAdminClient` mock.

get_configuration (Requirement 5):
  * reports the stored suite, or null when none stored              (5.1)
  * projects all fourteen catalog keys, defaulting to not_configured  (5.2)
  * echoes recorded last_changed_at + changed_by exactly            (5.4)
  * repository read failure -> server_error describing missing data (5.3)

get_summary (Requirement 6):
  * reports the stored suite                                        (6.1)
  * partitions all fourteen entries across the four stages, none
    missing or duplicated                                           (6.3)
  * every final status is within {enabled, skipped, not_configured} (6.4)
  * suite-read failure -> server_error with no partial summary      (6.2)
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    ResultStatus,
    Stage,
    Suite,
)
from quick_admin_buddy_mcp.quick.mock import MockQuickAdminClient
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"

# The four summary stage names (6.3).
FOUR_STAGES = (
    Stage.PRODUCTIVITY_SUITE.value,
    Stage.PRODUCTIVITY_CONNECTORS.value,
    Stage.ENTERPRISE_KNOWLEDGE.value,
    Stage.OTHER_CONNECTORS.value,
)

# The allowed final-status set reported by the summary (6.4).
ALLOWED_SUMMARY_STATUSES = {"enabled", "skipped", "not_configured"}


def _service(state: InMemoryStateRepository | None = None):
    catalog = ConnectorCatalog()
    state = state or InMemoryStateRepository()
    quick = MockQuickAdminClient()
    return OnboardingService(catalog, state, quick), state, quick


def _all_catalog_keys() -> set[str]:
    return {entry.key for entry in ConnectorCatalog().all()}


# ---------------------------------------------------------------------------
# get_configuration
# ---------------------------------------------------------------------------


def test_get_configuration_reports_null_suite_when_none_stored():
    service, _state, _quick = _service()

    result = service.get_configuration(TENANT)

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] is None


def test_get_configuration_reports_stored_suite():
    service, state, _quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)

    result = service.get_configuration(TENANT)

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] == Suite.MICROSOFT_365.value


def test_get_configuration_covers_all_fourteen_keys_defaulting_not_configured():
    service, _state, _quick = _service()

    result = service.get_configuration(TENANT)

    assert result.status == ResultStatus.SUCCESS
    connectors = result.data["connectors"]
    # Exactly the fourteen catalog keys, none missing or extra.
    assert set(connectors) == _all_catalog_keys()
    assert len(connectors) == 14
    # Every unrecorded connector defaults to not_configured.
    for view in connectors.values():
        assert view["status"] == ConnectorStatus.NOT_CONFIGURED.value
        assert view["last_changed_at"] is None
        assert view["changed_by"] is None


def test_get_configuration_status_values_are_within_allowed_set():
    service, state, _quick = _service()
    state.set_connector_status(TENANT, "outlook", ConnectorStatus.ENABLED, "admin")
    state.set_connector_status(TENANT, "atlassian", ConnectorStatus.ERROR, "admin")

    result = service.get_configuration(TENANT)

    allowed = {s.value for s in ConnectorStatus}
    for view in result.data["connectors"].values():
        assert view["status"] in allowed
    assert result.data["connectors"]["outlook"]["status"] == "enabled"
    assert result.data["connectors"]["atlassian"]["status"] == "error"


def test_get_configuration_echoes_recorded_timestamp_and_principal():
    service, state, _quick = _service()
    # Record with an explicit, known timestamp and principal so we can assert
    # they are echoed back byte-for-byte (5.4). We set the record directly on
    # the stored state to control the timestamp value.
    from quick_admin_buddy_mcp.domain.types import ConnectorRecord, OnboardingState

    stored = OnboardingState(
        tenant_id=TENANT,
        suite=Suite.MICROSOFT_365,
        connectors={
            "outlook": ConnectorRecord(
                status=ConnectorStatus.ENABLED,
                last_changed_at="2025-01-15T10:22:00Z",
                changed_by="admin@acme",
            )
        },
    )
    state._store[TENANT] = stored  # type: ignore[attr-defined]

    result = service.get_configuration(TENANT)

    view = result.data["connectors"]["outlook"]
    assert view["status"] == "enabled"
    assert view["last_changed_at"] == "2025-01-15T10:22:00Z"
    assert view["changed_by"] == "admin@acme"


def test_get_configuration_read_failure_is_server_error_no_partial_data():
    service, state, _quick = _service()

    def _boom(_tenant):
        raise RuntimeError("dynamo read failed")

    state.get = _boom  # type: ignore[assignment]

    result = service.get_configuration(TENANT)

    assert result.status == ResultStatus.SERVER_ERROR
    assert result.message
    # No partial data: connectors/suite are not populated.
    assert result.data.get("connectors") is None
    assert result.data.get("suite") is None


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


def test_get_summary_reports_stored_suite():
    service, state, _quick = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.get_summary(TENANT)

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] == Suite.GOOGLE_WORKSPACE.value
    # The productivity suite stage carries the suite info separately.
    stages = result.data["stages"]
    assert (
        stages[Stage.PRODUCTIVITY_SUITE.value]["suite"] == Suite.GOOGLE_WORKSPACE.value
    )


def test_get_summary_reports_null_suite_when_none_stored():
    service, _state, _quick = _service()

    result = service.get_summary(TENANT)

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] is None


def test_get_summary_partitions_all_fourteen_entries_across_four_stages():
    service, _state, _quick = _service()

    result = service.get_summary(TENANT)

    assert result.status == ResultStatus.SUCCESS
    stages = result.data["stages"]
    # Exactly the four stage names present.
    assert set(stages) == set(FOUR_STAGES)

    # Collect every connector key across the three connector-bearing stages.
    seen: list[str] = []
    for stage_name in (
        Stage.PRODUCTIVITY_CONNECTORS.value,
        Stage.ENTERPRISE_KNOWLEDGE.value,
        Stage.OTHER_CONNECTORS.value,
    ):
        seen.extend(stages[stage_name]["connectors"].keys())

    # Every catalog entry appears exactly once — none omitted, none duplicated.
    assert sorted(seen) == sorted(_all_catalog_keys())
    assert len(seen) == len(set(seen)) == 14
    # The productivity suite stage holds no connectors (it is the suite itself).
    assert "connectors" not in stages[Stage.PRODUCTIVITY_SUITE.value]


def test_get_summary_final_statuses_within_allowed_set():
    service, state, _quick = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)
    state.set_connector_status(TENANT, "outlook", ConnectorStatus.ENABLED, "admin")
    state.set_connector_status(
        TENANT, "sharepoint", ConnectorStatus.NOT_CONFIGURED, "admin"
    )
    # A stored ERROR must be reported within the allowed set (mapped to
    # not_configured), never leaking an "error" status.
    state.set_connector_status(TENANT, "atlassian", ConnectorStatus.ERROR, "admin")

    result = service.get_summary(TENANT)

    stages = result.data["stages"]
    for stage_name in (
        Stage.PRODUCTIVITY_CONNECTORS.value,
        Stage.ENTERPRISE_KNOWLEDGE.value,
        Stage.OTHER_CONNECTORS.value,
    ):
        for status in stages[stage_name]["connectors"].values():
            assert status in ALLOWED_SUMMARY_STATUSES

    # Spot-check the specific mappings.
    assert (
        stages[Stage.PRODUCTIVITY_CONNECTORS.value]["connectors"]["outlook"]
        == "enabled"
    )
    assert (
        stages[Stage.ENTERPRISE_KNOWLEDGE.value]["connectors"]["sharepoint"]
        == "not_configured"
    )
    assert (
        stages[Stage.OTHER_CONNECTORS.value]["connectors"]["atlassian"]
        == "not_configured"
    )


def test_get_summary_suite_read_failure_is_server_error_no_partial_summary():
    service, state, _quick = _service()

    def _boom(_tenant):
        raise RuntimeError("dynamo read failed")

    state.get = _boom  # type: ignore[assignment]

    result = service.get_summary(TENANT)

    assert result.status == ResultStatus.SERVER_ERROR
    assert result.message
    # No partial summary is returned.
    assert result.data.get("stages") is None
    assert result.data.get("suite") is None
