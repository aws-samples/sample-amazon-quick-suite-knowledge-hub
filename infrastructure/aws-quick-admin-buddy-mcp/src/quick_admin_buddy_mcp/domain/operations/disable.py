"""Disable operations: delete an action connector or a knowledge base."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.operations.base import (
    _DEFAULT_PRINCIPAL,
    Operation,
    _invalid_id_prefix,
    _is_not_found,
    _record_key,
)
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    DisableKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    IntegrationType,
    ResultStatus,
    ToolResult,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminInternalError,
    resource_id,
)
from quick_admin_buddy_mcp.state.repository import (
    StateStoreError,
    TenantId,
)


class _DisableIntegration(Operation):
    """Shared delete and record logic for both disable operations."""

    def _disable_integration(
        self,
        tenant: TenantId,
        key: str,
        integration_type: IntegrationType,
        principal: str | None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Disable one integration of a connector.

        ``connector_id_prefix`` must match the prefix used at enable time so the
        derived resource id targets the same connector/knowledge base for
        deletion. If it is invalid (not ``[\\w\\-]+``) the delete is rejected
        with ``validation_failure`` before any API call.

        Ordered checks (first failing check wins):

        1. ``key`` must exist in the catalog → else ``validation_failure``.
        2. The connector must support this integration type → else
           ``validation_failure``.
        3. If this integration is not ``enabled`` (absent / not_configured) →
           ``already_configured``; no API call, no state change.
        4. Delete via ``DeleteActionConnector``, or ``DeleteKnowledgeBase`` then
           ``DeleteDataSource``, then persist ``not_configured``. API error →
           ``server_error``; internal → ``internal_error``; persistence failure
           after a successful delete → ``server_error``. A knowledge base that
           is already missing counts as deleted. Once the knowledge base is
           deleted the state is recorded ``not_configured`` even if the data
           source delete fails; that failure → ``server_error`` with
           ``data_source_id`` and ``data_source_deleted=False``.
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

        if _invalid_id_prefix(connector_id_prefix):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid connector id prefix {connector_id_prefix!r}. "
                    "A prefix may contain only letters, digits, underscores, and "
                    "hyphens (pattern [\\w\\-]+). Retry with the same prefix used "
                    "when the integration was enabled, or omit it to use the default."
                ),
                {**base_data, "invalid_connector_id_prefix": connector_id_prefix},
            )

        record_key = _record_key(key, integration_type)
        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The {label} for {key!r} was NOT disabled.",
                dict(base_data),
            )
        record = state.connectors.get(record_key)
        current_status = (
            record.status if record is not None else ConnectorStatus.NOT_CONFIGURED
        )
        if current_status != ConnectorStatus.ENABLED:
            return self._ok(
                ResultStatus.ALREADY_CONFIGURED,
                f"The {label} for {key!r} is already not configured.",
                dict(base_data),
            )

        # A knowledge base owns two resources (the KB and its backing data
        # source). Delete the knowledge base first (it references the data
        # source). Once the knowledge base is gone - deleted now, or already
        # missing in AWS Quick - it is recorded as not_configured even if
        # the data source cannot be deleted afterwards; that leftover is
        # reported separately.
        data_source_error: Exception | None = None
        try:
            if integration_type == IntegrationType.ACTION_CONNECTOR:
                self._quick.delete_action_connector(key, connector_id_prefix)
            else:
                try:
                    self._quick.delete_knowledge_base(key, connector_id_prefix)
                except QuickAdminApiError as exc:
                    if not _is_not_found(exc):
                        raise
                try:
                    self._quick.delete_data_source(key, connector_id_prefix)
                except (QuickAdminApiError, QuickAdminInternalError) as exc:
                    if not _is_not_found(exc):
                        data_source_error = exc
        except QuickAdminApiError as exc:
            return self._server_error(f"disabling the {label}", key, exc, (), base_data)
        except QuickAdminInternalError as exc:
            return self._internal_error(
                f"disabling the {label}", key, exc, (), base_data
            )

        acting = principal or _DEFAULT_PRINCIPAL
        try:
            self._state.set_connector_status(
                tenant, record_key, ConnectorStatus.NOT_CONFIGURED, acting
            )
        except StateStoreError as exc:
            return self._state_error(
                exc,
                (
                    f"The {label} for {key!r} WAS deleted in AWS Quick, but its "
                    "not_configured status could not be recorded on the backend."
                ),
                dict(base_data),
                status=ResultStatus.SERVER_ERROR,
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._ok(
                ResultStatus.SERVER_ERROR,
                (
                    f"The {label} for {key!r} was disabled through the AWS Quick "
                    f"API but its not_configured status could not be persisted: {exc}. "
                    "Retry the operation to reconcile the stored state."
                ),
                dict(base_data),
            )

        if data_source_error is not None:
            data_source_id = resource_id(key, connector_id_prefix)
            return self._ok(
                ResultStatus.SERVER_ERROR,
                (
                    f"The {label} for {key!r} was deleted and recorded as "
                    f"not_configured, but its data source {data_source_id!r} "
                    f"could not be deleted: {data_source_error}. Delete the data "
                    "source in AWS Quick before enabling this knowledge base "
                    "again; a leftover data source makes the next enable fail."
                ),
                {
                    **base_data,
                    "data_source_id": data_source_id,
                    "data_source_deleted": False,
                },
            )

        return self._ok(
            ResultStatus.SUCCESS,
            f"The {label} for {key!r} has been disabled.",
            dict(base_data),
        )


class DisableActionConnector(_DisableIntegration):
    """``disable_action_connector``: delete an action connector the server created."""

    def __call__(
        self, tenant: TenantId, request: DisableActionConnectorRequest
    ) -> ToolResult:
        """Disable a connector's **action connector** integration.

        ``request.connector_id_prefix`` must match the prefix used when the
        connector was enabled (default ``quickadminbuddy-``) so the derived
        ``ActionConnectorId`` targets the same resource.
        """
        return self._disable_integration(
            tenant,
            request.connector_key,
            IntegrationType.ACTION_CONNECTOR,
            request.principal,
            request.connector_id_prefix,
        )


class DisableKnowledgeBase(_DisableIntegration):
    """``disable_kb_integration``: delete a knowledge base and its data source."""

    def __call__(
        self, tenant: TenantId, request: DisableKnowledgeBaseRequest
    ) -> ToolResult:
        """Disable a connector's **knowledge base** integration.

        ``request.connector_id_prefix`` must match the prefix used when the
        knowledge base was enabled (default ``quickadminbuddy-``) so the derived
        ``KnowledgeBaseId`` targets the same resource.
        """
        return self._disable_integration(
            tenant,
            request.connector_key,
            IntegrationType.KNOWLEDGE_BASE,
            request.principal,
            request.connector_id_prefix,
        )
