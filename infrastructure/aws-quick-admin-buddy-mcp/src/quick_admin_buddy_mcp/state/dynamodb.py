"""DynamoDB-backed :class:`StateRepository`.

Persists per-tenant :class:`~quick_admin_buddy_mcp.domain.types.OnboardingState`
to a single DynamoDB table (``OnboardingState``, partition key ``tenant_id``),
storing one item per tenant with this item shape:

.. code-block:: json

    {
      "tenant_id": "acme-corp",
      "suite": "microsoft_365",
      "connectors": {
        "outlook":    {"status": "enabled",        "last_changed_at": "2025-01-15T10:22:00Z", "changed_by": "admin@acme"},
        "sharepoint": {"status": "not_configured", "last_changed_at": null, "changed_by": null}
      }
    }

Design guarantees implemented here:

- **Per-tenant isolation.** Every operation keys the item by ``tenant_id``
  only, so an operation for one tenant can never read or write another tenant's
  item. This is inherent to the partition-key access pattern below.
- **Durable reads.** :meth:`get` reads the item by key and returns a fresh,
  empty :class:`OnboardingState` when the item is absent (never ``None``).
- **Writes complete before returning.** ``put_suite`` and
  ``set_connector_status`` issue a synchronous ``update_item`` and return only
  after it succeeds, so the change is durable before the caller sees a result.
- **Idempotency / convergence.** Both writes use idempotent ``SET`` update
  expressions rather than full-item ``Put`` operations. A ``SET`` of ``suite`` or
  of a single ``connectors.<key>`` entry produces the same item no matter how
  many times it runs, so a re-invoked, interrupted write converges to the same
  state. ``if_not_exists(connectors, {})`` guarantees the parent ``connectors``
  map exists before a nested set without clobbering connectors written by an
  earlier (possibly interrupted) invocation. Because the writes touch only the
  attribute(s) they own, they never overwrite unrelated state.
- **Actionable failures.** Every table call goes through :meth:`_call`, which
  re-raises AWS and botocore failures as
  :class:`~quick_admin_buddy_mcp.state.repository.StateStoreError` (missing
  table, denied access, unusable KMS key, or unreachable), so the calling agent
  learns that the backend infrastructure needs review instead of seeing an
  opaque tool failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from quick_admin_buddy_mcp.domain.types import (
    ConnectorRecord,
    ConnectorStatus,
    ExtensionRecord,
    ExtensionStatus,
    OnboardingState,
    Suite,
)
from quick_admin_buddy_mcp.state.repository import StateStoreError, TenantId

# AWS error codes that mean the state table itself is missing.
_TABLE_NOT_FOUND_CODES = frozenset({"ResourceNotFoundException"})

# AWS error codes that mean the caller's credentials may not use the table.
_ACCESS_DENIED_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "InvalidSignatureException",
        "ExpiredTokenException",
    }
)

# AWS error codes that may clear on their own, so a retry can help.
_TRANSIENT_CODES = frozenset(
    {
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "ThrottlingException",
        "InternalServerError",
        "ServiceUnavailable",
    }
)


def _classify_client_error(exc: ClientError) -> tuple[str, str, bool]:
    """Return ``(reason, error_code, retryable)`` for a DynamoDB ``ClientError``."""
    error = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
    code = str(error.get("Code") or "ClientError")
    message = str(error.get("Message") or "")
    # A KMS problem surfaces either with a KMS* code or as an access/validation
    # error whose message names KMS (e.g. a disabled or inaccessible CMK).
    if code.startswith("KMS") or "KMS" in message:
        return StateStoreError.REASON_ENCRYPTION_KEY_UNAVAILABLE, code, False
    if code in _TABLE_NOT_FOUND_CODES:
        return StateStoreError.REASON_TABLE_NOT_FOUND, code, False
    if code in _ACCESS_DENIED_CODES:
        return StateStoreError.REASON_ACCESS_DENIED, code, False
    return StateStoreError.REASON_UNAVAILABLE, code, code in _TRANSIENT_CODES


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DynamoDbStateRepository:
    """A DynamoDB-backed :class:`StateRepository`.

    Stores one item per tenant in the ``OnboardingState`` table keyed by
    ``tenant_id``. See the module docstring for the item shape and the
    persistence guarantees (9.1-9.4) this implementation provides.
    """

    def __init__(self, table_name: str, resource: Any | None = None) -> None:
        """Create the repository for the given table.

        ``resource`` is an optional boto3 DynamoDB *resource* (used for tests).
        When omitted, a resource is created **without explicit credentials** so
        that boto3's default credential chain resolves the AgentCore Runtime
        execution role (``Agent_IAM_Identity``) — no fallback credentials are
        ever supplied here.
        """
        if resource is None:
            resource = boto3.resource("dynamodb")
        self._table_name = table_name
        self._region = self._resource_region(resource)
        self._table = resource.Table(table_name)

    @staticmethod
    def _resource_region(resource: Any) -> str | None:
        """Return the Region the boto3 resource targets, or ``None`` if unknown."""
        try:
            return resource.meta.client.meta.region_name or None
        except AttributeError:
            return None

    def _call(self, operation: str, method: str, **kwargs: Any) -> Any:
        """Invoke ``Table.<method>(**kwargs)``, mapping failures to StateStoreError.

        Any AWS-side failure (``ClientError``) or botocore transport/credential
        failure is re-raised as a :class:`StateStoreError` naming the table, the
        Region, the AWS error code and whether a retry can help, so the domain
        layer can tell the calling agent the backend infrastructure needs review.
        """
        try:
            return getattr(self._table, method)(**kwargs)
        except ClientError as exc:
            reason, code, retryable = _classify_client_error(exc)
            raise StateStoreError(
                reason,
                table_name=self._table_name,
                region=self._region,
                operation=operation,
                error_code=code,
                retryable=retryable,
            ) from exc
        except NoCredentialsError as exc:
            raise StateStoreError(
                StateStoreError.REASON_ACCESS_DENIED,
                table_name=self._table_name,
                region=self._region,
                operation=operation,
                error_code=type(exc).__name__,
            ) from exc
        except BotoCoreError as exc:
            # Endpoint/connection/timeout failures: possibly transient.
            raise StateStoreError(
                StateStoreError.REASON_UNAVAILABLE,
                table_name=self._table_name,
                region=self._region,
                operation=operation,
                error_code=type(exc).__name__,
                retryable=True,
            ) from exc

    # -- reads -------------------------------------------------------------

    def get(self, tenant: TenantId) -> OnboardingState:
        """Return the tenant's durably persisted state (empty state if absent).

        Reads the single item keyed by ``tenant_id`` (per-tenant isolation, 9.2)
        and deserializes it into an :class:`OnboardingState`. Returns a fresh,
        empty state when no item exists for the tenant.
        """
        response = self._call("read", "get_item", Key={"tenant_id": tenant})
        item = response.get("Item")
        if item is None:
            return OnboardingState(tenant_id=tenant)
        return self._deserialize(tenant, item)

    # -- writes ------------------------------------------------------------

    def put_suite(self, tenant: TenantId, suite: Suite) -> None:
        """Persist the tenant's suite, creating the item if absent.

        Uses a ``SET`` update expression that sets ``suite`` and initializes
        ``connectors`` to an empty map only when it does not already exist
        (``if_not_exists``), so existing connectors are never clobbered. The
        expression is idempotent: setting the same suite twice yields the same
        item, so an interrupted, re-invoked write converges. ``update_item``
        completes before this method returns.
        """
        self._call(
            "write",
            "update_item",
            Key={"tenant_id": tenant},
            UpdateExpression=(
                "SET suite = :suite, connectors = if_not_exists(connectors, :empty)"
            ),
            ExpressionAttributeValues={
                ":suite": suite.value,
                ":empty": {},
            },
        )

    def set_connector_status(
        self,
        tenant: TenantId,
        key: str,
        status: ConnectorStatus,
        principal: str,
    ) -> None:
        """Persist a connector's status with change metadata.

        Issues two sequential ``update_item`` calls rather than one:

        1. Ensure the parent ``connectors`` map exists
           (``SET connectors = if_not_exists(connectors, {})``).
        2. Set the single nested entry ``connectors.<key>``.

        A single ``UpdateExpression`` cannot do both: DynamoDB rejects
        ``SET connectors = ..., connectors.<key> = ...`` in one expression with
        ``ValidationException: ... document paths overlap ...`` because the two
        clauses target overlapping branches of the same attribute tree. (A
        previous version of this method assumed DynamoDB evaluates ``SET``
        clauses left-to-right within one expression so the parent would already
        exist by the time the nested clause ran; that assumption is wrong -
        DynamoDB validates the whole expression's path set up front, before
        evaluating any clause, so the overlap is rejected regardless of order.)
        Nor can the nested ``SET`` run alone when ``connectors`` is absent:
        DynamoDB does not auto-create intermediate map levels on a nested path -
        the parent map must already exist, or the call fails with
        ``ValidationException: ... attribute does not exist in the item``.

        Each call is independently idempotent: step 1 is a no-op once
        ``connectors`` exists (``if_not_exists`` leaves it untouched), and step 2
        is a plain idempotent ``SET`` of one entry. A re-invoked, interrupted
        write (which could only have failed after step 1, since step 2 either
        both runs or doesn't) converges to the same item. Only the one
        ``connectors.<key>`` entry is ever touched, so other connectors and the
        suite are preserved. ``status`` is a DynamoDB reserved word, so it is
        aliased through :class:`ExpressionAttributeNames`. Both calls complete
        before this method returns.
        """
        self._call(
            "write",
            "update_item",
            Key={"tenant_id": tenant},
            UpdateExpression="SET connectors = if_not_exists(connectors, :empty)",
            ExpressionAttributeValues={":empty": {}},
        )
        self._call(
            "write",
            "update_item",
            Key={"tenant_id": tenant},
            UpdateExpression="SET connectors.#key = :record",
            ExpressionAttributeNames={"#key": key},
            ExpressionAttributeValues={
                ":record": {
                    "status": status.value,
                    "last_changed_at": _utc_now_iso(),
                    "changed_by": principal,
                },
            },
        )

    def set_extension_status(
        self,
        tenant: TenantId,
        key: str,
        status: ExtensionStatus,
        principal: str,
    ) -> None:
        """Persist an extension's admin-attested status with change metadata.

        Mirrors :meth:`set_connector_status` exactly, but writes to a separate
        top-level ``extensions`` map so extension status (``installed`` /
        ``skipped`` / ``not_configured``) never collides with the connector
        ``ConnectorStatus`` value space. Two sequential idempotent ``update_item``
        calls: (1) ensure the parent ``extensions`` map exists via
        ``if_not_exists``; (2) ``SET`` the single ``extensions.<key>`` entry.
        ``status`` is a DynamoDB reserved word only as a bare attribute name; here
        it is a nested map field inside the record value, so no alias is needed
        for it — but ``#key`` is still aliased to be safe against reserved keys.
        """
        self._call(
            "write",
            "update_item",
            Key={"tenant_id": tenant},
            UpdateExpression="SET extensions = if_not_exists(extensions, :empty)",
            ExpressionAttributeValues={":empty": {}},
        )
        self._call(
            "write",
            "update_item",
            Key={"tenant_id": tenant},
            UpdateExpression="SET extensions.#key = :record",
            ExpressionAttributeNames={"#key": key},
            ExpressionAttributeValues={
                ":record": {
                    "status": status.value,
                    "last_changed_at": _utc_now_iso(),
                    "changed_by": principal,
                },
            },
        )

    # -- (de)serialization -------------------------------------------------

    @staticmethod
    def _deserialize(tenant: TenantId, item: dict[str, Any]) -> OnboardingState:
        """Map a stored DynamoDB item into an :class:`OnboardingState`.

        - ``suite`` string maps to the :class:`Suite` enum (``None`` when absent).
        - ``connectors`` map is projected into ``dict[str, ConnectorRecord]``
          with each entry's ``status`` string mapped to :class:`ConnectorStatus`.
        - ``extensions`` map is projected into ``dict[str, ExtensionRecord]``
          with each entry's ``status`` string mapped to :class:`ExtensionStatus`.
        """
        suite_raw = item.get("suite")
        suite = Suite(suite_raw) if suite_raw is not None else None

        connectors: dict[str, ConnectorRecord] = {}
        for conn_key, record in (item.get("connectors") or {}).items():
            connectors[conn_key] = ConnectorRecord(
                status=ConnectorStatus(record["status"]),
                last_changed_at=record.get("last_changed_at"),
                changed_by=record.get("changed_by"),
            )

        extensions: dict[str, ExtensionRecord] = {}
        for ext_key, record in (item.get("extensions") or {}).items():
            extensions[ext_key] = ExtensionRecord(
                status=ExtensionStatus(record["status"]),
                last_changed_at=record.get("last_changed_at"),
                changed_by=record.get("changed_by"),
            )

        return OnboardingState(
            tenant_id=tenant,
            suite=suite,
            connectors=connectors,
            extensions=extensions,
        )
