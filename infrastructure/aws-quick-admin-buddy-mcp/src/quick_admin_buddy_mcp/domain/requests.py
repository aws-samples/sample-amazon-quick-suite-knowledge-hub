"""Request models for the onboarding operations.

Each operation that takes more than one caller-supplied value gets its own
immutable request model. The models are keyword-only, so a call site names
every field (``EnableActionConnectorRequest(connector_key="outlook", ...)``)
and two optional string fields can never be swapped by position. Adding a field
is a change to one model, not to every caller.

The models carry the raw values the caller supplied. Validation (catalog keys,
auth types, id prefixes, principals, and so on) stays in
:class:`~quick_admin_buddy_mcp.domain.service.OnboardingService`, which turns
every problem into a ``validation_failure`` Tool_Result rather than raising.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class EnableActionConnectorRequest:
    """Inputs for ``enable_action_connector`` (``CreateActionConnector``).

    ``auth_params`` may carry only non-secret values (for example ``client_id``
    or ``domain_url``). Secret values must come from the Secrets Manager secret
    named by ``secret_id``.
    """

    connector_key: str
    auth_type: str | None = None
    secret_id: str | None = None
    auth_params: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    share_with: str | None = None
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class EnableKnowledgeBaseRequest:
    """Inputs for ``enable_kb_integration`` (data source + knowledge base).

    ``owner`` is required by the service (it returns ``validation_failure``
    without one). ``share_with`` is accepted for parity with the tool signature
    but is not used for knowledge bases; use ``share_kb_integration`` instead.
    """

    connector_key: str
    auth_type: str | None = None
    secret_id: str | None = None
    auth_params: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    owner: str | None = None
    share_with: str | None = None
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class DisableActionConnectorRequest:
    """Inputs for ``disable_action_connector`` (``DeleteActionConnector``)."""

    connector_key: str
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class DisableKnowledgeBaseRequest:
    """Inputs for ``disable_kb_integration`` (knowledge base + data source)."""

    connector_key: str
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class ShareActionConnectorRequest:
    """Inputs for ``share_action_connector``."""

    connector_key: str
    share_with: str
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class ShareKnowledgeBaseRequest:
    """Inputs for ``share_kb_integration`` (grants the VIEWER role)."""

    connector_key: str
    share_with: str
    connector_id_prefix: str | None = None
    principal: str | None = None


@dataclass(frozen=True, kw_only=True)
class SetExtensionStatusRequest:
    """Inputs for ``set_extension_status``."""

    extension_key: str
    status: str
    principal: str | None = None
