"""The OnboardingService: the domain layer's orchestration surface.

:class:`OnboardingService` is the deterministic core of the MCP server. It
composes the domain collaborators — the fixed
:class:`~quick_admin_buddy_mcp.domain.catalog.ConnectorCatalog`, a
:class:`~quick_admin_buddy_mcp.state.repository.StateRepository`, a
:class:`~quick_admin_buddy_mcp.quick.client.QuickAdminClient`, and an optional
:class:`~quick_admin_buddy_mcp.quick.secrets.SecretResolver` — and exposes one
method per higher-level onboarding tool. Every method returns a
:class:`~quick_admin_buddy_mcp.domain.types.ToolResult` that has already passed
through the centralized :func:`~quick_admin_buddy_mcp.domain.sanitize.sanitize`
boundary, so raw credentials and internal stack detail never leak.

Two integration types
---------------------
AWS Quick exposes two distinct integration-creation APIs, and this service
mirrors them with two enable/disable method pairs:

* ``enable_action_connector`` / ``disable_action_connector`` → ``CreateActionConnector``
* ``enable_kb_integration`` / ``disable_kb_integration``   → ``CreateKnowledgeBase``

A single catalog connector may support one or both (see the catalog capability
flags). The two integrations are tracked independently per connector, keyed by
``"<connector_key>:<integration_type>"`` in the onboarding state.

Authentication
--------------
Both enable operations take an ``auth_type``
(:class:`~quick_admin_buddy_mcp.domain.types.AuthType`) and its required
parameters, supplied either inline (``auth_params``) or by reference to a
Secrets Manager secret (``secret_id`` — the secret **name/id only, never an
ARN**) whose JSON body follows the contract in
:mod:`quick_admin_buddy_mcp.domain.auth`. When required parameters are missing,
the operation returns a ``validation_failure`` that names exactly what is
missing and includes the secret contract, so the agent can react. Errors raised
by the underlying Create* APIs are surfaced (sanitized) so the agent can help
the admin troubleshoot.

Initial sharing
----------------
Both enable operations also take an optional ``share_with``: a Quick user to
grant owner-equivalent permissions to on the newly created connector/knowledge
base, via the real API's ``Permissions`` property. Without it, a resource
created by the Runtime's own execution role is visible only to that role - not
to any human Quick user, including the admin who asked for it - until shared
separately. ``share_with`` accepts either a full Quick user ARN or a bare user
name; a bare name is expanded to the ``default`` namespace using the region and
account the Runtime is deployed in (see
:meth:`~quick_admin_buddy_mcp.quick.boto_client.BotoQuickAdminClient._resolve_principal_arn`).
This is distinct from ``principal`` (the *acting* identity recorded as
``changed_by`` in the Onboarding_State audit trail) - a tool call records who
made the change via ``principal`` and separately grants resource access to
whoever ``share_with`` names; they are typically the same admin, but need not
be.
"""

from __future__ import annotations

import re

from quick_admin_buddy_mcp.domain import auth as auth_contract
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog, ExtensionCatalog
from quick_admin_buddy_mcp.domain.sanitize import sanitize
from quick_admin_buddy_mcp.domain.types import (
    AuthType,
    CatalogEntry,
    ConnectorStatus,
    ExtensionStatus,
    IntegrationType,
    ResultStatus,
    Stage,
    Suite,
    ToolResult,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminClient,
    QuickAdminInternalError,
    resource_id,
)
from quick_admin_buddy_mcp.quick.secrets import (
    SecretAccessError,
    SecretResolutionError,
    SecretResolver,
    is_secret_arn,
)
from quick_admin_buddy_mcp.state.repository import (
    StateRepository,
    StateStoreError,
    TenantId,
)

# The default acting principal recorded on a connector change when the caller
# supplies none.
_DEFAULT_PRINCIPAL = "system"

# The two valid Productivity_Suite string values, in a stable order suitable for
# inclusion in validation messages.
_VALID_SUITES: tuple[str, ...] = (
    Suite.GOOGLE_WORKSPACE.value,
    Suite.MICROSOFT_365.value,
)

# The suite-gate value that matches every Productivity_Suite.
_GATE_ANY = "any"

# Knowledge-base connectors whose admin-managed setup is certificate-based
# (a Microsoft Entra app + an AWS KMS signing key). Their service credentials -
# OneDrive admin-managed certificate credentials - client_id, tenant_id,
# kms_key_arn, certificate_thumbprint - can ONLY be carried in a Secrets Manager
# secret that Quick's data connector reads via Credentials.SecretArn (there is
# no inline CreateDataSource path wired for OneDrive here). The agent passes the
# secret NAME; the server resolves its canonical ARN (see step 7b/9).
# NOTE: SharePoint was historically in this set, but it now uses the INLINE
# SharePointClientCredentials channel (see _KB_SERVICE_ACCOUNT_KEYS below), so
# only OneDrive remains here.
_KB_SECRET_REQUIRED_KEYS: frozenset[str] = frozenset({"onedrive"})

# KB connectors whose secret-backed credentials are sent INLINE on the data
# source (NOT as a Credentials.SecretArn): the server resolves the secret and
# expands specific fields into a typed Credentials member. They must NOT go
# through the name->ARN resolution that sets request["credentials_secret_arn"].
#   - google_drive -> Credentials.ServiceAccountCredentials
#   - sharepoint   -> Credentials.SharePointClientCredentials
_KB_SERVICE_ACCOUNT_KEYS: frozenset[str] = frozenset({"google_drive", "sharepoint"})

# Required JSON keys per inline-secret KB connector, and the ServiceAccount/
# SharePoint API members they map to:
#   google_drive: client_email->ClientEmail, private_key->ServiceAccountPrivateKey,
#                 admin_account_email->AdminAccountEmail
#   sharepoint:   certificate_thumbprint->CertThumbprint, kms_key_arn->KmsKeyArn,
#                 plus tenant_id/client_id/domain_url for SharePointParameters
_GOOGLE_DRIVE_SECRET_SCHEMA: tuple[str, ...] = (
    "client_email",
    "private_key",
    "admin_account_email",
)
_SHAREPOINT_SECRET_SCHEMA: tuple[str, ...] = (
    "certificate_thumbprint",
    "kms_key_arn",
    "tenant_id",
    "client_id",
    "domain_url",
)
_KB_INLINE_SECRET_SCHEMA: dict[str, tuple[str, ...]] = {
    "google_drive": _GOOGLE_DRIVE_SECRET_SCHEMA,
    "sharepoint": _SHAREPOINT_SECRET_SCHEMA,
}

