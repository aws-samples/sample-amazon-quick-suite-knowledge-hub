"""A configurable in-memory mock of :class:`QuickAdminClient` for domain tests.

The mock implements the full :class:`QuickAdminClient` protocol and lets tests
inject, *per action*, one of three behaviours:

* **success** - return a configured (or sensibly defaulted) detail value.
* **API error** - raise :class:`QuickAdminApiError` (maps to ``server_error``).
* **internal/network error** - raise :class:`QuickAdminInternalError`
  (maps to ``internal_error``).

Behaviour is configured through :class:`MockBehavior` values keyed by action
name. A default behaviour applies to any action without an explicit override,
so a test can, for example, make only ``create_action_connector`` fail while
every other action succeeds.

The mock also records every call it receives so tests can assert which API
actions were invoked (e.g. Property 7 requires ``create_action_connector`` to
be called on the enable happy path).

This lives in the package (rather than ``tests/``) so both the domain tests and
any future in-package harness can import it without a test-only path hack.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .client import (
    ConnectorDetail,
    DataSourceDetail,
    KnowledgeBaseDetail,
    PermissionDetail,
    QuickAdminApiError,
    QuickAdminInternalError,
)

# The action names, matching the QuickAdminClient protocol methods.
ACTIONS = (
    "create_action_connector",
    "describe_action_connector",
    "describe_action_connector_permissions",
    "list_action_connectors",
    "search_action_connectors",
    "delete_action_connector",
    "create_knowledge_base",
    "delete_knowledge_base",
    "create_data_source",
    "delete_data_source",
    "share_action_connector",
    "share_knowledge_base",
)


class Mode(str, Enum):
    """How a configured action should behave when invoked."""

    SUCCESS = "success"
    API_ERROR = "api_error"
    INTERNAL_ERROR = "internal_error"


@dataclass
class MockBehavior:
    """The behaviour to apply to one action (or as the default).

    * ``mode`` selects success, an API error, or an internal/network error.
    * ``response`` is the value returned on success. If ``None``, the mock
      synthesizes a sensible default for the action.
    * ``error_message`` / ``error_code`` customize the raised exception.
    """

    mode: Mode = Mode.SUCCESS
    response: Any = None
    error_message: str = "injected failure"
    error_code: str | None = None


@dataclass
class RecordedCall:
    """A single recorded invocation of a mocked action."""

    action: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class MockQuickAdminClient:
    """Configurable mock implementing the :class:`QuickAdminClient` protocol.

    Example::

        mock = MockQuickAdminClient()                       # everything succeeds
        mock.set_behavior("create_action_connector",
                          MockBehavior(mode=Mode.API_ERROR)) # this one fails

    All other actions keep the default behaviour.
    """

    default: MockBehavior = field(default_factory=MockBehavior)
    behaviors: dict[str, MockBehavior] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)
    # Quick user names/ARNs that principal_exists() reports as missing. Every
    # other principal is reported as existing. principal_exists() is a
    # pre-flight lookup, not an admin action, so it is not recorded in
    # ``calls`` (existing tests assert exact call lists).
    missing_principals: set[str] = field(default_factory=set)

    # -- configuration ---------------------------------------------------

    def set_behavior(self, action: str, behavior: MockBehavior) -> MockQuickAdminClient:
        """Override the behaviour for a single action. Returns ``self`` to chain."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")
        self.behaviors[action] = behavior
        return self

    def set_default(self, behavior: MockBehavior) -> MockQuickAdminClient:
        """Set the behaviour applied to actions without an explicit override."""
        self.default = behavior
        return self

    def calls_for(self, action: str) -> list[RecordedCall]:
        """Return the recorded calls for a given action."""
        return [c for c in self.calls if c.action == action]

    # -- protocol methods ------------------------------------------------

    def create_action_connector(self, key: str, request: dict) -> ConnectorDetail:
        return self._dispatch(
            "create_action_connector",
            lambda: ConnectorDetail(
                key=key, status="enabled", config=dict(request or {})
            ),
            key,
            request,
        )

    def delete_action_connector(
        self, key: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        return self._dispatch(
            "delete_action_connector",
            lambda: ConnectorDetail(key=key, status="not_configured"),
            key,
            id_prefix,
        )

    def create_knowledge_base(self, key: str, request: dict) -> KnowledgeBaseDetail:
        return self._dispatch(
            "create_knowledge_base",
            lambda: KnowledgeBaseDetail(
                key=key,
                knowledge_base_id=f"kb-{key}",
                creation_status="CREATING",
                raw=dict(request or {}),
            ),
            key,
            request,
        )

    def delete_knowledge_base(
        self, key: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        return self._dispatch(
            "delete_knowledge_base",
            lambda: KnowledgeBaseDetail(key=key, creation_status="DELETING"),
            key,
            id_prefix,
        )

    def create_data_source(self, key: str, request: dict) -> DataSourceDetail:
        return self._dispatch(
            "create_data_source",
            lambda: DataSourceDetail(
                key=key,
                data_source_id=f"ds-{key}",
                arn=f"arn:aws:quicksight:us-east-1:123456789012:datasource/ds-{key}",
                creation_status="CREATION_IN_PROGRESS",
                raw=dict(request or {}),
            ),
            key,
            request,
        )

    def delete_data_source(
        self, key: str, id_prefix: str | None = None
    ) -> DataSourceDetail:
        return self._dispatch(
            "delete_data_source",
            lambda: DataSourceDetail(key=key, creation_status="DELETED"),
            key,
            id_prefix,
        )

    def share_action_connector(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        return self._dispatch(
            "share_action_connector",
            lambda: ConnectorDetail(key=key, status="enabled"),
            key,
            principal,
            id_prefix,
        )

    def share_knowledge_base(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        return self._dispatch(
            "share_knowledge_base",
            lambda: KnowledgeBaseDetail(
                key=key,
                knowledge_base_id=f"kb-{key}",
                creation_status="ACTIVE",
            ),
            key,
            principal,
            id_prefix,
        )

    def principal_exists(self, principal: str) -> bool | None:
        return principal not in self.missing_principals

    def describe_action_connector(self, key: str) -> ConnectorDetail:
        return self._dispatch(
            "describe_action_connector",
            lambda: ConnectorDetail(key=key, status="enabled"),
            key,
        )

    def describe_action_connector_permissions(self, key: str) -> PermissionDetail:
        return self._dispatch(
            "describe_action_connector_permissions",
            lambda: PermissionDetail(key=key, granted=True, authorization_url=None),
            key,
        )

    def list_action_connectors(self) -> list[ConnectorDetail]:
        return self._dispatch(
            "list_action_connectors",
            lambda: [],
        )

    def search_action_connectors(self, query: dict) -> list[ConnectorDetail]:
        return self._dispatch(
            "search_action_connectors",
            lambda: [],
            query,
        )

    # -- internals -------------------------------------------------------

    def _dispatch(
        self,
        action: str,
        default_factory: Callable[[], Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Record the call, then apply the configured behaviour for ``action``."""
        self.calls.append(RecordedCall(action=action, args=args, kwargs=kwargs))
        behavior = self.behaviors.get(action, self.default)

        if behavior.mode is Mode.API_ERROR:
            raise QuickAdminApiError(behavior.error_message, code=behavior.error_code)
        if behavior.mode is Mode.INTERNAL_ERROR:
            raise QuickAdminInternalError(behavior.error_message)

        # Mode.SUCCESS
        return behavior.response if behavior.response is not None else default_factory()
