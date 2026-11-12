"""One class per onboarding operation.

:func:`build_operations` builds every operation once from the shared
collaborators. :class:`Operations` holds them, one attribute per MCP tool, so a
caller (the MCP tool layer, or :class:`OnboardingService`) calls the operation it
needs, for example ``operations.enable_action_connector(tenant, request)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from quick_admin_buddy_mcp.domain.operations.base import Operation, OperationContext
from quick_admin_buddy_mcp.domain.operations.disable import (
    DisableActionConnector,
    DisableKnowledgeBase,
)
from quick_admin_buddy_mcp.domain.operations.enable import (
    EnableActionConnector,
    EnableKnowledgeBase,
)
from quick_admin_buddy_mcp.domain.operations.extensions import (
    ListExtensions,
    SetExtensionStatus,
)
from quick_admin_buddy_mcp.domain.operations.reads import (
    GetOnboardingSummary,
    GetTenantConfiguration,
)
from quick_admin_buddy_mcp.domain.operations.share import (
    ShareActionConnector,
    ShareKnowledgeBase,
)
from quick_admin_buddy_mcp.domain.operations.suite import (
    ListConnectorCatalog,
    SetProductivitySuite,
)


@dataclass(frozen=True)
class Operations:
    """Every onboarding operation, one attribute per MCP tool."""

    list_connector_catalog: ListConnectorCatalog
    set_productivity_suite: SetProductivitySuite
    enable_action_connector: EnableActionConnector
    enable_kb_integration: EnableKnowledgeBase
    disable_action_connector: DisableActionConnector
    disable_kb_integration: DisableKnowledgeBase
    share_action_connector: ShareActionConnector
    share_kb_integration: ShareKnowledgeBase
    get_tenant_configuration: GetTenantConfiguration
    get_onboarding_summary: GetOnboardingSummary
    list_extensions: ListExtensions
    set_extension_status: SetExtensionStatus


def build_operations(ctx: OperationContext) -> Operations:
    """Build every operation once from the shared collaborators."""
    return Operations(
        list_connector_catalog=ListConnectorCatalog(ctx),
        set_productivity_suite=SetProductivitySuite(ctx),
        enable_action_connector=EnableActionConnector(ctx),
        enable_kb_integration=EnableKnowledgeBase(ctx),
        disable_action_connector=DisableActionConnector(ctx),
        disable_kb_integration=DisableKnowledgeBase(ctx),
        share_action_connector=ShareActionConnector(ctx),
        share_kb_integration=ShareKnowledgeBase(ctx),
        get_tenant_configuration=GetTenantConfiguration(ctx),
        get_onboarding_summary=GetOnboardingSummary(ctx),
        list_extensions=ListExtensions(ctx),
        set_extension_status=SetExtensionStatus(ctx),
    )


__all__ = [
    "DisableActionConnector",
    "DisableKnowledgeBase",
    "EnableActionConnector",
    "EnableKnowledgeBase",
    "GetOnboardingSummary",
    "GetTenantConfiguration",
    "ListConnectorCatalog",
    "ListExtensions",
    "Operation",
    "OperationContext",
    "Operations",
    "SetExtensionStatus",
    "SetProductivitySuite",
    "ShareActionConnector",
    "ShareKnowledgeBase",
    "build_operations",
]
