"""Unit tests for OnboardingService.list_catalog and OnboardingService.set_suite.

These cover each branch of the two methods using the in-memory
:class:`InMemoryStateRepository` fake and the :class:`MockQuickAdminClient`
mock, so the deterministic domain layer is tested without AWS.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    ResultStatus,
    Suite,
)
from quick_admin_buddy_mcp.quick.mock import MockQuickAdminClient
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

TENANT = "acme-corp"


def _service():
    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    quick = MockQuickAdminClient()
    return OnboardingService(catalog, state, quick), state


# ---------------------------------------------------------------------------
# list_catalog
# ---------------------------------------------------------------------------


def test_list_catalog_no_filter_returns_all_fourteen():
    service, _ = _service()
    result = service.list_catalog(None)

    assert result.status == ResultStatus.SUCCESS
    connectors = result.data["connectors"]
    assert len(connectors) == 14
    # Microsoft Calendar is not a standalone entry (covered by `outlook`).
    assert "ms_calendar" not in {e["key"] for e in connectors}
    # Each entry carries exactly the public projection fields.
    for entry in connectors:
        assert set(entry) == {
            "key",
            "display_name",
            "category",
            "suite_gate",
            "supports_action",
            "supports_knowledge_base",
        }
        assert entry["key"]
        assert entry["suite_gate"] in {"microsoft_365", "google_workspace", "any"}


def test_list_catalog_microsoft_365_filter_returns_ms_and_any_gates():
    service, _ = _service()
    result = service.list_catalog("microsoft_365")

    assert result.status == ResultStatus.SUCCESS
    gates = {e["suite_gate"] for e in result.data["connectors"]}
    assert gates <= {"microsoft_365", "any"}
    assert "google_workspace" not in gates
    keys = {e["key"] for e in result.data["connectors"]}
    assert "outlook" in keys  # ms-gated
    assert "confluence" in keys  # any-gated
    assert "gmail" not in keys  # google-gated, excluded


def test_list_catalog_google_workspace_filter_returns_google_and_any_gates():
    service, _ = _service()
    result = service.list_catalog("google_workspace")

    assert result.status == ResultStatus.SUCCESS
    gates = {e["suite_gate"] for e in result.data["connectors"]}
    assert gates <= {"google_workspace", "any"}
    keys = {e["key"] for e in result.data["connectors"]}
    assert "gmail" in keys
    assert "atlassian" in keys  # any-gated (Jira Cloud)
    assert "outlook" not in keys
    # The four new Google Workspace action-only connectors are gated here too.
    assert {"google_docs", "google_meet", "google_sheets", "google_slides"} <= keys


def test_new_google_workspace_connectors_are_action_only_not_kb():
    """google_docs/google_meet/google_sheets/google_slides: action-only, no KB."""
    catalog = ConnectorCatalog()

    for key in ("google_docs", "google_meet", "google_sheets", "google_slides"):
        entry = catalog.get(key)
        assert entry is not None, f"missing catalog entry: {key}"
        assert entry.suite_gate == "google_workspace"
        assert catalog.supports_action(key) is True
        assert catalog.supports_kb(key) is False


def test_list_catalog_invalid_suite_is_validation_failure_naming_value_and_options():
    service, _ = _service()
    result = service.list_catalog("libreoffice")

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "libreoffice" in result.message
    assert "google_workspace" in result.message
    assert "microsoft_365" in result.message
    assert result.data["invalid_suite"] == "libreoffice"
    assert result.data["valid_suites"] == ["google_workspace", "microsoft_365"]


# ---------------------------------------------------------------------------
# set_suite
# ---------------------------------------------------------------------------


def test_set_suite_invalid_value_no_state_change():
    service, state = _service()
    result = service.set_suite(TENANT, "not_a_suite")

    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "not_a_suite" in result.message
    assert "google_workspace" in result.message and "microsoft_365" in result.message
    # No state persisted.
    assert state.get(TENANT).suite is None


def test_set_suite_from_empty_state_persists_and_succeeds():
    service, state = _service()
    result = service.set_suite(TENANT, "microsoft_365")

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] == "microsoft_365"
    assert state.get(TENANT).suite == Suite.MICROSOFT_365


def test_set_suite_same_suite_is_already_configured_no_change():
    service, state = _service()
    state.put_suite(TENANT, Suite.GOOGLE_WORKSPACE)

    result = service.set_suite(TENANT, "google_workspace")

    assert result.status == ResultStatus.ALREADY_CONFIGURED
    assert result.data["suite"] == "google_workspace"
    assert state.get(TENANT).suite == Suite.GOOGLE_WORKSPACE


def test_set_suite_conflict_lists_enabled_gated_connectors_no_change():
    service, state = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)
    # Enable an ms-gated connector and an any-gated connector.
    state.set_connector_status(TENANT, "outlook", ConnectorStatus.ENABLED, "admin")
    state.set_connector_status(TENANT, "confluence", ConnectorStatus.ENABLED, "admin")

    result = service.set_suite(TENANT, "google_workspace")

    assert result.status == ResultStatus.SUITE_CONFLICT
    # Only the ms-gated connector conflicts; the any-gated one survives.
    assert result.data["conflicting_connectors"] == ["outlook"]
    assert "outlook" in result.message
    assert result.data["current_suite"] == "microsoft_365"
    assert result.data["requested_suite"] == "google_workspace"
    # State unchanged.
    assert state.get(TENANT).suite == Suite.MICROSOFT_365


def test_set_suite_switch_allowed_when_no_gated_connectors_enabled():
    service, state = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)
    # Only an any-gated connector is enabled -> switch is safe.
    state.set_connector_status(TENANT, "confluence", ConnectorStatus.ENABLED, "admin")

    result = service.set_suite(TENANT, "google_workspace")

    assert result.status == ResultStatus.SUCCESS
    assert result.data["suite"] == "google_workspace"
    assert result.data["previous_suite"] == "microsoft_365"
    assert state.get(TENANT).suite == Suite.GOOGLE_WORKSPACE


def test_set_suite_switch_ignores_not_enabled_gated_connectors():
    service, state = _service()
    state.put_suite(TENANT, Suite.MICROSOFT_365)
    # An ms-gated connector recorded but NOT enabled must not block a switch.
    state.set_connector_status(
        TENANT, "outlook", ConnectorStatus.NOT_CONFIGURED, "admin"
    )

    result = service.set_suite(TENANT, "google_workspace")

    assert result.status == ResultStatus.SUCCESS
    assert state.get(TENANT).suite == Suite.GOOGLE_WORKSPACE
