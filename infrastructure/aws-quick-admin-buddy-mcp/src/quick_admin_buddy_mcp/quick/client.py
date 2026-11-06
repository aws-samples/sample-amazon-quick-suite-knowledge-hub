"""The Quick Admin API client interface and its failure taxonomy.

This module defines the :class:`QuickAdminClient` protocol that the domain layer
depends on. The protocol fronts the five AWS Quick admin API actions
(Requirements 7.1-7.5): ``create_action_connector``,
``describe_action_connector``, ``describe_action_connector_permissions``,
``list_action_connectors``, and ``search_action_connectors``.

It also defines the lightweight value objects returned by those actions
(:class:`ConnectorDetail`, :class:`PermissionDetail`) and two distinct exception
classes the domain layer uses to classify failures:

* :class:`QuickAdminApiError` - a failure *returned by* a Quick Admin API action.
  The domain layer maps this to a ``server_error`` Tool_Result (Requirement 7.6).
* :class:`QuickAdminInternalError` - an internal/network failure (timeouts,
  connection errors, credential-resolution failures) that is *not* an API-level
  error. The domain layer maps this to an ``internal_error`` Tool_Result
  (Requirement 7.7).

The protocol is transport-agnostic: the runtime implementation wraps ``boto3``
(see task 16), while domain tests use the configurable :mod:`mock` in this
package. Keeping the interface here lets the domain layer stay independent of
both AWS and the test harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ConnectorDetail:
    """Lightweight detail for a single action connector.

    Mirrors the subset of an AWS Quick action-connector description the
    domain layer needs. ``key`` is the stable Connector_Key; ``status`` is the
    connector's state as reported by the API; ``config`` and ``raw`` carry any
    additional attributes without imposing a rigid schema. ``raw`` never holds
    resolved credential values - only opaque references.
    """

    key: str
    status: str = "enabled"
    config: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeBaseDetail:
    """Lightweight detail for a single knowledge base integration.

    Mirrors the subset of a ``CreateKnowledgeBase`` / describe response the
    domain layer needs. ``key`` is the stable Connector_Key the knowledge base
    was created for; ``knowledge_base_id`` / ``arn`` identify the created
    resource; ``creation_status`` reflects the API's reported status. ``raw``
    carries the full response and never holds resolved credential values.
    """

    key: str
    knowledge_base_id: str = ""
    arn: str = ""
    creation_status: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSourceDetail:
    """Lightweight detail for a single knowledge-base data source.

    An admin-managed knowledge base is created in two steps: first a *data
    source* (``CreateDataSource``) that holds the source-service connection and
    credentials (a SharePoint/OneDrive Entra app, a Google service account, or
    a Confluence site), then a *knowledge base* (``CreateKnowledgeBase``) that
    references that data source by ARN. This value object mirrors the subset of
    a ``CreateDataSource`` response the domain layer needs to thread the created
    data source's ``arn`` into the subsequent ``CreateKnowledgeBase`` call.
    ``raw`` carries the full response and never holds resolved credential
    values.
    """

    key: str
    data_source_id: str = ""
    arn: str = ""
    creation_status: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PermissionDetail:
    """Lightweight permission detail for a single action connector.

    ``key`` is the Connector_Key the permissions belong to. ``granted`` reflects
    whether the required third-party authorization has been granted.
    ``authorization_url`` carries the authorization URL verbatim when a grant is
    still required (Requirement 3.8); it is ``None`` once ``granted`` is true.
    ``raw`` carries any additional API attributes.
    """

    key: str
    granted: bool = True
    authorization_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# Default prefix for the stable, per-connector resource id used for action
# connectors, knowledge bases and their data sources, when the caller supplies
# none. The ActionConnectorId pattern is [\w\-]+ and must not start with
# "quicksuite-"; the KnowledgeBaseId pattern is [0-9a-zA-Z-_=.+]+.
# "quickadminbuddy-<key>" satisfies both. The domain layer validates a
# caller-supplied prefix against [\w\-]+ before it reaches the client.
DEFAULT_RESOURCE_ID_PREFIX = "quickadminbuddy-"


def resource_id(key: str, prefix: str | None = None) -> str:
    """Return the stable, API-valid resource id for a connector ``key``.

    The id is ``<prefix><key>``; ``prefix`` defaults to
    :data:`DEFAULT_RESOURCE_ID_PREFIX` when ``None`` or empty. Create, delete
    and share derive the id the same way, so they target the same resource as
    long as the same prefix is supplied to each.
    """
    effective = prefix if prefix else DEFAULT_RESOURCE_ID_PREFIX
    return f"{effective}{key}"


class QuickAdminApiError(Exception):
    """A failure returned by a Quick Admin API action.

    The domain layer maps this to a Tool_Result with Result_Status
    ``server_error`` (Requirement 7.6). ``code`` is the API-provided error code
    when available; ``message`` is a human-readable description. Callers must
    ensure raw credentials and internal stack detail are stripped before the
    message reaches a Tool_Result (handled by the sanitization boundary).
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class QuickAdminInternalError(Exception):
    """An internal or network failure that is not a Quick Admin API error.

    Covers timeouts, connection failures, and credential-resolution failures.
    The domain layer maps this to a Tool_Result with Result_Status
    ``internal_error`` (Requirement 7.7).
    """


