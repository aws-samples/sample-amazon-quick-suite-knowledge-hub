"""Tests for AWS Quick extension support.

Extensions are client-side add-ins (the Microsoft Office add-ins and the Google
Workspace browser extension) with **no AWS Quick admin API**, so the backend
records an admin-attested status (installed / skipped / not_configured) rather
than creating/verifying them. These tests cover the extension catalog, the
service `list_extensions` / `set_extension_status` methods, suite gating, the
configuration/summary projections, DynamoDB persistence, and the server tools.
"""

from __future__ import annotations

import boto3
import pytest

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog, ExtensionCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ExtensionStatus, ResultStatus, Suite
from quick_admin_buddy_mcp.quick.mock import MockQuickAdminClient
from quick_admin_buddy_mcp.state.repository import InMemoryStateRepository

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None

TENANT = "shared"

_MS_EXTENSIONS = {"outlook_addin", "excel_addin", "word_addin", "powerpoint_addin"}
_GOOGLE_EXTENSIONS = {"browser_extension"}


def _service():
    state = InMemoryStateRepository()
    return OnboardingService(ConnectorCatalog(), state, MockQuickAdminClient()), state


# ---------------------------------------------------------------------------
# Extension catalog
# ---------------------------------------------------------------------------


def test_extension_catalog_has_five_entries_suite_gated():
    cat = ExtensionCatalog()
    keys = {e.key for e in cat.all()}
    assert keys == _MS_EXTENSIONS | _GOOGLE_EXTENSIONS
    ms = {e.key for e in cat.filter_by_suite(Suite.MICROSOFT_365)}
    google = {e.key for e in cat.filter_by_suite(Suite.GOOGLE_WORKSPACE)}
    assert ms == _MS_EXTENSIONS
    assert google == _GOOGLE_EXTENSIONS


def test_extension_catalog_entries_have_docs_urls():
    for e in ExtensionCatalog().all():
        assert e.docs_url.startswith("https://docs.aws.amazon.com/quick/")


# ---------------------------------------------------------------------------
# list_extensions
# ---------------------------------------------------------------------------


def test_list_extensions_no_suite_returns_all():
    service, _ = _service()
    result = service.list_extensions(TENANT)
    assert result.status == ResultStatus.SUCCESS
    keys = {e["key"] for e in result.data["extensions"]}
    assert keys == _MS_EXTENSIONS | _GOOGLE_EXTENSIONS
    # default status is not_configured
    assert all(e["status"] == "not_configured" for e in result.data["extensions"])


def test_list_extensions_uses_stored_suite():
    service, _ = _service()
    service.set_suite(TENANT, "microsoft_365")
    result = service.list_extensions(TENANT)
    keys = {e["key"] for e in result.data["extensions"]}
    assert keys == _MS_EXTENSIONS
    assert result.data["suite"] == "microsoft_365"


def test_list_extensions_explicit_suite_overrides_stored():
    service, _ = _service()
    service.set_suite(TENANT, "microsoft_365")
    result = service.list_extensions(TENANT, suite="google_workspace")
    keys = {e["key"] for e in result.data["extensions"]}
    assert keys == _GOOGLE_EXTENSIONS


def test_list_extensions_invalid_suite_is_validation_failure():
    service, _ = _service()
    result = service.list_extensions(TENANT, suite="bogus")
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "bogus" in result.message


# ---------------------------------------------------------------------------
# set_extension_status
# ---------------------------------------------------------------------------


def test_set_extension_status_installed_succeeds_and_persists():
    service, state = _service()
    service.set_suite(TENANT, "microsoft_365")

    result = service.set_extension_status(TENANT, "outlook_addin", "installed", "admin")
    assert result.status == ResultStatus.SUCCESS
    assert result.data["status"] == "installed"

    # Persisted in state.extensions
    stored = state.get(TENANT)
    assert stored.extensions["outlook_addin"].status == ExtensionStatus.INSTALLED
    assert stored.extensions["outlook_addin"].changed_by == "admin"
    # Did NOT leak into the connectors map
    assert "outlook_addin" not in stored.connectors


def test_set_extension_status_skipped():
    service, state = _service()
    service.set_suite(TENANT, "google_workspace")
    result = service.set_extension_status(
        TENANT, "browser_extension", "skipped", "admin"
    )
    assert result.status == ResultStatus.SUCCESS
    assert state.get(TENANT).extensions["browser_extension"].status == (
        ExtensionStatus.SKIPPED
    )


def test_set_extension_status_suite_mismatch_is_validation_failure():
    service, _ = _service()
    service.set_suite(TENANT, "microsoft_365")
    # browser_extension is google_workspace-gated -> rejected under MS
    result = service.set_extension_status(
        TENANT, "browser_extension", "installed", "admin"
    )
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert result.data["suite_gate"] == "google_workspace"
    assert result.data["stored_suite"] == "microsoft_365"


