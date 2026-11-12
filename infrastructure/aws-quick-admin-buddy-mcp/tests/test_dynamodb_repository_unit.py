"""Unit tests for :class:`DynamoDbStateRepository`.

These tests use a hand-rolled fake DynamoDB table that captures ``get_item`` /
``update_item`` calls (no real AWS, no ``moto`` dependency). They verify the
repository's boto3 usage and its (de)serialization, not DynamoDB's own
expression-evaluation semantics (that is covered by the moto-backed tests in
test_dynamodb_repository_moto.py).
"""

from __future__ import annotations

from typing import Any

from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    OnboardingState,
    Suite,
)
from quick_admin_buddy_mcp.state import DynamoDbStateRepository


class FakeTable:
    """Minimal fake of a boto3 DynamoDB Table that records calls."""

    def __init__(self, item: dict[str, Any] | None = None) -> None:
        self.stored_item = item
        self.get_item_calls: list[dict[str, Any]] = []
        self.update_item_calls: list[dict[str, Any]] = []

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        self.get_item_calls.append(kwargs)
        if self.stored_item is None:
            return {}
        return {"Item": self.stored_item}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.update_item_calls.append(kwargs)
        return {}


class FakeResource:
    """Fake boto3 resource whose ``Table`` returns a provided FakeTable."""

    def __init__(self, table: FakeTable) -> None:
        self._table = table
        self.requested_table_name: str | None = None

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - boto3 API name
        self.requested_table_name = name
        return self._table


def _make_repo(item: dict[str, Any] | None = None):
    table = FakeTable(item)
    resource = FakeResource(table)
    repo = DynamoDbStateRepository("OnboardingState", resource=resource)
    return repo, table, resource


def test_constructor_uses_provided_table_name() -> None:
    _, _, resource = _make_repo()
    assert resource.requested_table_name == "OnboardingState"


def test_get_absent_item_returns_empty_state() -> None:
    repo, table, _ = _make_repo(item=None)

    state = repo.get("acme-corp")

    assert state == OnboardingState(tenant_id="acme-corp")
    assert state.suite is None
    assert state.connectors == {}
    # Read is keyed by tenant_id only (per-tenant isolation).
    assert table.get_item_calls == [{"Key": {"tenant_id": "acme-corp"}}]


def test_get_deserializes_stored_item() -> None:
    stored = {
        "tenant_id": "acme-corp",
        "suite": "microsoft_365",
        "connectors": {
            "outlook": {
                "status": "enabled",
                "last_changed_at": "2025-01-15T10:22:00Z",
                "changed_by": "admin@acme",
            },
            "sharepoint": {
                "status": "not_configured",
                "last_changed_at": None,
                "changed_by": None,
            },
        },
    }
    repo, _, _ = _make_repo(item=stored)

    state = repo.get("acme-corp")

    assert state.tenant_id == "acme-corp"
    assert state.suite is Suite.MICROSOFT_365
    assert state.connectors["outlook"].status is ConnectorStatus.ENABLED
    assert state.connectors["outlook"].last_changed_at == "2025-01-15T10:22:00Z"
    assert state.connectors["outlook"].changed_by == "admin@acme"
    assert state.connectors["sharepoint"].status is ConnectorStatus.NOT_CONFIGURED
    assert state.connectors["sharepoint"].last_changed_at is None
    assert state.connectors["sharepoint"].changed_by is None


def test_put_suite_issues_update_setting_suite() -> None:
    repo, table, _ = _make_repo()

    repo.put_suite("acme-corp", Suite.GOOGLE_WORKSPACE)

    assert len(table.update_item_calls) == 1
    call = table.update_item_calls[0]
    assert call["Key"] == {"tenant_id": "acme-corp"}
    assert "SET suite = :suite" in call["UpdateExpression"]
    # connectors initialized only if absent -> existing connectors preserved.
    assert "if_not_exists(connectors" in call["UpdateExpression"]
    assert call["ExpressionAttributeValues"][":suite"] == "google_workspace"


def test_set_connector_status_sets_nested_connector() -> None:
    repo, table, _ = _make_repo()

    repo.set_connector_status(
        "acme-corp", "outlook", ConnectorStatus.ENABLED, "admin@acme"
    )

    # Two sequential calls, not one: DynamoDB rejects a single UpdateExpression
    # that both initializes a parent map and sets a nested path under it as an
    # "overlapping document paths" ValidationException. See the method's
    # docstring in state/dynamodb.py.
    assert len(table.update_item_calls) == 2

    ensure_parent_call, set_nested_call = table.update_item_calls
    assert ensure_parent_call["Key"] == {"tenant_id": "acme-corp"}
    assert ensure_parent_call["UpdateExpression"] == (
        "SET connectors = if_not_exists(connectors, :empty)"
    )
    assert ensure_parent_call["ExpressionAttributeValues"] == {":empty": {}}
    assert "connectors.#key" not in ensure_parent_call["UpdateExpression"]

    assert set_nested_call["Key"] == {"tenant_id": "acme-corp"}
    assert set_nested_call["UpdateExpression"] == "SET connectors.#key = :record"
    assert "if_not_exists" not in set_nested_call["UpdateExpression"]
    assert set_nested_call["ExpressionAttributeNames"] == {"#key": "outlook"}
    record = set_nested_call["ExpressionAttributeValues"][":record"]
    assert record["status"] == "enabled"
    assert record["changed_by"] == "admin@acme"
    assert record["last_changed_at"] is not None
    assert record["last_changed_at"].endswith("Z")


def test_writes_are_idempotent_in_shape() -> None:
    """Re-invoking with the same input produces the same update expression."""
    repo, table, _ = _make_repo()

    repo.put_suite("acme-corp", Suite.MICROSOFT_365)
    repo.put_suite("acme-corp", Suite.MICROSOFT_365)

    first, second = table.update_item_calls
    assert first["UpdateExpression"] == second["UpdateExpression"]
    assert first["ExpressionAttributeValues"] == second["ExpressionAttributeValues"]
