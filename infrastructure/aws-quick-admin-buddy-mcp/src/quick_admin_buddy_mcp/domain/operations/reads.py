"""Read operations: the tenant configuration and the onboarding summary."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.operations.base import (
    Operation,
    _record_key,
)
from quick_admin_buddy_mcp.domain.types import (
    CatalogEntry,
    ConnectorStatus,
    ExtensionStatus,
    IntegrationType,
    ResultStatus,
    Stage,
    ToolResult,
)
from quick_admin_buddy_mcp.state.repository import (
    StateStoreError,
    TenantId,
)


class _ReadOperation(Operation):
    """Shared per-connector projection for both read operations."""

    def _connector_view(self, state, entry: CatalogEntry):
        """Return ``(status, last_changed_at, changed_by, integrations)`` for a key.

        Aggregates the per-integration records (``<key>:action_connector`` and
        ``<key>:knowledge_base``) plus any legacy bare-key record. The aggregate
        status is ``enabled`` if any supported integration is enabled.
        """
        integrations: dict[str, str] = {}
        candidates: list = []

        if entry.supports_action:
            integrations[IntegrationType.ACTION_CONNECTOR.value] = (
                ConnectorStatus.NOT_CONFIGURED.value
            )
        if entry.supports_kb:
            integrations[IntegrationType.KNOWLEDGE_BASE.value] = (
                ConnectorStatus.NOT_CONFIGURED.value
            )

        for integration_type in IntegrationType:
            record = state.connectors.get(_record_key(entry.key, integration_type))
            if record is not None:
                integrations[integration_type.value] = record.status.value
                candidates.append(record)

        legacy = state.connectors.get(entry.key)
        if legacy is not None:
            candidates.append(legacy)

        any_enabled = any(r.status == ConnectorStatus.ENABLED for r in candidates)
        any_error = any(r.status == ConnectorStatus.ERROR for r in candidates)
        if any_enabled:
            status = ConnectorStatus.ENABLED.value
        elif any_error:
            status = ConnectorStatus.ERROR.value
        else:
            status = ConnectorStatus.NOT_CONFIGURED.value

        # Most recent change metadata across the candidate records.
        last_changed_at = None
        changed_by = None
        for record in candidates:
            if record.last_changed_at is not None and (
                last_changed_at is None or record.last_changed_at > last_changed_at
            ):
                last_changed_at = record.last_changed_at
                changed_by = record.changed_by

        return status, last_changed_at, changed_by, integrations


class GetTenantConfiguration(_ReadOperation):
    """``get_tenant_configuration``: the stored suite and every connector's status."""

    def __call__(self, tenant: TenantId) -> ToolResult:
        """Return the tenant's current onboarding configuration.

        On success → ``success`` with the stored ``suite`` (or ``None``) and a
        ``connectors`` projection of **all fifteen** catalog keys. Each entry
        reports an aggregate ``status`` (``enabled`` if either integration is
        enabled, else ``not_configured``), the most recent ``last_changed_at`` /
        ``changed_by``, and an ``integrations`` breakdown with the per-type
        status for ``action_connector`` and ``knowledge_base``. A read failure →
        ``server_error`` with no partial data.
        """
        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc, "The tenant's onboarding configuration could not be read."
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._ok(
                ResultStatus.SERVER_ERROR,
                (
                    "The tenant's onboarding configuration could not be "
                    f"retrieved: {exc}. The required onboarding data is "
                    "unavailable; retry the operation."
                ),
                {},
            )

        connectors: dict[str, dict] = {}
        for entry in self._catalog.all():
            status, last_changed_at, changed_by, integrations = self._connector_view(
                state, entry
            )
            connectors[entry.key] = {
                "status": status,
                "last_changed_at": last_changed_at,
                "changed_by": changed_by,
                "integrations": integrations,
            }

        suite_value = state.suite.value if state.suite is not None else None

        # Extensions: project every extension valid for the stored suite (or all
        # when no suite is stored), with its admin-attested status.
        if state.suite is not None:
            ext_entries = self._extension_catalog.filter_by_suite(state.suite)
        else:
            ext_entries = self._extension_catalog.all()
        extensions: dict[str, dict] = {}
        for ext in ext_entries:
            record = state.extensions.get(ext.key)
            extensions[ext.key] = {
                "status": (
                    record.status.value
                    if record is not None
                    else ExtensionStatus.NOT_CONFIGURED.value
                ),
                "last_changed_at": record.last_changed_at if record else None,
                "changed_by": record.changed_by if record else None,
            }

        return self._ok(
            ResultStatus.SUCCESS,
            "Returning the tenant's current onboarding configuration.",
            {"suite": suite_value, "connectors": connectors, "extensions": extensions},
        )


class GetOnboardingSummary(_ReadOperation):
    """``get_onboarding_summary``: a stage-grouped recap of the onboarding."""

    def __call__(self, tenant: TenantId) -> ToolResult:
        """Return a structured recap of the tenant's onboarding.

        On success → ``success`` with the stored ``suite`` and a ``stages``
        mapping keyed by all four stage names. Each catalog connector appears
        under exactly one of the three catalog stages with a final status in
        ``{enabled, skipped, not_configured}`` — ``enabled`` if any of its
        integrations is enabled. A read failure → ``server_error``.
        """
        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc, "The tenant's onboarding summary could not be built."
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._ok(
                ResultStatus.SERVER_ERROR,
                (
                    "The tenant's onboarding summary could not be built because "
                    f"the stored productivity suite could not be retrieved: {exc}. "
                    "Retry the operation."
                ),
                {},
            )

        suite_value = state.suite.value if state.suite is not None else None

        stages: dict[str, dict] = {
            Stage.PRODUCTIVITY_SUITE.value: {"suite": suite_value},
            Stage.PRODUCTIVITY_CONNECTORS.value: {"connectors": {}},
            Stage.ENTERPRISE_KNOWLEDGE.value: {"connectors": {}},
            Stage.OTHER_CONNECTORS.value: {"connectors": {}},
        }

        for entry in self._catalog.all():
            status, _, _, _ = self._connector_view(state, entry)
            final_status = (
                "enabled"
                if status == ConnectorStatus.ENABLED.value
                else "not_configured"
            )
            stages[entry.stage]["connectors"][entry.key] = final_status

        # Extensions summary: status per extension valid for the stored suite
        # (or all when no suite is stored). Kept as a top-level block since
        # extensions are not connectors and do not belong to a connector stage.
        if state.suite is not None:
            ext_entries = self._extension_catalog.filter_by_suite(state.suite)
        else:
            ext_entries = self._extension_catalog.all()
        extensions_summary: dict[str, str] = {}
        for ext in ext_entries:
            record = state.extensions.get(ext.key)
            extensions_summary[ext.key] = (
                record.status.value
                if record is not None
                else ExtensionStatus.NOT_CONFIGURED.value
            )

        return self._ok(
            ResultStatus.SUCCESS,
            "Returning the tenant's onboarding summary.",
            {
                "suite": suite_value,
                "stages": stages,
                "extensions": extensions_summary,
            },
        )
