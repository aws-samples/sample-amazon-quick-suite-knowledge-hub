"""Share operations: grant access to an existing integration."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.operations.base import (
    Operation,
    _invalid_id_prefix,
)
from quick_admin_buddy_mcp.domain.requests import (
    ShareActionConnectorRequest,
    ShareKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.types import (
    IntegrationType,
    ResultStatus,
    ToolResult,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminInternalError,
)
from quick_admin_buddy_mcp.state.repository import (
    TenantId,
)


class _ShareIntegration(Operation):
    """Shared validation and permission-grant logic for both share operations."""

    def _share_integration(
        self,
        tenant: TenantId,
        key: str,
        integration_type: IntegrationType,
        share_with: str,
        principal: str | None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Grant a Quick principal owner-equivalent permissions on one existing
        integration of a connector.

        Ordered checks (first failing check wins):

        1. ``key`` must exist in the catalog → else ``validation_failure``.
        2. The connector must support this integration type → else
           ``validation_failure``.
        3. ``share_with`` must be a non-empty principal → else
           ``validation_failure``.
        4. ``connector_id_prefix`` (if given) must match ``[\\w\\-]+`` → else
           ``validation_failure`` so the derived resource id stays API-valid.
        5. Share via ``UpdateActionConnectorPermissions`` /
           ``UpdateKnowledgeBasePermissions``. API error → ``server_error``;
           internal → ``internal_error``.

        This does NOT require the integration to be ``enabled`` in local state:
        the resource is assumed to already exist in AWS Quick (the API call
        is the source of truth and reports an error if it does not).
        """
        label = self._integration_label(integration_type)
        base_data = {"connector_key": key, "integration_type": integration_type.value}

        entry = self._catalog.get(key)
        if entry is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Unknown connector key {key!r}. "
                    "Retry with a connector key from the catalog."
                ),
                dict(base_data),
            )

        if (
            integration_type == IntegrationType.ACTION_CONNECTOR
            and not entry.supports_action
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                f"Connector {key!r} does not support an action connector.",
                dict(base_data),
            )
        if integration_type == IntegrationType.KNOWLEDGE_BASE and not entry.supports_kb:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                f"Connector {key!r} does not support a knowledge base integration.",
                dict(base_data),
            )

        if not share_with or not share_with.strip():
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    "A principal to share with is required. Provide a Quick user "
                    "or group - a bare user name (assumed in the 'default' "
                    "namespace) or a full user/group ARN."
                ),
                dict(base_data),
            )

        if _invalid_id_prefix(connector_id_prefix):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid connector id prefix {connector_id_prefix!r}. "
                    "A prefix may contain only letters, digits, underscores, and "
                    "hyphens (pattern [\\w\\-]+). Retry with the same prefix used "
                    "when the integration was created, or omit it to use the default."
                ),
                {**base_data, "invalid_connector_id_prefix": connector_id_prefix},
            )

        missing = self._missing_principal(
            "share_with", share_with, label, key, base_data
        )
        if missing is not None:
            return missing

        try:
            if integration_type == IntegrationType.ACTION_CONNECTOR:
                self._quick.share_action_connector(key, share_with, connector_id_prefix)
            else:
                self._quick.share_knowledge_base(key, share_with, connector_id_prefix)
        except QuickAdminApiError as exc:
            return self._server_error(f"sharing the {label}", key, exc, (), base_data)
        except QuickAdminInternalError as exc:
            return self._internal_error(f"sharing the {label}", key, exc, (), base_data)

        return self._ok(
            ResultStatus.SUCCESS,
            f"The {label} for {key!r} was shared with {share_with!r}.",
            {**base_data, "shared_with": share_with},
        )


class ShareActionConnector(_ShareIntegration):
    """``share_action_connector``: grant owner-equivalent access to an action connector."""

    def __call__(
        self, tenant: TenantId, request: ShareActionConnectorRequest
    ) -> ToolResult:
        """Share an existing **action connector** with a Quick user or group
        (``UpdateActionConnectorPermissions``).

        ``request.share_with`` is the Quick principal to grant owner-equivalent
        permissions to - a bare user name (assumed in the ``default`` namespace)
        or a full user/group ARN. Unlike ``share_with`` on the enable path (a
        create-time convenience), this operates on a connector that already
        exists in AWS Quick, so no local ``enabled`` state is required.

        ``request.connector_id_prefix`` must match the prefix used when the
        connector was created (default ``quickadminbuddy-``) so the derived
        ``ActionConnectorId`` targets the same resource.
        """
        return self._share_integration(
            tenant,
            request.connector_key,
            IntegrationType.ACTION_CONNECTOR,
            request.share_with,
            request.principal,
            request.connector_id_prefix,
        )


class ShareKnowledgeBase(_ShareIntegration):
    """``share_kb_integration``: grant the VIEWER role on a knowledge base."""

    def __call__(
        self, tenant: TenantId, request: ShareKnowledgeBaseRequest
    ) -> ToolResult:
        """Share an existing **knowledge base** with a Quick user or group
        (``UpdateKnowledgeBasePermissions``).

        ``request.share_with`` is the Quick principal to grant the **VIEWER**
        role to (the only role besides the creation-time OWNER that AWS Quick
        accepts) - a bare user name (assumed in the ``default`` namespace) or a
        full user/group ARN. Operates on a knowledge base that already exists
        in AWS Quick, so no local ``enabled`` state is required.

        ``request.connector_id_prefix`` must match the prefix used when the
        knowledge base was created (default ``quickadminbuddy-``) so the
        derived ``KnowledgeBaseId`` targets the same resource.
        """
        return self._share_integration(
            tenant,
            request.connector_key,
            IntegrationType.KNOWLEDGE_BASE,
            request.share_with,
            request.principal,
            request.connector_id_prefix,
        )
