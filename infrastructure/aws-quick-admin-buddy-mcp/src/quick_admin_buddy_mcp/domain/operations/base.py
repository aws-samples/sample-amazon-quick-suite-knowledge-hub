"""Shared building blocks for the onboarding operations.

Every onboarding tool is one operation class (see the sibling modules). Each
operation is built once from an :class:`OperationContext` - the catalogs, the
state repository, the AWS Quick admin client, and the optional secret resolver -
and is called with the tenant plus the operation's arguments. This module holds
what the operations share: the constants and helper functions, and the
:class:`Operation` base class with the result builders every operation uses.
Every returned :class:`ToolResult` passes through the central
:func:`~quick_admin_buddy_mcp.domain.sanitize.sanitize` boundary, so raw
credentials and internal stack detail never leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog, ExtensionCatalog
from quick_admin_buddy_mcp.domain.sanitize import sanitize
from quick_admin_buddy_mcp.domain.types import (
    CatalogEntry,
    IntegrationType,
    ResultStatus,
    Suite,
    ToolResult,
)
from quick_admin_buddy_mcp.quick.client import (
    QuickAdminApiError,
    QuickAdminClient,
)
from quick_admin_buddy_mcp.quick.secrets import (
    SecretResolver,
)
from quick_admin_buddy_mcp.state.repository import (
    StateRepository,
    StateStoreError,
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


@dataclass(frozen=True)
class OperationContext:
    """The collaborators every operation is built from."""

    catalog: ConnectorCatalog
    state: StateRepository
    quick: QuickAdminClient
    secrets: SecretResolver | None = None
    extension_catalog: ExtensionCatalog = field(default_factory=ExtensionCatalog)


class Operation:
    """Base class for one onboarding operation.

    Holds the collaborators from the :class:`OperationContext` and the result
    builders shared by every operation. Instances hold no mutable state of their
    own; all tenant state lives behind the state repository.
    """

    def __init__(self, ctx: OperationContext) -> None:
        self._catalog = ctx.catalog
        self._state = ctx.state
        self._quick = ctx.quick
        # Optional Secrets Manager resolver. When absent, a request that
        # references a secret fails with an actionable internal error rather
        # than silently proceeding without the auth parameters.
        self._secrets = ctx.secrets
        self._extension_catalog = ctx.extension_catalog

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