@runtime_checkable
class QuickAdminClient(Protocol):
    """Interface fronting the five AWS Quick admin API actions.

    Every method either returns its detail value or raises
    :class:`QuickAdminApiError` (API-level failure) or
    :class:`QuickAdminInternalError` (internal/network failure). Implementations
    must not leak raw credentials through returned values or raised messages.
    """

    def create_action_connector(self, key: str, request: dict) -> ConnectorDetail:
        """Create/configure an action connector (``CreateActionConnector``).

        ``request`` is a provider-agnostic payload assembled by the domain layer
        (connector type, auth type, resolved auth parameters, connector config).
        The implementation maps it onto the real API request shape.
        """
        ...

    def describe_action_connector(self, key: str) -> ConnectorDetail:
        """Return detail for a single action connector (``DescribeActionConnector``)."""
        ...

    def describe_action_connector_permissions(self, key: str) -> PermissionDetail:
        """Return permission detail for a connector (``DescribeActionConnectorPermissions``)."""
        ...

    def list_action_connectors(self) -> list[ConnectorDetail]:
        """Return all of a tenant's action connectors (``ListActionConnectors``)."""
        ...

    def search_action_connectors(self, query: dict) -> list[ConnectorDetail]:
        """Return action connectors matching ``query`` (``SearchActionConnectors``)."""
        ...

    def delete_action_connector(
        self, key: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        """Delete/deactivate an action connector (``DeleteActionConnector``).

        ``id_prefix`` must match the prefix used at creation so the derived
        resource id targets the same connector.
        """
        ...

    def create_knowledge_base(self, key: str, request: dict) -> KnowledgeBaseDetail:
        """Create a knowledge base integration (``CreateKnowledgeBase``).

        ``request`` is a provider-agnostic payload assembled by the domain layer
        (data-source type, auth type, resolved auth parameters, config such as
        ACL settings). The implementation maps it onto the real API request.

        An admin-managed knowledge base references a data source by ARN, so the
        domain layer first calls :meth:`create_data_source` and passes the
        resulting ``arn`` in ``request["data_source_arn"]``.
        """
        ...

    def create_data_source(self, key: str, request: dict) -> DataSourceDetail:
        """Create a knowledge-base data source (``CreateDataSource``).

        ``request`` is a provider-agnostic payload assembled by the domain layer
        (data-source type, source connection parameters, resolved service
        credentials). The implementation maps it onto the real API request and
        returns the created data source's ``arn`` so the caller can create the
        knowledge base that references it.
        """
        ...

    def delete_data_source(
        self, key: str, id_prefix: str | None = None
    ) -> DataSourceDetail:
        """Delete a knowledge-base data source (``DeleteDataSource``).

        ``id_prefix`` must match the prefix used at creation so the derived
        resource id targets the same data source.
        """
        ...

    def delete_knowledge_base(
        self, key: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        """Delete a knowledge base integration (``DeleteKnowledgeBase``).

        ``id_prefix`` must match the prefix used at creation so the derived
        resource id targets the same knowledge base.
        """
        ...

    def share_action_connector(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        """Share an existing action connector with a Quick user or group
        (``UpdateActionConnectorPermissions``).

        Grants ``principal`` the owner-equivalent action-connector permission
        set on the connector whose id is derived from ``key`` + ``id_prefix``
        (the same id used at creation). ``principal`` is a full Quick user/group
        ARN or a bare name assumed to be a user in the ``default`` namespace.
        ``id_prefix`` must match the prefix used at creation so the derived
        resource id targets the same connector.
        """
        ...

    def share_knowledge_base(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        """Share an existing knowledge base with a Quick user or group
        (``UpdateKnowledgeBasePermissions``).

        Grants ``principal`` the owner-equivalent knowledge-base permission set
        on the knowledge base whose id is derived from ``key`` + ``id_prefix``.
        ``principal`` is a full Quick user/group ARN or a bare name assumed to
        be a user in the ``default`` namespace. ``id_prefix`` must match the
        prefix used at creation so the derived resource id targets the same
        knowledge base.
        """
        ...

    def principal_exists(self, principal: str) -> bool | None:
        """Report whether a Quick user named by ``principal`` exists.

        ``principal`` is the same value accepted by ``share_with`` / ``owner``:
        a bare user name (``default`` namespace) or a full Quick user ARN.

        Returns ``True`` when the user exists, ``False`` when AWS Quick says
        the user (or its namespace) does not exist, and ``None`` when existence
        could not be determined (for example a group ARN, or the lookup itself
        is not permitted). Callers treat ``None`` as "unknown" and proceed, so
        this check only ever improves the error message - it never blocks a
        request that AWS Quick itself would accept.
        """
        ...
