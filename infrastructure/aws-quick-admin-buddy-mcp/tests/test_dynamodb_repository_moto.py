"""Integration tests for :class:`DynamoDbStateRepository` against real DynamoDB
semantics, via ``moto``'s in-memory DynamoDB.

Unlike the hand-rolled fake in ``test_dynamodb_repository_unit.py`` (which
records calls but does not evaluate ``UpdateExpression`` validity), ``moto``
enforces DynamoDB's actual rules. In particular, it reproduces the production
bug this suite guards against: a single ``UpdateExpression`` that both
initializes the ``connectors`` map and sets a nested ``connectors.<key>`` path
raises ``ValidationException: ... document paths overlap ...``. These tests pin
the corrected two-call behavior end to end, including the first-ever write for
a tenant where ``connectors`` does not yet exist.
"""

from __future__ import annotations

import boto3
import pytest

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover - moto is a dev dependency
    pytest.skip("moto not installed", allow_module_level=True)

from quick_admin_buddy_mcp.domain.types import ConnectorStatus, Suite
from quick_admin_buddy_mcp.state import DynamoDbStateRepository

_TABLE_NAME = "OnboardingState"
_REGION = "eu-west-1"


def _create_table(resource) -> None:
    resource.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@mock_aws
def test_set_connector_status_persists_on_first_write_when_connectors_absent() -> None:
    """The original failing scenario: enable a connector when the tenant item
    has no ``connectors`` map yet. Must persist without a ValidationException."""
    resource = boto3.resource("dynamodb", region_name=_REGION)
    _create_table(resource)
    repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

    # No put_suite first: connectors truly absent (parent map does not exist).
    repo.set_connector_status(
        "shared", "outlook", ConnectorStatus.ENABLED, "admin@acme"
    )

    state = repo.get("shared")
    assert state.connectors["outlook"].status is ConnectorStatus.ENABLED
    assert state.connectors["outlook"].changed_by == "admin@acme"


@mock_aws
def test_set_connector_status_after_put_suite_preserves_suite() -> None:
    """Reproduces the exact production sequence: set_suite (creates item with
    empty connectors map), then enable a connector."""
    resource = boto3.resource("dynamodb", region_name=_REGION)
    _create_table(resource)
    repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

    repo.put_suite("shared", Suite.MICROSOFT_365)
    repo.set_connector_status(
        "shared", "outlook", ConnectorStatus.ENABLED, "admin@acme"
    )

    state = repo.get("shared")
    assert state.suite is Suite.MICROSOFT_365
    assert state.connectors["outlook"].status is ConnectorStatus.ENABLED


@mock_aws
def test_multiple_connectors_do_not_clobber_each_other() -> None:
    """Each connector write touches only its own entry; others are preserved."""
    resource = boto3.resource("dynamodb", region_name=_REGION)
    _create_table(resource)
    repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

    repo.put_suite("shared", Suite.MICROSOFT_365)
    repo.set_connector_status("shared", "outlook", ConnectorStatus.ENABLED, "a")
    repo.set_connector_status("shared", "ms_teams", ConnectorStatus.ENABLED, "a")

    state = repo.get("shared")
    assert state.connectors["outlook"].status is ConnectorStatus.ENABLED
    assert state.connectors["ms_teams"].status is ConnectorStatus.ENABLED
    assert state.suite is Suite.MICROSOFT_365


@mock_aws
def test_set_connector_status_is_idempotent_on_reinvocation() -> None:
    """Re-invoking an interrupted write converges to the same stored state."""
    resource = boto3.resource("dynamodb", region_name=_REGION)
    _create_table(resource)
    repo = DynamoDbStateRepository(_TABLE_NAME, resource=resource)

    repo.set_connector_status("shared", "outlook", ConnectorStatus.ENABLED, "a")
    repo.set_connector_status("shared", "outlook", ConnectorStatus.ENABLED, "a")

    state = repo.get("shared")
    assert state.connectors["outlook"].status is ConnectorStatus.ENABLED
    assert len(state.connectors) == 1
