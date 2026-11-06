"""State Repository interface and an in-memory fake for domain tests.

The :class:`StateRepository` protocol abstracts durable persistence of
:class:`~quick_admin_buddy_mcp.domain.types.OnboardingState` behind a narrow
interface so the domain layer can be exercised with an in-memory fake in tests
and backed by DynamoDB at runtime.

The interface guarantees:

- ``get`` returns a fresh, empty :class:`OnboardingState` for the tenant when no
  state has been stored yet (never ``None``).
- state is isolated per tenant, so one tenant's writes are never visible to
  another tenant (Requirement 9.2).
- reads reflect the durably persisted state for the requested tenant
  (Requirement 9.3).

``TenantId`` is a plain string alias naming the tenant partition key.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from quick_admin_buddy_mcp.domain.types import (
    ConnectorRecord,
    ConnectorStatus,
    ExtensionRecord,
    ExtensionStatus,
    OnboardingState,
    Suite,
)

# A tenant identifier. A plain string alias; the value is the DynamoDB
# partition key for the tenant's Onboarding_State item.
TenantId = str


class StateStoreError(Exception):
    """The backend state store could not be read or written.

    Raised by a :class:`StateRepository` implementation when the persistence
    layer itself is unusable (as opposed to a caller mistake). It carries enough
    structured detail for the domain layer to tell the calling agent *what* is
    wrong with the backend infrastructure and *whether a retry can help*, so the
    agent can send the admin to review the deployment instead of retrying.

    Attributes:
        reason: A stable machine-readable cause. One of
            :data:`REASON_TABLE_NOT_FOUND`, :data:`REASON_ACCESS_DENIED`,
            :data:`REASON_ENCRYPTION_KEY_UNAVAILABLE`, :data:`REASON_UNAVAILABLE`.
        table_name: The state table the operation targeted.
        region: The AWS Region the repository talks to, when known.
        error_code: The underlying AWS error code (e.g.
            ``ResourceNotFoundException``) or exception class name.
        operation: The repository operation that failed (e.g. ``read``).
        retryable: ``True`` only when the failure may be transient (throttling,
            network); ``False`` when the infrastructure must be fixed first.
    """

    #: The state table does not exist (deleted, never created, wrong region/name).
    REASON_TABLE_NOT_FOUND = "table_not_found"
    #: The runtime's execution role is not allowed to use the table.
    REASON_ACCESS_DENIED = "access_denied"
    #: The table's KMS key is disabled, pending deletion, or not usable by the role.
    REASON_ENCRYPTION_KEY_UNAVAILABLE = "encryption_key_unavailable"
    #: Any other (possibly transient) failure reaching the state store.
    REASON_UNAVAILABLE = "unavailable"

    def __init__(
        self,
        reason: str,
        *,
        table_name: str,
        operation: str,
        error_code: str,
        region: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.reason = reason
        self.table_name = table_name
        self.region = region
        self.error_code = error_code
        self.operation = operation
        self.retryable = retryable
        super().__init__(self.describe())

    def describe(self) -> str:
        """Return a one-sentence, caller-safe description of the failure."""
        where = f"DynamoDB table {self.table_name!r}"
        if self.region:
            where += f" in region {self.region!r}"
        if self.reason == self.REASON_TABLE_NOT_FOUND:
            cause = "does not exist"
        elif self.reason == self.REASON_ACCESS_DENIED:
            cause = "is not accessible to the server's execution role"
        elif self.reason == self.REASON_ENCRYPTION_KEY_UNAVAILABLE:
            cause = "cannot be used because its KMS encryption key is unavailable"
        else:
            cause = "could not be reached"
        return (
            f"The backend onboarding state store ({where}) {cause} "
            f"({self.error_code}) during a {self.operation}."
        )

    def remediation(self) -> str:
        """Return the action the admin should take for this failure."""
        if self.reason == self.REASON_TABLE_NOT_FOUND:
            return (
                "This is a backend infrastructure problem, not a problem with the "
                "request, and retrying will not help. Ask the AWS administrator to "
                "review the Quick Admin Buddy CloudFormation stack: confirm the "
                "table exists in that region with the name in the stack's "
                "OnboardingStateTableName parameter (it may have been deleted "
                "outside CloudFormation), recreate it to match the template if it "
                "is missing, then retry."
            )
        if self.reason == self.REASON_ACCESS_DENIED:
            return (
                "This is a backend infrastructure problem and retrying will not "
                "help. Ask the AWS administrator to check that the AgentCore "
                "Runtime execution role still grants DynamoDB access to this "
                "table (the stack's OnboardingStateTableAccess policy), then retry."
            )
        if self.reason == self.REASON_ENCRYPTION_KEY_UNAVAILABLE:
            return (
                "This is a backend infrastructure problem and retrying will not "
                "help. Ask the AWS administrator to check that the stack's KMS key "
                "is enabled, not pending deletion, and still usable by the "
                "AgentCore Runtime execution role (the OnboardingStateKmsAccess "
                "policy), then retry."
            )
        return (
            "This may be transient: retry once. If it keeps failing, ask the AWS "
            "administrator to review the Quick Admin Buddy backend (the DynamoDB "
            "table and the AgentCore Runtime's network and credentials)."
        )

    def to_data(self) -> dict:
        """Return the structured detail to include in a Tool_Result's ``data``."""
        return {
            "error_type": "state_store_unavailable",
            "reason": self.reason,
            "table_name": self.table_name,
            "region": self.region,
            "error_code": self.error_code,
            "operation": self.operation,
            "retryable": self.retryable,
        }


