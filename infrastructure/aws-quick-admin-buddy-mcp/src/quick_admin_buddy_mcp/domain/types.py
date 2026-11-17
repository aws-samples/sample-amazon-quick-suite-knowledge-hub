"""Shared domain types and the Tool_Result contract.

This module defines the enumerated value spaces (productivity suites, connector
statuses, summary stages, and tool-result statuses) and the core dataclasses
that flow through the domain layer: :class:`CatalogEntry`, :class:`ConnectorRecord`,
:class:`OnboardingState`, and :class:`ToolResult`.

These types are deterministic, dependency-free value objects.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Suite(str, Enum):
    """A tenant's selected office suite (Productivity_Suite).

    Gates which connectors are valid for a tenant. Subclasses ``str`` so values
    serialize directly to their string form in JSON.
    """

    GOOGLE_WORKSPACE = "google_workspace"
    MICROSOFT_365 = "microsoft_365"


class ConnectorStatus(str, Enum):
    """The state of a single connector for a tenant (Connector_Status)."""

    ENABLED = "enabled"
    NOT_CONFIGURED = "not_configured"
    ERROR = "error"


class IntegrationType(str, Enum):
    """The kind of AWS Quick integration an operation targets.

    A single catalog connector can be set up as an **action connector**
    (``CreateActionConnector``) and/or a **knowledge base**
    (``CreateKnowledgeBase``). The two are tracked and enabled/disabled
    independently, so a connector may be enabled as a knowledge base while its
    action connector is not configured, and vice versa.
    """

    ACTION_CONNECTOR = "action_connector"
    KNOWLEDGE_BASE = "knowledge_base"
    EXTENSION = "extension"


class ExtensionStatus(str, Enum):
    """The admin-attested state of a client-side AWS Quick extension.

    Extensions (the Microsoft Office add-ins and the browser extension) are
    installed by the user in their Office app / browser — there is **no AWS
    Quick admin API** to create or verify them. The backend therefore records an
    **admin-declared** status, not an API-verified one:

    * ``INSTALLED`` — the admin confirmed the extension is installed/deployed.
    * ``SKIPPED`` — the admin explicitly chose not to install it.
    * ``NOT_CONFIGURED`` — no decision recorded yet (the default).
    """

    INSTALLED = "installed"
    SKIPPED = "skipped"
    NOT_CONFIGURED = "not_configured"


class AuthType(str, Enum):
    """The authentication method used to create an integration.

    Both integration types support these OAuth methods. Each type requires a
    specific set of parameters (see :mod:`quick_admin_buddy_mcp.domain.auth`),
    which the agent supplies inline or references from a Secrets Manager secret.

    Action connectors use the OAuth app methods:

    * ``CUSTOM_OAUTH_APP`` — three-legged (3LO) OAuth with a customer-managed
      OAuth application; per-user delegated access.
    * ``SERVICE_TO_SERVICE_OAUTH`` — client-credentials, server-to-server OAuth;
      actions run under a service identity.
    * ``API_KEY`` — two-legged (2LO) API-key authentication (an API token, with
      an optional associated email). Used by sources such as Atlassian Confluence
      Cloud, whose 2LO flow authenticates with an API token (and the Atlassian
      account email). Maps to the API's ``ApiKeyConnectionMetadata``.

    Knowledge bases use a DIFFERENT pair of setup methods (the OAuth app methods
    above are NOT valid for a knowledge base):

    * ``KB_USER_MANAGED`` — the admin signs in to the source directly to
      authorize the connection; access is scoped to that user's own content.
    * ``KB_ADMIN_MANAGED`` — service credentials (a Microsoft Entra app with a
      certificate, or a Google service account with domain-wide delegation);
      AWS Quick crawls every user's content and enforces document-level ACLs.
    """

    CUSTOM_OAUTH_APP = "custom_oauth_app"
    SERVICE_TO_SERVICE_OAUTH = "service_to_service_oauth"
    API_KEY = "api_key"
    KB_USER_MANAGED = "user_managed"
    KB_ADMIN_MANAGED = "admin_managed"


class Stage(str, Enum):
    """The onboarding summary stage a catalog entry is grouped under.

    ``PRODUCTIVITY_SUITE`` is reported separately (the stored suite) and is not
    a catalog-entry stage; the remaining three group the fifteen catalog
    entries for :func:`get_onboarding_summary`.
    """

    PRODUCTIVITY_SUITE = "productivity suite"
    PRODUCTIVITY_CONNECTORS = "productivity connectors"
    ENTERPRISE_KNOWLEDGE = "enterprise knowledge"
    OTHER_CONNECTORS = "other connectors"


class ResultStatus(str, Enum):
    """The status field of a Tool_Result (Result_Status).

    ``server_error`` indicates a failure returned by a Quick_Admin_API action;
    ``internal_error`` indicates a failure within the MCP server itself
    (including network failures and timeouts).
    """

    SUCCESS = "success"
    ALREADY_CONFIGURED = "already_configured"
    VALIDATION_FAILURE = "validation_failure"
    SUITE_CONFLICT = "suite_conflict"
    AUTHORIZATION_REQUIRED = "authorization_required"
    SERVER_ERROR = "server_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class CatalogEntry:
    """A single, immutable entry in the fixed connector catalog.

    ``suite_gate`` is one of ``microsoft_365``, ``google_workspace``, or ``any``.
    ``stage`` is one of the three catalog-entry stages used for summary grouping.
    ``required_fields`` names the configuration fields that must be present to
    enable the connector (empty for connectors needing no extra config).
    ``requires_authorization`` marks connectors needing an OAuth-style
    third-party grant.
    """

    key: str
    display_name: str
    category: str
    suite_gate: str  # Literal["microsoft_365", "google_workspace", "any"]
    stage: str  # Literal["productivity connectors", "enterprise knowledge", "other connectors"]
    required_fields: tuple[str, ...] = ()
    requires_authorization: bool = False
    # Which AWS Quick integration types this connector can be set up as. A
    # connector may support an action connector, a knowledge base, or both
    # (see the official integration guides). Enable/disable operations check
    # the matching capability before calling the API.
    supports_action: bool = True
    supports_kb: bool = False
    # Config fields required ONLY when enabling this connector as a knowledge
    # base (in addition to ``required_fields``, which applies to every
    # integration type). Admin-managed knowledge bases are created as a data
    # source + knowledge base pair (``CreateDataSource`` → ``CreateKnowledgeBase``),
    # so they need source-connection fields an action connector does not. Kept
    # separate so a dual-capability connector's action-connector enable is not
    # forced to supply knowledge-base-only fields.
    kb_required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtensionEntry:
    """A single, immutable entry in the fixed extension catalog.

    An **extension** is a client-side AWS Quick add-in (a Microsoft Office
    add-in, or the browser extension) that the user installs in their app /
    browser. Unlike a :class:`CatalogEntry` connector, an extension has **no
    AWS Quick admin API** to create it, so its status is admin-attested (see
    :class:`ExtensionStatus`), not API-verified.

    ``suite_gate`` is one of ``microsoft_365``, ``google_workspace``, or ``any``.
    ``docs_url`` is the official install guide. ``order`` gives a stable display
    order within a suite.
    """

    key: str
    display_name: str
    suite_gate: str  # Literal["microsoft_365", "google_workspace", "any"]
    docs_url: str
    description: str = ""


@dataclass
class ConnectorRecord:
    """The recorded state of a single connector for a tenant.

    Connectors absent from an :class:`OnboardingState` are treated as
    ``not_configured``. ``last_changed_at`` is an ISO-8601 UTC timestamp and
    ``changed_by`` is the principal from the request context; both are ``None``
    until the connector's state is first changed.
    """

    status: ConnectorStatus
    last_changed_at: str | None = None
    changed_by: str | None = None


@dataclass
class ExtensionRecord:
    """The admin-attested state of a single AWS Quick extension for a tenant.

    Mirrors :class:`ConnectorRecord` but carries an :class:`ExtensionStatus`
    (``installed`` / ``skipped`` / ``not_configured``). Extensions absent from an
    :class:`OnboardingState` are treated as ``not_configured``.
    """

    status: ExtensionStatus
    last_changed_at: str | None = None
    changed_by: str | None = None


@dataclass
class OnboardingState:
    """The durably persisted, per-tenant onboarding record.

    ``suite`` is ``None`` when no productivity suite has been stored yet.
    ``connectors`` maps a Connector_Key to its :class:`ConnectorRecord`;
    unrecorded connectors default to ``not_configured`` when projected.
    ``extensions`` maps an Extension_Key to its :class:`ExtensionRecord`;
    unrecorded extensions default to ``not_configured`` when projected.
    """

    tenant_id: str
    suite: Suite | None = None
    connectors: dict[str, ConnectorRecord] = field(default_factory=dict)
    extensions: dict[str, ExtensionRecord] = field(default_factory=dict)


@dataclass
class ToolResult:
    """The structured response every MCP server tool returns.

    ``status`` is a :class:`ResultStatus`. ``message`` is a confirmation (for
    ``success``/``already_configured``) or an actionable cause-and-remedy
    description (otherwise). ``data`` carries status-specific fields and never
    contains raw credentials or internal stack detail.
    """

    status: ResultStatus
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain ``dict`` representation with enum values as strings."""
        return {
            "status": self.status.value,
            "message": self.message,
            "data": _to_jsonable(self.data),
        }

    def to_json(self) -> str:
        """Serialize this result to a JSON string."""
        return json.dumps(self.to_dict())


def _to_jsonable(value: Any) -> Any:
    """Recursively convert enums and dataclasses into JSON-serializable values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass_instance(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def is_dataclass_instance(value: Any) -> bool:
    """Return ``True`` if ``value`` is a dataclass instance (not the class)."""
    from dataclasses import is_dataclass

    return is_dataclass(value) and not isinstance(value, type)
