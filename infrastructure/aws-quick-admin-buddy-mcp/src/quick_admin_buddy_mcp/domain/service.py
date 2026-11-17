"""The OnboardingService: one entry point over the onboarding operations.

Each onboarding tool is implemented by its own operation class in
:mod:`quick_admin_buddy_mcp.domain.operations` (for example
:class:`~quick_admin_buddy_mcp.domain.operations.EnableActionConnector`), so a
change to one tool touches one class. :class:`OnboardingService` builds all of
them once from the shared collaborators and exposes them two ways:

* ``service.operations.<tool_name>(...)`` - the operation objects themselves,
  one attribute per MCP tool. The MCP tool layer calls these.
* the methods below (``enable_action_connector``, ``list_catalog``, and so on),
  which keep the original service API and delegate to the matching operation.

Two integration types
---------------------
AWS Quick exposes two integration-creation APIs, mirrored by two
enable/disable/share operation pairs:

* action connectors → ``CreateActionConnector``
* knowledge bases → ``CreateDataSource`` + ``CreateKnowledgeBase``

A single catalog connector may support one or both (see the catalog capability
flags). The two integrations are tracked independently per connector, keyed by
``"<connector_key>:<integration_type>"`` in the onboarding state.

Authentication
--------------
Both enable operations take an ``auth_type`` and its required parameters.
Secret values come only from a Secrets Manager secret (``secret_id`` - the
secret **name/id only, never an ARN**); a secret value passed inline is
rejected with ``validation_failure``. Non-secret values may also be passed
inline (``auth_params``). When required parameters are missing, the operation
returns a ``validation_failure`` that names exactly what is missing and includes
the secret contract. Errors raised by the underlying Create* APIs are surfaced
(sanitized) so the agent can help the admin troubleshoot.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog, ExtensionCatalog
from quick_admin_buddy_mcp.domain.operations import (
    OperationContext,
    Operations,
    build_operations,
)
from quick_admin_buddy_mcp.domain.operations.base import _record_key
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    DisableKnowledgeBaseRequest,
    EnableActionConnectorRequest,
    EnableKnowledgeBaseRequest,
    SetExtensionStatusRequest,
    ShareActionConnectorRequest,
    ShareKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.types import ToolResult
from quick_admin_buddy_mcp.quick.client import QuickAdminClient
from quick_admin_buddy_mcp.quick.secrets import SecretResolver
from quick_admin_buddy_mcp.state.repository import StateRepository, TenantId

__all__ = ["OnboardingService", "_record_key"]


class OnboardingService:
    """Builds the onboarding operations once and delegates to them.

    Instances hold no mutable state of their own; all tenant state lives behind
    the injected :class:`StateRepository`. Every returned :class:`ToolResult` is
    sanitized before it leaves its operation.
    """

    def __init__(
        self,
        catalog: ConnectorCatalog,
        state: StateRepository,
        quick: QuickAdminClient,
        secrets: SecretResolver | None = None,
        extension_catalog: ExtensionCatalog | None = None,
    ) -> None:
        self.operations: Operations = build_operations(
            OperationContext(
                catalog=catalog,
                state=state,
                quick=quick,
                secrets=secrets,
                # The extension catalog is a fixed constant; default-construct
                # it so callers that omit it keep working.
                extension_catalog=extension_catalog or ExtensionCatalog(),
            )
        )

    # -- catalog and suite -------------------------------------------------

    def list_catalog(self, suite: str | None) -> ToolResult:
        """List the connector catalog, optionally filtered by productivity suite."""
        return self.operations.list_connector_catalog(suite)

    def set_suite(self, tenant: TenantId, suite: str) -> ToolResult:
        """Record the tenant's productivity suite."""
        return self.operations.set_productivity_suite(tenant, suite)

    # -- integrations ------------------------------------------------------

    def enable_action_connector(
        self, tenant: TenantId, request: EnableActionConnectorRequest
    ) -> ToolResult:
        """Enable a connector as an action connector (``CreateActionConnector``)."""
        return self.operations.enable_action_connector(tenant, request)

    def disable_action_connector(
        self, tenant: TenantId, request: DisableActionConnectorRequest
    ) -> ToolResult:
        """Disable a connector's action connector integration."""
        return self.operations.disable_action_connector(tenant, request)

    def enable_kb_integration(
        self, tenant: TenantId, request: EnableKnowledgeBaseRequest
    ) -> ToolResult:
        """Enable a connector as a knowledge base (data source + knowledge base)."""
        return self.operations.enable_kb_integration(tenant, request)

    def disable_kb_integration(
        self, tenant: TenantId, request: DisableKnowledgeBaseRequest
    ) -> ToolResult:
        """Disable a connector's knowledge base integration."""
        return self.operations.disable_kb_integration(tenant, request)

    def share_action_connector(
        self, tenant: TenantId, request: ShareActionConnectorRequest
    ) -> ToolResult:
        """Share an existing action connector with a Quick user or group."""
        return self.operations.share_action_connector(tenant, request)

    def share_kb_integration(
        self, tenant: TenantId, request: ShareKnowledgeBaseRequest
    ) -> ToolResult:
        """Share an existing knowledge base with a Quick user or group (VIEWER)."""
        return self.operations.share_kb_integration(tenant, request)

    # -- reads -------------------------------------------------------------

    def get_configuration(self, tenant: TenantId) -> ToolResult:
        """Return the tenant's current onboarding configuration."""
        return self.operations.get_tenant_configuration(tenant)

    def get_summary(self, tenant: TenantId) -> ToolResult:
        """Return a structured recap of the tenant's onboarding."""
        return self.operations.get_onboarding_summary(tenant)

    # -- extensions ----------------------------------------------------------

    def list_extensions(self, tenant: TenantId, suite: str | None = None) -> ToolResult:
        """List the AWS Quick extensions and each one's admin-attested status."""
        return self.operations.list_extensions(tenant, suite)

    def set_extension_status(
        self, tenant: TenantId, request: SetExtensionStatusRequest
    ) -> ToolResult:
        """Record the admin-attested status of an extension."""
        return self.operations.set_extension_status(tenant, request)
