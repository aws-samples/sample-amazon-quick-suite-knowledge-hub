"""Extension operations: list and record admin-attested extension status."""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.operations.base import (
    _DEFAULT_PRINCIPAL,
    _VALID_SUITES,
    Operation,
    _parse_suite,
)
from quick_admin_buddy_mcp.domain.requests import (
    SetExtensionStatusRequest,
)
from quick_admin_buddy_mcp.domain.types import (
    ExtensionStatus,
    ResultStatus,
    Suite,
    ToolResult,
)
from quick_admin_buddy_mcp.state.repository import (
    StateStoreError,
    TenantId,
)


class ListExtensions(Operation):
    """``list_extensions``: the suite's extensions and their attested status."""

    def __call__(self, tenant: TenantId, suite: str | None = None) -> ToolResult:
        """List the AWS Quick extensions and each one's admin-attested status.

        Extensions are client-side add-ins the user installs in their Office app
        or browser; there is **no AWS Quick admin API** for them, so the
        status is admin-declared (``installed`` / ``skipped`` / ``not_configured``),
        not API-verified.

        The set is suite-gated. If ``suite`` is given it is used; otherwise the
        tenant's stored suite is used. If neither is available, every extension
        is returned (both suites) so the caller can still see the catalog.

        On success → ``success`` with ``suite`` (the effective suite or ``None``)
        and an ``extensions`` list, each item carrying ``key``, ``display_name``,
        ``suite_gate``, ``docs_url``, ``description``, ``status``,
        ``last_changed_at``, and ``changed_by``.
        """
        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc, "The tenant's extensions could not be listed."
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._ok(
                ResultStatus.SERVER_ERROR,
                (
                    "The tenant's extensions could not be listed because the "
                    f"onboarding state could not be retrieved: {exc}. Retry."
                ),
                {},
            )

        effective_suite: Suite | None = None
        if suite is not None:
            parsed = _parse_suite(suite)
            if parsed is None:
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"Invalid productivity suite {suite!r}. "
                        f"Valid options are {_VALID_SUITES[0]!r} and "
                        f"{_VALID_SUITES[1]!r}. Retry with one of the valid "
                        "suite values."
                    ),
                    {"invalid_suite": suite, "valid_suites": list(_VALID_SUITES)},
                )
            effective_suite = parsed
        elif state.suite is not None:
            effective_suite = state.suite

        if effective_suite is not None:
            entries = self._extension_catalog.filter_by_suite(effective_suite)
        else:
            entries = self._extension_catalog.all()

        extensions = []
        for entry in entries:
            record = state.extensions.get(entry.key)
            status = (
                record.status.value
                if record is not None
                else ExtensionStatus.NOT_CONFIGURED.value
            )
            extensions.append(
                {
                    "key": entry.key,
                    "display_name": entry.display_name,
                    "suite_gate": entry.suite_gate,
                    "docs_url": entry.docs_url,
                    "description": entry.description,
                    "status": status,
                    "last_changed_at": record.last_changed_at if record else None,
                    "changed_by": record.changed_by if record else None,
                }
            )

        return self._ok(
            ResultStatus.SUCCESS,
            "Returning the AWS Quick extensions and their statuses.",
            {
                "suite": effective_suite.value if effective_suite else None,
                "extensions": extensions,
            },
        )


class SetExtensionStatus(Operation):
    """``set_extension_status``: record an extension's admin-attested status."""

    def __call__(
        self, tenant: TenantId, request: SetExtensionStatusRequest
    ) -> ToolResult:
        """Record the admin-attested status of an extension.

        ``request.status`` must be one of ``installed`` / ``skipped`` /
        ``not_configured``. The extension ``key`` must exist in the extension
        catalog, and must be valid for the tenant's stored suite (if a suite is
        stored) — a suite mismatch returns ``validation_failure`` so, e.g., the
        browser extension is not marked installed under Microsoft 365.

        This records an **admin declaration**; it performs no AWS Quick API
        call (extensions have none). On success → ``success`` echoing the new
        status.
        """
        key = request.extension_key
        status = request.status
        principal = request.principal
        entry = self._extension_catalog.get(key)
        if entry is None:
            valid = [e.key for e in self._extension_catalog.all()]
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Unknown extension {key!r}. Valid extension keys are: "
                    f"{', '.join(valid)}."
                ),
                {"extension_key": key, "valid_extensions": valid},
            )

        try:
            parsed_status = ExtensionStatus(status)
        except ValueError:
            valid = [s.value for s in ExtensionStatus]
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid extension status {status!r}. Valid values are: "
                    f"{', '.join(valid)}."
                ),
                {"extension_key": key, "valid_statuses": valid},
            )

        # Suite gate: if a suite is stored, the extension must be valid for it.
        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The status of extension {key!r} was NOT recorded.",
                {"extension_key": key},
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._internal_error(
                "reading the onboarding state", key, exc, (), {"extension_key": key}
            )

        if state.suite is not None and entry.suite_gate not in (
            state.suite.value,
            "any",
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"The extension {key!r} is gated to {entry.suite_gate!r}, which "
                    f"does not match the tenant's suite {state.suite.value!r}. "
                    "It cannot be configured under the current suite."
                ),
                {
                    "extension_key": key,
                    "suite_gate": entry.suite_gate,
                    "stored_suite": state.suite.value,
                },
            )

        acting = principal or _DEFAULT_PRINCIPAL
        try:
            self._state.set_extension_status(tenant, key, parsed_status, acting)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The status of extension {key!r} was NOT recorded.",
                {"extension_key": key},
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            return self._internal_error(
                "recording the extension status", key, exc, (), {"extension_key": key}
            )

        return self._ok(
            ResultStatus.SUCCESS,
            (
                f"Recorded the {entry.display_name!r} extension as "
                f"{parsed_status.value!r}."
            ),
            {"extension_key": key, "status": parsed_status.value},
        )