# Default Secrets Manager secret NAME per inline-secret KB connector, used when
# the caller supplies none. Matches the quick-admin-buddy/* naming the execution
# role's AuthSecretRead policy grants, so they are readable by default.
_GOOGLE_DRIVE_DEFAULT_SECRET = "quick-admin-buddy/google-drive-kb-admin"  # noqa: S105  # nosec B105 - Secrets Manager secret name, not a credential
_SHAREPOINT_DEFAULT_SECRET = "quick-admin-buddy/sharepoint-kb-admin"  # noqa: S105  # nosec B105 - Secrets Manager secret name, not a credential
_KB_INLINE_DEFAULT_SECRET: dict[str, str] = {
    "google_drive": _GOOGLE_DRIVE_DEFAULT_SECRET,
    "sharepoint": _SHAREPOINT_DEFAULT_SECRET,
}

# Every KB key that REQUIRES a secret (either channel), for the step-7b check.
_KB_ANY_SECRET_REQUIRED_KEYS: frozenset[str] = (
    _KB_SECRET_REQUIRED_KEYS | _KB_SERVICE_ACCOUNT_KEYS
)

# Human labels for the two integration types, used in messages.
_INTEGRATION_LABELS = {
    IntegrationType.ACTION_CONNECTOR: "action connector",
    IntegrationType.KNOWLEDGE_BASE: "knowledge base integration",
}

# Substrings that mark a config key as carrying a secret-like value, redacted
# defensively at the sanitize boundary.
_SECRET_KEY_MARKERS: tuple[str, ...] = (
    "secret",
    "credential",
    "password",
    "token",
    "ref",
)


def _secret_values(config: dict | None) -> tuple[str, ...]:
    """Return the secret-looking string values carried by ``config``."""
    if not config:
        return ()
    secrets: list[str] = []
    for name, value in config.items():
        lowered = name.lower()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            if isinstance(value, str) and value:
                secrets.append(value)
    return tuple(secrets)


def _record_key(key: str, integration_type: IntegrationType) -> str:
    """Return the composite state key tracking one integration of a connector."""
    return f"{key}:{integration_type.value}"


# Error codes meaning the targeted AWS Quick resource does not exist. A
# delete that hits one has nothing left to remove, so it counts as done.
_NOT_FOUND_CODES: frozenset[str] = frozenset({"ResourceNotFoundException"})


def _is_not_found(exc: Exception) -> bool:
    """Return True if ``exc`` is an API error reporting a missing resource."""
    return isinstance(exc, QuickAdminApiError) and exc.code in _NOT_FOUND_CODES


# A custom connector-id prefix must keep the resulting resource id within the
# AWS Quick ActionConnectorId pattern [\w\-]+ (also valid for KnowledgeBaseId).
# The connector key appended after the prefix is always from the fixed catalog
# (snake_case word chars), so validating the prefix against the same character
# class guarantees the full "<prefix><key>" id matches. An empty prefix means
# "use the server default", so emptiness is allowed here and handled downstream.
_ID_PREFIX_PATTERN = re.compile(r"^[\w\-]*$")


def _invalid_id_prefix(prefix: str | None) -> bool:
    """Return True if a non-empty ``prefix`` violates the resource-id charset.

    ``None`` / empty is valid (the client falls back to the default prefix).
    Any other value must match ``[\\w\\-]+`` so the derived id stays API-valid.
    """
    if not prefix:
        return False
    return _ID_PREFIX_PATTERN.match(prefix) is None


def _parse_suite(value: str) -> Suite | None:
    """Return the :class:`Suite` for ``value``, or ``None`` if it is invalid."""
    try:
        return Suite(value)
    except ValueError:
        return None


def _entry_view(entry: CatalogEntry) -> dict:
    """Project a catalog entry to the public shape returned by ``list_catalog``."""
    return {
        "key": entry.key,
        "display_name": entry.display_name,
        "category": entry.category,
        "suite_gate": entry.suite_gate,
        "supports_action": entry.supports_action,
        "supports_knowledge_base": entry.supports_kb,
    }