def test_set_extension_status_unknown_key_is_validation_failure():
    service, _ = _service()
    result = service.set_extension_status(TENANT, "nope_addin", "installed", "admin")
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert "nope_addin" in result.message
    assert "outlook_addin" in result.data["valid_extensions"]


def test_set_extension_status_invalid_status_is_validation_failure():
    service, _ = _service()
    result = service.set_extension_status(TENANT, "excel_addin", "bogus", "admin")
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert set(result.data["valid_statuses"]) == {
        "installed",
        "skipped",
        "not_configured",
    }


def test_set_extension_status_no_suite_allows_any():
    """With no stored suite, the suite gate does not block (catalog view only)."""
    service, state = _service()
    result = service.set_extension_status(TENANT, "outlook_addin", "installed", "admin")
    assert result.status == ResultStatus.SUCCESS


# ---------------------------------------------------------------------------
# Projections: get_configuration / get_summary include extensions
# ---------------------------------------------------------------------------


def test_get_configuration_includes_extensions():
    service, _ = _service()
    service.set_suite(TENANT, "microsoft_365")
    service.set_extension_status(TENANT, "outlook_addin", "installed", "admin")

    cfg = service.get_configuration(TENANT)
    exts = cfg.data["extensions"]
    assert exts["outlook_addin"]["status"] == "installed"
    assert exts["excel_addin"]["status"] == "not_configured"
    # suite-filtered: no google extension present under MS
    assert "browser_extension" not in exts


def test_get_summary_includes_extensions():
    service, _ = _service()
    service.set_suite(TENANT, "google_workspace")
    service.set_extension_status(TENANT, "browser_extension", "skipped", "admin")

    summ = service.get_summary(TENANT)
    assert summ.data["extensions"] == {"browser_extension": "skipped"}


# ---------------------------------------------------------------------------
# DynamoDB persistence (moto) — round-trips via a separate `extensions` attr
# ---------------------------------------------------------------------------

_TABLE_NAME = "OnboardingState"
_REGION = "eu-west-1"


def _create_table(resource) -> None:
    resource.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.mark.skipif(mock_aws is None, reason="moto not installed")
def test_dynamodb_extension_status_round_trip():
    with mock_aws():
        from quick_admin_buddy_mcp.state import DynamoDbStateRepository

        resource = boto3.resource("dynamodb", region_name=_REGION)
        _create_table(resource)
        repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

        # First write with no prior item (extensions map absent).
        repo.set_extension_status(
            TENANT, "outlook_addin", ExtensionStatus.INSTALLED, "admin@acme"
        )
        state = repo.get(TENANT)
        assert state.extensions["outlook_addin"].status is ExtensionStatus.INSTALLED
        assert state.extensions["outlook_addin"].changed_by == "admin@acme"


@pytest.mark.skipif(mock_aws is None, reason="moto not installed")
def test_dynamodb_extensions_and_connectors_coexist():
    with mock_aws():
        from quick_admin_buddy_mcp.domain.types import ConnectorStatus
        from quick_admin_buddy_mcp.state import DynamoDbStateRepository

        resource = boto3.resource("dynamodb", region_name=_REGION)
        _create_table(resource)
        repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

        repo.put_suite(TENANT, Suite.MICROSOFT_365)
        repo.set_connector_status(
            TENANT, "outlook:action_connector", ConnectorStatus.ENABLED, "a"
        )
        repo.set_extension_status(TENANT, "excel_addin", ExtensionStatus.SKIPPED, "a")

        state = repo.get(TENANT)
        assert state.suite is Suite.MICROSOFT_365
        assert state.connectors["outlook:action_connector"].status is (
            ConnectorStatus.ENABLED
        )
        assert state.extensions["excel_addin"].status is ExtensionStatus.SKIPPED


# ---------------------------------------------------------------------------
# Server tool wrappers
# ---------------------------------------------------------------------------


def test_server_list_and_set_extension_tools():
    import quick_admin_buddy_mcp.server as server

    catalog = ConnectorCatalog()
    state = InMemoryStateRepository()
    svc = OnboardingService(catalog, state, MockQuickAdminClient())
    server.set_service(svc)
    try:
        svc.set_suite(TENANT, "microsoft_365")
        listed = server._list_extensions_impl(ctx=None)
        assert listed["status"] == "success"
        assert {e["key"] for e in listed["data"]["extensions"]} == _MS_EXTENSIONS

        res = server._set_extension_status_impl("word_addin", "installed", ctx=None)
        assert res["status"] == "success"
        assert res["data"]["status"] == "installed"
    finally:
        server.set_service(None)