@runtime_checkable
class StateRepository(Protocol):
    """Durable persistence of per-tenant Onboarding_State.

    Implementations isolate state by tenant and return the durably persisted
    state on read. Concrete implementations include an in-memory fake (tests)
    and a DynamoDB-backed repository (runtime). When the backing store itself
    is unusable (missing table, denied access, unreachable), implementations
    raise :class:`StateStoreError` so callers can report the cause.
    """

    def get(self, tenant: TenantId) -> OnboardingState:
        """Return the tenant's persisted Onboarding_State.

        Returns a fresh, empty :class:`OnboardingState` (``suite=None`` and no
        connector records) when nothing has been stored for the tenant.
        """
        ...

    def put_suite(self, tenant: TenantId, suite: Suite) -> None:
        """Persist the tenant's chosen Productivity_Suite."""
        ...

    def set_connector_status(
        self,
        tenant: TenantId,
        key: str,
        status: ConnectorStatus,
        principal: str,
    ) -> None:
        """Persist a connector's status for the tenant.

        Records ``last_changed_at`` (current UTC time, ISO-8601) and
        ``changed_by`` (``principal``) alongside the new ``status``.
        """
        ...

    def set_extension_status(
        self,
        tenant: TenantId,
        key: str,
        status: ExtensionStatus,
        principal: str,
    ) -> None:
        """Persist an extension's admin-attested status for the tenant.

        Records ``last_changed_at`` (current UTC time, ISO-8601) and
        ``changed_by`` (``principal``) alongside the new :class:`ExtensionStatus`.
        """
        ...


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class InMemoryStateRepository:
    """An in-memory :class:`StateRepository` fake for domain tests.

    Stores one :class:`OnboardingState` per tenant in a dictionary. State is
    deep-copied on every read and write so that:

    - callers cannot mutate stored state by holding a reference to a returned
      object (reads return an independent copy), and
    - stored state cannot be mutated by a reference the caller passed in.

    This gives the same tenant-isolation and durable-read semantics as the
    DynamoDB repository without any external dependency.
    """

    def __init__(self) -> None:
        self._store: dict[TenantId, OnboardingState] = {}

    def get(self, tenant: TenantId) -> OnboardingState:
        """Return a deep copy of the tenant's state, or a fresh empty state."""
        stored = self._store.get(tenant)
        if stored is None:
            return OnboardingState(tenant_id=tenant)
        return copy.deepcopy(stored)

    def put_suite(self, tenant: TenantId, suite: Suite) -> None:
        """Persist the tenant's Productivity_Suite, creating state if needed."""
        state = self._store.get(tenant)
        if state is None:
            state = OnboardingState(tenant_id=tenant)
            self._store[tenant] = state
        state.suite = suite

    def set_connector_status(
        self,
        tenant: TenantId,
        key: str,
        status: ConnectorStatus,
        principal: str,
    ) -> None:
        """Persist a connector's status with change metadata for the tenant."""
        state = self._store.get(tenant)
        if state is None:
            state = OnboardingState(tenant_id=tenant)
            self._store[tenant] = state
        state.connectors[key] = ConnectorRecord(
            status=status,
            last_changed_at=_utc_now_iso(),
            changed_by=principal,
        )

    def set_extension_status(
        self,
        tenant: TenantId,
        key: str,
        status: ExtensionStatus,
        principal: str,
    ) -> None:
        """Persist an extension's status with change metadata for the tenant."""
        state = self._store.get(tenant)
        if state is None:
            state = OnboardingState(tenant_id=tenant)
            self._store[tenant] = state
        state.extensions[key] = ExtensionRecord(
            status=status,
            last_changed_at=_utc_now_iso(),
            changed_by=principal,
        )