class OnboardingService:
    """Deterministic orchestration of the onboarding tools.

    Instances are cheap and hold no mutable state of their own; all tenant state
    lives behind the injected :class:`StateRepository`. Every returned
    :class:`ToolResult` is sanitized before it leaves the service.
    """

    def __init__(
        self,
        catalog: ConnectorCatalog,
        state: StateRepository,
        quick: QuickAdminClient,
        secrets: SecretResolver | None = None,
        extension_catalog: ExtensionCatalog | None = None,
    ) -> None:
        self._catalog = catalog
        self._state = state
        self._quick = quick
        # Optional Secrets Manager resolver. When absent, a request that
        # references a secret fails with an actionable internal error rather
        # than silently proceeding without the auth parameters.
        self._secrets = secrets
        # The extension catalog is a fixed constant; default-construct it so
        # existing callers/tests that omit it keep working.
        self._extension_catalog = extension_catalog or ExtensionCatalog()

    # -- catalog listing -------------------------------------------------

    def list_catalog(self, suite: str | None) -> ToolResult:
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

    # -- suite management ------------------------------------------------

    def set_suite(self, tenant: TenantId, suite: str) -> ToolResult:
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

    # -- sanitization boundary -------------------------------------------

    def _ok(
        self,
        status: ResultStatus,
        message: str,
        data: dict,
        secrets: tuple[str, ...] = (),
    ) -> ToolResult:
        """Build a :class:`ToolResult` and pass it through the sanitize boundary."""
        return sanitize(
            ToolResult(status=status, message=message, data=data), secrets=secrets
        )

    # -- centralized failure classification ------------------------------

    def _server_error(
        self,
        operation: str,
        key: str | None,
        exc: Exception,
        secrets: tuple[str, ...] = (),
        extra: dict | None = None,
        suffix: str = "",
    ) -> ToolResult:
        """Build a ``server_error`` for a Quick Admin API failure.

        The message names the cause (the failed operation and the sanitized API
        error text) and the remedy, so the agent can help the admin troubleshoot
        the underlying ``Create*`` failure.
        """
        target = f" {key!r}" if key is not None else ""
        data = {"connector_key": key} if key is not None else {}
        if extra:
            data.update(extra)
        return self._ok(
            ResultStatus.SERVER_ERROR,
            (
                f"The AWS Quick API failed while {operation}{target}: {exc}. "
                + (
                    suffix
                    or "Review the reported error and the configuration, then retry."
                )
            ),
            data,
            secrets,
        )

    def _internal_error(
        self,
        operation: str,
        key: str | None,
        exc: Exception,
        secrets: tuple[str, ...] = (),
        extra: dict | None = None,
        suffix: str = "",
    ) -> ToolResult:
        """Build an ``internal_error`` for an internal/network failure."""
        target = f" {key!r}" if key is not None else ""
        data = {"connector_key": key} if key is not None else {}
        if extra:
            data.update(extra)
        return self._ok(
            ResultStatus.INTERNAL_ERROR,
            (
                f"An internal error occurred while {operation}{target}: {exc}. "
                + (suffix or "Retry the operation.")
            ),
            data,
            secrets,
        )

    def _state_error(
        self,
        exc: StateStoreError,
        context: str | None = None,
        data: dict | None = None,
        secrets: tuple[str, ...] = (),
        status: ResultStatus = ResultStatus.INTERNAL_ERROR,
    ) -> ToolResult:
        """Build a result for an unusable backend state store.

        The message states what is wrong with the backend infrastructure (for
        example, the DynamoDB table does not exist), whether retrying can help,
        and what the admin should review, so the calling agent stops retrying
        and sends the admin to the deployment. ``context`` optionally prefixes
        what already happened (e.g. a Quick resource was created before the
        state write failed). ``data["state_store_error"]`` carries the same
        detail in machine-readable form (``reason``, ``table_name``, ``region``,
        ``error_code``, ``retryable``).

        ``status`` defaults to ``internal_error`` (nothing was changed). Pass
        ``server_error`` when a Quick Admin API call already succeeded and only
        the state write afterwards failed, matching the documented contract for
        post-API persistence failures.
        """
        parts = [context] if context else []
        parts.append(exc.describe())
        parts.append(exc.remediation())
        payload = dict(data or {})
        payload["state_store_error"] = exc.to_data()
        return self._ok(status, " ".join(parts), payload, secrets)

    def _missing_principal(
        self,
        field_name: str,
        principal: str | None,
        label: str,
        key: str,
        base_data: dict,
        secrets: tuple[str, ...] = (),
    ) -> ToolResult | None:
        """Return a ``validation_failure`` if ``principal`` names a missing Quick user.

        Asks the Quick client whether the user exists before any create/share
        call, because AWS Quick rejects a grant to a non-existent user with a
        generic ``InvalidParameterValueException`` that does not name the cause.
        Returns ``None`` (proceed) when the user exists, when existence is
        unknown (group ARN, lookup not permitted), or when no principal was
        given - so this check only ever sharpens the error, never blocks a
        request AWS Quick would accept.
        """
        if not principal:
            return None
        check = getattr(self._quick, "principal_exists", None)
        if check is None or check(principal) is not False:
            return None
        hint = ""
        if "@" in principal and not principal.startswith("arn:"):
            hint = (
                " The value looks like an email address, for example the "
                f"username may be {principal.split('@', 1)[0]!r} instead."
            )
        return self._ok(
            ResultStatus.VALIDATION_FAILURE,
            (
                f"AWS Quick user {principal!r} (passed as '{field_name}') does "
                f"not exist in this AWS Quick account, so the {label} for "
                f"{key!r} was NOT created or changed. The '{field_name}' "
                "parameter must be the AWS Quick USERNAME, not the user's "
                "email address - the two are often different."
                f"{hint} User names are case-sensitive and a bare name is looked "
                "up in the 'default' namespace. Ask Quick \"Which is the "
                'connected user for this session?" and use the username it '
                "returns (or ask the admin for their AWS Quick username), "
                "then retry."
            ),
            {
                **base_data,
                "error_type": "principal_not_found",
                "invalid_principal": principal,
                "principal_field": field_name,
            },
            secrets,
        )

    @staticmethod
    def _integration_label(integration_type: IntegrationType) -> str:
        return _INTEGRATION_LABELS[integration_type]

    # -- public enable/disable method pairs ------------------------------

    def enable_action_connector(
        self,
        tenant: TenantId,
        key: str,
        auth_type: str | None,
        auth_params: dict | None = None,
        secret_id: str | None = None,
        config: dict | None = None,
        principal: str | None = None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Enable a connector as an **action connector** (``CreateActionConnector``).

        ``share_with`` optionally names a Quick user (bare name, assumed in the
        ``default`` namespace, or a full user ARN) to grant owner-equivalent
        permissions to on the connector at creation time, via the API's
        ``Permissions`` property - so the admin (typically themselves) can see
        and manage the connector immediately rather than it being visible only
        to the Runtime's own execution role.

        ``connector_id_prefix`` optionally overrides the prefix used to build the
        connector's ``ActionConnectorId`` (default ``quickadminbuddy-``); the id
        becomes ``<prefix><connector_key>``. It must match ``[\\w\\-]+``. The same
        prefix must be supplied to :meth:`disable_action_connector` to target the
        same resource for deletion.
        """
        return self._enable_integration(
            tenant,
            key,
            IntegrationType.ACTION_CONNECTOR,
            auth_type,
            auth_params,
            secret_id,
            config,
            principal,
            share_with,
            connector_id_prefix,
        )

    def disable_action_connector(
        self,
        tenant: TenantId,
        key: str,
        principal: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Disable a connector's **action connector** integration.

        ``connector_id_prefix`` must match the prefix used when the connector
        was enabled (default ``quickadminbuddy-``) so the derived
        ``ActionConnectorId`` targets the same resource.
        """
        return self._disable_integration(
            tenant,
            key,
            IntegrationType.ACTION_CONNECTOR,
            principal,
            connector_id_prefix,
        )

    def enable_kb_integration(
        self,
        tenant: TenantId,
        key: str,
        auth_type: str | None,
        auth_params: dict | None = None,
        secret_id: str | None = None,
        config: dict | None = None,
        principal: str | None = None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
        owner: str | None = None,
    ) -> ToolResult:
        """Enable a connector as a **knowledge base** (``CreateKnowledgeBase``).

        ``owner`` names the Quick user (bare name, assumed in the ``default``
        namespace, or a full user ARN) who should OWN the created knowledge base.
        AWS Quick grants it the fixed **OWNER** role at creation time via the
        API's ``Permissions`` property, so a human owns and can manage the KB
        (without it, the KB is visible only to the server's execution role).
        A knowledge base has no arbitrary "share_with" grant at create time -
        its Actions must match the OWNER/VIEWER roles - so ``share_with`` is not
        used for knowledge bases here; use :meth:`share_kb_integration` to grant
        another user the VIEWER role afterward.

        ``connector_id_prefix`` optionally overrides the prefix used to build the
        ``KnowledgeBaseId`` (default ``quickadminbuddy-``); the id becomes
        ``<prefix><connector_key>``. It must match ``[\\w\\-]+``. The same prefix
        must be supplied to :meth:`disable_kb_integration` to target the same
        resource for deletion.
        """
        return self._enable_integration(
            tenant,
            key,
            IntegrationType.KNOWLEDGE_BASE,
            auth_type,
            auth_params,
            secret_id,
            config,
            principal,
            share_with,
            connector_id_prefix,
            owner,
        )

    def disable_kb_integration(
        self,
        tenant: TenantId,
        key: str,
        principal: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Disable a connector's **knowledge base** integration.

        ``connector_id_prefix`` must match the prefix used when the knowledge
        base was enabled (default ``quickadminbuddy-``) so the derived
        ``KnowledgeBaseId`` targets the same resource.
        """
        return self._disable_integration(
            tenant, key, IntegrationType.KNOWLEDGE_BASE, principal, connector_id_prefix
        )

    # -- public share method pair ----------------------------------------

    def share_action_connector(
        self,
        tenant: TenantId,
        key: str,
        share_with: str,
        principal: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Share an existing **action connector** with a Quick user or group
        (``UpdateActionConnectorPermissions``).

        ``share_with`` is the Quick principal to grant owner-equivalent
        permissions to - a bare user name (assumed in the ``default`` namespace)
        or a full user/group ARN. Unlike ``share_with`` on the enable path (a
        create-time convenience), this operates on a connector that already
        exists in AWS Quick, so no local ``enabled`` state is required.

        ``connector_id_prefix`` must match the prefix used when the connector was
        created (default ``quickadminbuddy-``) so the derived ``ActionConnectorId``
        targets the same resource.
        """
        return self._share_integration(
            tenant,
            key,
            IntegrationType.ACTION_CONNECTOR,
            share_with,
            principal,
            connector_id_prefix,
        )

    def share_kb_integration(
        self,
        tenant: TenantId,
        key: str,
        share_with: str,
        principal: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> ToolResult:
        """Share an existing **knowledge base** with a Quick user or group
        (``UpdateKnowledgeBasePermissions``).

        ``share_with`` is the Quick principal to grant the **VIEWER** role to (the
        only role besides the creation-time OWNER that AWS Quick accepts) - a
        bare user name (assumed in the ``default`` namespace) or a full
        user/group ARN. Operates on a knowledge base that already
        exists in AWS Quick, so no local ``enabled`` state is required.

        ``connector_id_prefix`` must match the prefix used when the knowledge base
        was created (default ``quickadminbuddy-``) so the derived ``KnowledgeBaseId``
        targets the same resource.
        """
        return self._share_integration(
            tenant,
            key,
            IntegrationType.KNOWLEDGE_BASE,
            share_with,
            principal,
            connector_id_prefix,
        )

    # -- shared share implementation -------------------------------------

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

    # -- shared enable implementation ------------------------------------

    def _enable_integration(
        self,
        tenant: TenantId,
        key: str,
        integration_type: IntegrationType,
        auth_type_raw: str | None,
        auth_params: dict | None,
        secret_id: str | None,
        config: dict | None,
        principal: str | None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
        owner: str | None = None,
    ) -> ToolResult:
        """Validate, authenticate, create, and record one integration.

        Ordered checks (first failing check wins):

        1. A Productivity_Suite must be stored → else ``validation_failure``.
        2. ``key`` must exist in the catalog → else ``validation_failure``.
        3. The connector must support this integration type → else
           ``validation_failure`` naming the unsupported capability.
        3b. ``connector_id_prefix`` (if given) must match ``[\\w\\-]+`` → else
           ``validation_failure`` so the derived resource id stays API-valid.
        4. The connector's Suite_Gate must match the stored suite or be ``any``.
        5. ``auth_type`` must be a valid :class:`AuthType` → else
           ``validation_failure`` listing the valid auth types.
        6. Auth parameters are resolved from the Secrets Manager secret (when
           ``secret_id`` is given) and/or the inline ``auth_params``. A missing
           secret → ``validation_failure``; an access failure → ``internal_error``.
        7. All required auth parameters for the auth type (and any connector
           ``required_fields``) must be present → else ``validation_failure``
           listing the missing items and the secret contract, so the agent can
           collect them or fix the secret.
        8. If this integration is already ``enabled`` → ``already_configured``.
        9. Create via ``CreateActionConnector``, or ``CreateDataSource`` then
           ``CreateKnowledgeBase``. An API error → ``server_error`` carrying the
           reported error; an internal error → ``internal_error``. If
           ``CreateKnowledgeBase`` fails, the data source (and, after an
           internal error, the possibly-created knowledge base) is rolled back
           and ``data["rollback"]`` reports the outcome.
        10. Persist the ``enabled`` status for this integration. A persistence
            failure after a successful create → ``server_error`` for an action
            connector; a knowledge base is rolled back instead (``internal_error``
            when the rollback is complete, ``server_error`` otherwise).
        """
        label = self._integration_label(integration_type)
        # Redact any secret-looking inline value from every result this method
        # may return, regardless of branch.
        secrets = auth_contract.secret_values(auth_params or {}) + _secret_values(
            config
        )
        base_data = {"connector_key": key, "integration_type": integration_type.value}

        # 0. Name-only contract. Every secret-accepting tool takes the secret
        # NAME/ID, never an ARN. AWS Quick auto-redacts ARN-shaped tool
        # arguments, which would silently corrupt a forwarded ARN, so reject one
        # up front with an actionable message instead of failing later with an
        # opaque Secrets Manager error.
        if is_secret_arn(secret_id):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"secret_id must be the Secrets Manager secret NAME, not an "
                    f"ARN, for this {label}. You passed an 'arn:...' value; pass "
                    "just the secret name instead (for example "
                    "'quick-admin-buddy/sharepoint-kb'). If you only have the "
                    "ARN, use the part after ':secret:' (keep the trailing "
                    "'-xxxxxx' suffix). The server resolves the ARN itself when "
                    "one is needed."
                ),
                dict(base_data),
                secrets,
            )

        try:
            state = self._state.get(tenant)
        except StateStoreError as exc:
            return self._state_error(
                exc,
                f"The {label} for {key!r} was NOT created.",
                dict(base_data),
                secrets,
            )

        # 1. Suite must be set first.
        if state.suite is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Cannot enable a {label} before a productivity suite is set. "
                    "Set the productivity suite first, then retry."
                ),
                dict(base_data),
                secrets,
            )

        # 2. Connector key must exist.
        entry = self._catalog.get(key)
        if entry is None:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Unknown connector key {key!r}. "
                    "Retry with a connector key from the catalog."
                ),
                dict(base_data),
                secrets,
            )

        # 3. Connector must support this integration type.
        if (
            integration_type == IntegrationType.ACTION_CONNECTOR
            and not entry.supports_action
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} does not support an action connector. "
                    "Use a knowledge base integration, or choose another connector."
                ),
                dict(base_data),
                secrets,
            )
        if integration_type == IntegrationType.KNOWLEDGE_BASE and not entry.supports_kb:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} does not support a knowledge base integration. "
                    "Use an action connector, or choose another connector."
                ),
                dict(base_data),
                secrets,
            )

        # 3b. A custom connector-id prefix must keep the resource id valid.
        if _invalid_id_prefix(connector_id_prefix):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid connector id prefix {connector_id_prefix!r}. "
                    "A prefix may contain only letters, digits, underscores, and "
                    "hyphens (pattern [\\w\\-]+), since the resulting connector id "
                    f"is {connector_id_prefix!r} + the connector key. "
                    "Retry with a valid prefix, or omit it to use the default."
                ),
                {**base_data, "invalid_connector_id_prefix": connector_id_prefix},
                secrets,
            )

        # 4. Suite gate must match the stored suite or be "any".
        if entry.suite_gate != _GATE_ANY and entry.suite_gate != state.suite.value:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Connector {key!r} is gated to {entry.suite_gate!r} but the "
                    f"tenant's productivity suite is {state.suite.value!r}. "
                    "Enable a connector valid for the stored suite, or change the suite."
                ),
                {
                    **base_data,
                    "suite_gate": entry.suite_gate,
                    "stored_suite": state.suite.value,
                },
                secrets,
            )

        # 5. Auth type must be valid AND allowed for this integration type.
        # Action connectors and knowledge bases accept DIFFERENT auth types, so
        # a value that is valid for one is rejected for the other (e.g.
        # custom_oauth_app is not a knowledge-base auth method).
        allowed_for_type = auth_contract.valid_auth_types_for(integration_type)
        auth_type = auth_contract.parse_auth_type(auth_type_raw)
        if auth_type is None or not auth_contract.is_auth_type_allowed(
            auth_type, integration_type
        ):
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Invalid or missing auth type {auth_type_raw!r} for a {label}. "
                    f"Valid options for a {label} are: {', '.join(allowed_for_type)}. "
                    "Retry with one of those."
                ),
                {
                    **base_data,
                    "invalid_auth_type": auth_type_raw,
                    "valid_auth_types": list(allowed_for_type),
                },
                secrets,
            )

        # 5b. Default the inline-secret KB connector's secret name. Google Drive
        # and SharePoint admin-managed are secret-backed (credentials sent inline
        # from the secret); when the caller omits secret_id, fall back to the
        # conventional default name per connector so the common case needs no
        # argument. An explicit secret_id always wins.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and key in _KB_SERVICE_ACCOUNT_KEYS
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and not secret_id
        ):
            secret_id = _KB_INLINE_DEFAULT_SECRET.get(key)

        # 6. Resolve auth parameters (secret and/or inline).
        resolved, secret_vals, error = self._resolve_auth_params(
            key, integration_type, auth_params, secret_id
        )
        if error is not None:
            return error
        secrets = secrets + secret_vals

        # 7. Required auth parameters (and connector config fields) must be present.
        missing_auth = auth_contract.missing_params(auth_type, resolved)
        provided_cfg = config or {}
        # Base config fields apply to every integration type; knowledge-base
        # connectors additionally require the source-connection fields needed to
        # create their backing data source (CreateDataSource). Action-connector
        # enables of a dual-capability connector are NOT forced to supply those.
        required_cfg_fields = tuple(entry.required_fields)
        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            required_cfg_fields = required_cfg_fields + tuple(entry.kb_required_fields)
        missing_cfg = [
            field_name
            for field_name in required_cfg_fields
            if field_name not in provided_cfg
            or provided_cfg[field_name] is None
            or provided_cfg[field_name] == ""
        ]
        if missing_auth or missing_cfg:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"The {label} for {key!r} is missing required "
                    f"{auth_type.value} parameter(s): "
                    f"{', '.join(missing_auth) or 'none'}"
                    + (
                        f"; and connector config field(s): {', '.join(missing_cfg)}"
                        if missing_cfg
                        else ""
                    )
                    + ". Provide them inline, or store them in a Secrets Manager "
                    "secret following the contract and pass its id, then retry."
                ),
                {
                    **base_data,
                    "auth_type": auth_type.value,
                    "missing_params": missing_auth,
                    "missing_config_fields": missing_cfg,
                    "required_params": list(auth_contract.required_params(auth_type)),
                    "secret_contract": auth_contract.secret_contract_example(auth_type),
                },
                secrets,
            )

        # 7b. Certificate-based KB connectors (SharePoint, OneDrive) MUST be
        # configured with a Secrets Manager secret: their service credentials
        # (client_id, tenant_id, kms_key_arn, certificate_thumbprint) can only
        # be carried in a secret Quick reads, never inline (no API field exists
        # for the KMS key ARN or thumbprint). Require a secret NAME before any
        # API call. The server resolves the name to its canonical ARN in step 9
        # for the data source's Credentials.SecretArn (an ARN is never passed in).
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and key in _KB_SECRET_REQUIRED_KEYS
        ):
            if not secret_id:
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"The {label} for {key!r} (admin-managed) requires a "
                        "Secrets Manager secret. Provide secret_id as the secret "
                        "NAME (not an ARN) of a Secrets Manager secret whose JSON "
                        "body has the keys client_id, tenant_id, kms_key_arn, and "
                        "certificate_thumbprint (the schema AWS Quick reads "
                        "for SharePoint/OneDrive certificate-based access). The "
                        "KMS key ARN and certificate thumbprint cannot be passed "
                        "inline — they must live in the secret. Create the "
                        "secret (name it 'quick-admin-buddy/<...>'), grant the "
                        "Quick service role access to it and kms:Sign on the "
                        "signing key, then retry with its NAME."
                    ),
                    {
                        **base_data,
                        "auth_type": auth_type.value,
                        "required_secret_schema": [
                            "client_id",
                            "tenant_id",
                            "kms_key_arn",
                            "certificate_thumbprint",
                        ],
                    },
                    secrets,
                )

        # 7c. Inline-secret KB connectors (Google Drive, SharePoint) MUST be
        # configured with a Secrets Manager secret whose fields are sent INLINE
        # on the data source (ServiceAccountCredentials / SharePointClientCredentials
        # plus the *Parameters). secret_id defaults in step 5b, so a missing
        # secret surfaces here as missing fields with an actionable message.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and auth_type == AuthType.KB_ADMIN_MANAGED
            and key in _KB_SERVICE_ACCOUNT_KEYS
        ):
            schema = _KB_INLINE_SECRET_SCHEMA.get(key, ())
            missing_sa = [f for f in schema if not resolved.get(f)]
            if missing_sa:
                default_secret = _KB_INLINE_DEFAULT_SECRET.get(key)
                if key == "sharepoint":
                    detail = (
                        "whose JSON body holds the SharePoint certificate-based "
                        "credentials: certificate_thumbprint and kms_key_arn (the "
                        "AWS KMS signing-key ARN), plus tenant_id, client_id, and "
                        "domain_url. These map to the API's "
                        "SharePointClientCredentials (CertThumbprint / KmsKeyArn) "
                        "and SharePointParameters (TenantId / ClientId / "
                        "SharePointDomain)."
                    )
                else:
                    detail = (
                        "whose JSON body is the downloaded Google service-account "
                        "key (so it includes client_email and private_key) PLUS an "
                        "added admin_account_email (the Google Workspace admin the "
                        "service account impersonates via domain-wide delegation). "
                        "These map to the API's ServiceAccountCredentials "
                        "(ClientEmail / ServiceAccountPrivateKey / AdminAccountEmail)."
                    )
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"The {label} for {key!r} (admin-managed) requires a "
                        "Secrets Manager secret, but it is missing: "
                        f"{', '.join(missing_sa)}. Provide secret_id as the secret "
                        f"NAME (not an ARN; default {default_secret!r} if omitted) "
                        f"of a secret {detail} Create/fix the secret, then retry."
                    ),
                    {
                        **base_data,
                        "auth_type": auth_type.value,
                        "secret_id": secret_id,
                        "missing_secret_fields": missing_sa,
                        "required_secret_schema": list(schema),
                    },
                    secrets,
                )

        # 7d. A knowledge base MUST have a human owner. Quick grants the OWNER
        # role to `owner` at create time (via Permissions); without it the KB is
        # visible only to the server's execution role. Require it before any API
        # call so the agent collects it from the admin.
        if integration_type == IntegrationType.KNOWLEDGE_BASE and not owner:
            return self._ok(
                ResultStatus.VALIDATION_FAILURE,
                (
                    f"Creating the {label} for {key!r} requires an owner. Provide "
                    "'owner' as the Quick user who should own the knowledge base "
                    "(a bare user name, assumed in the 'default' namespace, or a "
                    "full user ARN). They are granted the OWNER role so a human "
                    "can manage the knowledge base; without it the KB is visible "
                    "only to the server's execution role. Ask the admin who "
                    "should own it, then retry."
                ),
                dict(base_data),
                secrets,
            )

        # 8. Already enabled → idempotent no-op, no API call.
        record_key = _record_key(key, integration_type)
        existing = state.connectors.get(record_key)
        if existing is not None and existing.status == ConnectorStatus.ENABLED:
            return self._ok(
                ResultStatus.ALREADY_CONFIGURED,
                f"The {label} for {key!r} is already enabled.",
                dict(base_data),
                secrets,
            )

        # 8b. The Quick user being granted access (share_with for an action
        # connector, owner for a knowledge base) must exist; otherwise Quick
        # rejects the create with an opaque InvalidParameterValueException.
        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            principal_field, principal_value = "owner", owner
        else:
            principal_field, principal_value = "share_with", share_with
        missing = self._missing_principal(
            principal_field, principal_value, label, key, base_data, secrets
        )
        if missing is not None:
            return missing

        # 9. Create via the appropriate AWS Quick API.
        request = {
            "name": provided_cfg.get("name") or key,
            "description": provided_cfg.get("description"),
            "auth_type": auth_type.value,
            "auth_params": resolved,
            "config": {k: v for k, v in provided_cfg.items() if k != "principal"},
            "share_with": share_with,
            "owner": owner,
            "id_prefix": connector_id_prefix,
        }
        # Certificate-based knowledge-base connectors (SharePoint/OneDrive) read
        # their service credentials through the data source's
        # Credentials.SecretArn — which needs the secret's *canonical ARN*. The
        # agent only ever passes the NAME (ARNs are rejected in step 0 and
        # redacted by Quick), so resolve the name to its ARN here, server-side.
        # A secret is mandatory for these connectors (enforced in step 7b). Other
        # KB connectors (e.g. Confluence) keep using the inline OAuth credentials
        # already read into ``resolved`` by step 6, so they are left untouched.
        if (
            integration_type == IntegrationType.KNOWLEDGE_BASE
            and key in _KB_SECRET_REQUIRED_KEYS
            and secret_id
            and self._secrets is not None
        ):
            try:
                request["credentials_secret_arn"] = self._secrets.resolve_secret_arn(
                    secret_id
                )
            except SecretResolutionError as exc:
                return self._ok(
                    ResultStatus.VALIDATION_FAILURE,
                    (
                        f"Could not resolve the secret {secret_id!r} for {key!r}: "
                        f"{exc} Pass the secret NAME (not an ARN) of an existing "
                        "Secrets Manager secret, then retry."
                    ),
                    {**base_data, "secret_id": secret_id},
                    secrets,
                )
            except SecretAccessError as exc:
                return self._internal_error(
                    "resolving the authentication secret ARN",
                    key,
                    exc,
                    secrets,
                    base_data,
                )
        try:
            if integration_type == IntegrationType.ACTION_CONNECTOR:
                self._quick.create_action_connector(key, request)
            else:
                # A knowledge base is created in two steps: first its data
                # source (which holds the source connection + service
                # credentials), then the knowledge base that references the
                # data source by ARN. Thread the created ARN into the KB
                # request between the two calls. If CreateDataSource itself
                # fails, nothing was created and nothing is rolled back (an
                # existing resource with the same id is not ours to delete).
                data_source = self._quick.create_data_source(key, request)
                request["data_source_arn"] = getattr(data_source, "arn", "") or ""
        except QuickAdminApiError as exc:
            return self._server_error(
                f"creating the {label}", key, exc, secrets, base_data
            )
        except QuickAdminInternalError as exc:
            return self._internal_error(
                f"creating the {label}", key, exc, secrets, base_data
            )

        if integration_type == IntegrationType.KNOWLEDGE_BASE:
            try:
                self._quick.create_knowledge_base(key, request)
            except (QuickAdminApiError, QuickAdminInternalError) as exc:
                # The data source exists; roll it back so a failed enable
                # leaves nothing behind. An API error means the knowledge base
                # was not created. An internal error (e.g. a timeout) leaves
                # that unknown, so the knowledge base is deleted too.
                internal = isinstance(exc, QuickAdminInternalError)
                rollback, note = self._rollback_knowledge_base(
                    key, connector_id_prefix, delete_kb=internal
                )
                data = {**base_data, "rollback": rollback}
                builder = self._internal_error if internal else self._server_error
                return builder(
                    f"creating the {label}", key, exc, secrets, data, suffix=note
                )

        # 10. Persist the enabled status for this integration. For a knowledge
        # base, a failed write rolls the creation back: an unrecorded knowledge
        # base could never be disabled through this server.
        acting = principal or provided_cfg.get("principal") or _DEFAULT_PRINCIPAL
        try:
            self._state.set_connector_status(
                tenant, record_key, ConnectorStatus.ENABLED, acting
            )
        except Exception as exc:  # noqa: BLE001 - classified into a Tool_Result
            if integration_type == IntegrationType.KNOWLEDGE_BASE:
                return self._kb_persist_failure(
                    exc, key, connector_id_prefix, label, base_data, secrets
                )
            return self._persist_failure(exc, key, label, base_data, secrets)

        return self._ok(
            ResultStatus.SUCCESS,
            f"The {label} for {key!r} has been enabled.",
            dict(base_data),
            secrets,
        )

    def _kb_persist_failure(
        self,
        exc: Exception,
        key: str,
        id_prefix: str | None,
        label: str,
        base_data: dict,
        secrets: tuple[str, ...],
    ) -> ToolResult:
        """Roll back a created knowledge base whose enabled status was not saved.

        After a complete rollback nothing was changed, so a state-store failure
        is ``internal_error`` (the documented contract for "nothing changed");
        otherwise it is ``server_error`` because resources were left behind.
        """
        rollback, note = self._rollback_knowledge_base(key, id_prefix, delete_kb=True)
        data = {**base_data, "rollback": rollback}
        complete = rollback["complete"]
        if isinstance(exc, StateStoreError):
            return self._state_error(
                exc,
                (
                    f"The {label} for {key!r} was created in AWS Quick, but its "
                    f"enabled status could not be recorded on the backend. {note}"
                ),
                data,
                secrets,
                status=(
                    ResultStatus.INTERNAL_ERROR
                    if complete
                    else ResultStatus.SERVER_ERROR
                ),
            )
        return self._ok(
            ResultStatus.INTERNAL_ERROR if complete else ResultStatus.SERVER_ERROR,
            (
                f"The {label} for {key!r} was created through the AWS Quick "
                f"API but its enabled status could not be persisted: {exc}. {note}"
            ),
            data,
            secrets,
        )

    def _persist_failure(
        self,
        exc: Exception,
        key: str,
        label: str,
        base_data: dict,
        secrets: tuple[str, ...],
    ) -> ToolResult:
        """Report an action connector created but not recorded as enabled."""
        if isinstance(exc, StateStoreError):
            return self._state_error(
                exc,
                (
                    f"The {label} for {key!r} WAS created in AWS Quick, but its "
                    "enabled status could not be recorded on the backend."
                ),
                dict(base_data),
                secrets,
                status=ResultStatus.SERVER_ERROR,
            )
        return self._ok(
            ResultStatus.SERVER_ERROR,
            (
                f"The {label} for {key!r} was created through the AWS Quick "
                f"API but its enabled status could not be persisted: {exc}. "
                "Retry the operation to reconcile the stored state."
            ),
            dict(base_data),
            secrets,
        )

    def _rollback_knowledge_base(
        self, key: str, id_prefix: str | None, *, delete_kb: bool
    ) -> tuple[dict, str]:
        """Delete what a failed knowledge-base enable created in AWS Quick.

        A knowledge base is created as a data source plus a knowledge base, so a
        failure after ``CreateDataSource`` succeeded would otherwise leave the
        data source behind (and block every retry with a duplicate-id error).
        This removes, best effort, the knowledge base (when ``delete_kb`` - it
        may exist) and then the data source. A "not found" response counts as
        removed.

        Returns ``(rollback, note)``: ``rollback`` is the machine-readable
        outcome for ``data["rollback"]`` and ``note`` is the sentence that tells
        the admin whether anything was left behind.
        """
        kb_id = resource_id(key, id_prefix)
        rollback: dict = {"data_source_id": kb_id}
        failures: list[str] = []

        if delete_kb:
            rollback["knowledge_base_id"] = kb_id
            try:
                self._quick.delete_knowledge_base(key, id_prefix)
                rollback["knowledge_base_deleted"] = True
            except (QuickAdminApiError, QuickAdminInternalError) as exc:
                rollback["knowledge_base_deleted"] = _is_not_found(exc)
                if not _is_not_found(exc):
                    failures.append(f"deleting knowledge base {kb_id!r} failed: {exc}")

        try:
            self._quick.delete_data_source(key, id_prefix)
            rollback["data_source_deleted"] = True
        except (QuickAdminApiError, QuickAdminInternalError) as exc:
            rollback["data_source_deleted"] = _is_not_found(exc)
            if not _is_not_found(exc):
                failures.append(f"deleting data source {kb_id!r} failed: {exc}")

        rollback["complete"] = not failures
        if not failures:
            note = (
                "The knowledge base creation was rolled back: nothing it created "
                "is left in AWS Quick. Fix the cause, then retry."
            )
        else:
            leftovers = [f"data source {kb_id!r}"]
            if delete_kb and not rollback.get("knowledge_base_deleted"):
                leftovers.insert(0, f"knowledge base {kb_id!r}")
            note = (
                "The rollback was incomplete ("
                + "; ".join(failures)
                + "). Delete the "
                + " and the ".join(leftovers)
                + " in AWS Quick before retrying."
            )
        return rollback, note

    def _resolve_auth_params(
        self,
        key: str,
        integration_type: IntegrationType,
        auth_params: dict | None,
        secret_id: str | None,
    ) -> tuple[dict, tuple[str, ...], ToolResult | None]:
        """Resolve auth parameters from a secret and/or inline values.

        Returns ``(resolved, secret_values, error)``. When ``secret_id`` is
        given, the secret JSON is loaded first and inline ``auth_params`` are
        layered on top (inline overrides). ``error`` is a ready-to-return
        :class:`ToolResult` when resolution fails:

        * no resolver configured → ``internal_error``;
        * secret missing / not a JSON object → ``validation_failure``;
        * secret access failure → ``internal_error``.
        """
        base_data = {"connector_key": key, "integration_type": integration_type.value}
        resolved: dict = {}

        if secret_id:
            if self._secrets is None:
                return (
                    {},
                    (),
                    self._internal_error(
                        "resolving the authentication secret",
                        key,
                        RuntimeError("no secret resolver is configured"),
                        (),
                        base_data,
                    ),
                )
            try:
                payload = self._secrets.get_secret_json(secret_id)
            except SecretResolutionError as exc:
                return (
                    {},
                    (),
                    self._ok(
                        ResultStatus.VALIDATION_FAILURE,
                        (
                            f"Could not use the referenced secret for {key!r}: {exc} "
                            "Fix the secret (a JSON object of snake_case auth "
                            "parameters) or provide the parameters inline, then retry."
                        ),
                        {**base_data, "secret_id": secret_id},
                    ),
                )
            except SecretAccessError as exc:
                return (
                    {},
                    (),
                    self._internal_error(
                        "reading the authentication secret", key, exc, (), base_data
                    ),
                )
            if isinstance(payload, dict):
                # The optional "auth_type" hint in the secret is not a parameter.
                resolved.update({k: v for k, v in payload.items() if k != "auth_type"})

        for name, value in (auth_params or {}).items():
            if value is not None:
                resolved[name] = value

        return resolved, auth_contract.secret_values(resolved), None

    # -- shared disable implementation -----------------------------------

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

    # -- configuration read ----------------------------------------------

    def get_configuration(self, tenant: TenantId) -> ToolResult:
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

    # -- onboarding summary ----------------------------------------------

    def get_summary(self, tenant: TenantId) -> ToolResult:
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

    # -- extensions ------------------------------------------------------

    def list_extensions(self, tenant: TenantId, suite: str | None = None) -> ToolResult:
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

    def set_extension_status(
        self,
        tenant: TenantId,
        key: str,
        status: str,
        principal: str | None = None,
    ) -> ToolResult:
        """Record the admin-attested status of an extension.

        ``status`` must be one of ``installed`` / ``skipped`` /
        ``not_configured``. The extension ``key`` must exist in the extension
        catalog, and must be valid for the tenant's stored suite (if a suite is
        stored) — a suite mismatch returns ``validation_failure`` so, e.g., the
        browser extension is not marked installed under Microsoft 365.

        This records an **admin declaration**; it performs no AWS Quick API
        call (extensions have none). On success → ``success`` echoing the new
        status.
        """
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
