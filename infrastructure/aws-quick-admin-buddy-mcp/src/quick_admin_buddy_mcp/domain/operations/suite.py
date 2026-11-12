"""Catalog listing and productivity-suite operations."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.operations.base import (
    _VALID_SUITES,
    Operation,
    _entry_view,
    _parse_suite,
)
from quick_admin_buddy_mcp.domain.types import (
    ConnectorStatus,
    ResultStatus,
    Suite,
    ToolResult,
)
from quick_admin_buddy_mcp.state.repository import (
    StateStoreError,
    TenantId,
)


class ListConnectorCatalog(Operation):
    """``list_connector_catalog``: list the connector catalog, optionally filtered by suite."""

    def __call__(self, suite: str | None) -> ToolResult:
        """List the connector catalog, optionally filtered by Productivity_Suite.

        * No ``suite`` filter → ``success`` with all fifteen catalog entries.
        * A valid suite → ``success`` with the entries whose Suite_Gate matches
          the suite or is ``any``.
        * Any other string → ``validation_failure`` naming the invalid value.
        """
        if suite is None:
            entries = [_entry_view(e) for e in self._catalog.all()]
            return self._ok(
                ResultStatus.SUCCESS,
                "Returning all connector catalog entries.",
                {"connectors": entries},
            )

        parsed = _parse_suite(suite)
        if parsed is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid productivity suite {suite!r}. "
                    f"Valid options are {_VALID_SUITES[0]!r} and {_VALID_SUITES[1]!r}. "
                    "Retry with one of the valid suite values."
                ),
                {
                    "invalid_suite": suite,
                    "valid_suites": list(_VALID_SUITES),
                },
            )

        entries = [_entry_view(e) for e in self._catalog.filter_by_suite(parsed)]
        return self._ok(
            ResultStatus.SUCCESS,
            f"Returning connector catalog entries for suite {parsed.value!r}.",
            {"suite": parsed.value, "connectors": entries},
        )


class SetProductivitySuite(Operation):
    """``set_productivity_suite``: record the tenant's productivity suite."""

    def __call__(self, tenant: TenantId, suite: str) -> ToolResult:
        """Record the tenant's Productivity_Suite.

        * Invalid ``suite`` → ``validation_failure``; no state change.
        * No suite stored → persist and return ``success``.
        * Same suite already stored → ``already_configured``.
        * A different suite while connectors gated to the stored suite are
          enabled → ``suite_conflict`` listing those connectors; no change.
        * A different suite with no gated enabled connectors → persist + success.
        """
        parsed = _parse_suite(suite)
        if parsed is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid productivity suite {suite!r}. "
                    f"Valid options are {_VALID_SUITES[0]!r} and {_VALID_SUITES[1]!r}. "
                    "Retry with one of the valid suite values."
                ),
                {
                    "invalid_suite": suite,
                    "valid_suites": list(_VALID_SUITES),
                },
            )

        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The productivity suite {parsed.value!r} was NOT recorded.",
                {"requested_suite": parsed.value},
            )
        stored = state.suite

        if stored is None:
            try:
                self._state.put_suite(tenant, parsed)
            except StateStoreError as exc:
                return self._state_error(
                    exc,
                    f"The productivity suite {parsed.value!r} was NOT recorded.",
                    {"requested_suite": parsed.value},
                )
            return self._ok(
                ResultStatus.SUCCESS,
                f"Productivity suite set to {parsed.value!r}.",
                {"suite": parsed.value},
            )

        if stored == parsed:
            return self._ok(
                ResultStatus.ALREADY_CONFIGURED,
                f"Productivity suite is already set to {stored.value!r}.",
                {"suite": stored.value},
            )

        conflicting = self._conflicting_connectors(state.connectors, stored)
        if conflicting:
            return self._ok(
                ResultStatus.SUITE_CONFLICT,
                (
                    f"Cannot switch productivity suite from {stored.value!r} to "
                    f"{parsed.value!r} while connectors gated to {stored.value!r} "
                    f"are enabled: {', '.join(conflicting)}. "
                    "Disable those connectors before switching suites."
                ),
                {
                    "current_suite": stored.value,
                    "requested_suite": parsed.value,
                    "conflicting_connectors": conflicting,
                },
            )

        try:
            self._state.put_suite(tenant, parsed)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                (
                    f"The productivity suite was NOT changed from "
                    f"{stored.value!r} to {parsed.value!r}."
                ),
                {"requested_suite": parsed.value, "current_suite": stored.value},
            )
        return self._ok(
            ResultStatus.SUCCESS,
            f"Productivity suite changed from {stored.value!r} to {parsed.value!r}.",
            {"suite": parsed.value, "previous_suite": stored.value},
        )

    def _conflicting_connectors(self, connectors, stored_suite: Suite) -> list[str]:
        """Return enabled connectors gated specifically to ``stored_suite``.

        A connector is counted once (by its catalog key) if any of its tracked
        integrations is ``enabled`` and the connector is gated to the stored
        suite (not ``any``). Result preserves catalog definition order.
        """
        enabled_keys = {
            self._base_key(record_key)
            for record_key, record in connectors.items()
            if record.status == ConnectorStatus.ENABLED
        }
        conflicting: list[str] = []
        for entry in self._catalog.all():
            if entry.key in enabled_keys and entry.suite_gate == stored_suite.value:
                conflicting.append(entry.key)
        return conflicting

    @staticmethod
    def _base_key(record_key: str) -> str:
        """Return the catalog key from a possibly-composite state record key.

        Record keys are ``"<key>:<integration_type>"``; a legacy bare key (no
        ``:``) is returned unchanged for backward compatibility.
        """
        return record_key.split(":", 1)[0]
