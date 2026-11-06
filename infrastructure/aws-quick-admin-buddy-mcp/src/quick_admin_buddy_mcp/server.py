"""The MCP server tool layer (FastMCP / MCPServer).

This module is the transport-facing surface of the Quick Admin Buddy MCP
server. It registers the onboarding tools on a single MCP server instance,
resolves the tenant identity from the authenticated request context (never from
tool arguments), delegates to the deterministic
:class:`~quick_admin_buddy_mcp.domain.service.OnboardingService`, and returns the
already-sanitized :class:`~quick_admin_buddy_mcp.domain.types.ToolResult` as a
JSON-serializable ``dict``.

SDK note
--------
The design and tasks were written against the mcp v1 ``FastMCP`` API
(``from mcp.server.fastmcp import FastMCP`` with
``FastMCP(host="0.0.0.0", stateless_http=True)``). The installed SDK is
**mcp 2.x**, where that class was renamed to
:class:`mcp.server.mcpserver.MCPServer` and the ``host`` / ``stateless_http``
options moved from the constructor to :meth:`MCPServer.run`. This module targets
the installed 2.x API. The mapping is:

* ``FastMCP(...)``                        → ``MCPServer(name=...)``
* ``FastMCP(host=..., stateless_http=…)`` → ``mcp.run(transport="streamable-http",
  host=..., stateless_http=...)``
* ``@mcp.tool()``                         → unchanged (``@mcp.tool()``)

Import side-effects
-------------------
Importing this module constructs the ``MCPServer`` and registers the tools, but
it does **not** construct any boto3 client or DynamoDB resource, and it makes no
network call. The runtime :class:`OnboardingService` (which owns the DynamoDB
repository and the boto3-backed Quick Admin client) is built lazily on the first
tool invocation via :func:`_get_service`. Tests inject an in-memory service with
:func:`set_service`, so they never touch AWS.

Tenant identity (Requirement 9.2, security)
--------------------------------------------
This deployment authenticates callers but does NOT propagate per-user identity
to the backend. The AgentCore Gateway's Cognito CUSTOM_JWT authorizer gates
access — only a caller presenting a valid Cognito user token reaches the
server — but the gateway signs the gateway->runtime hop with its own IAM role,
so the runtime never sees the end user (this mirrors the proven AWS SAP MCP
gateway-fronted pattern). Every authenticated Quick admin therefore maps to a
single shared tenant.

:func:`_resolve_tenant` returns that shared tenant from the ``ONBOARDING_TENANT``
environment variable (default ``shared``). It never reads a tenant from tool
arguments — none of the tool signatures accept one — which is the structural
guarantee behind 9.2.
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import Context, MCPServer

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.quick import BotoQuickAdminClient, BotoSecretResolver
from quick_admin_buddy_mcp.state import DynamoDbStateRepository, TenantId

# ---------------------------------------------------------------------------
# Configuration (environment-driven; no AWS/network access at import time)
# ---------------------------------------------------------------------------

# The DynamoDB table that stores per-tenant Onboarding_State.
_STATE_TABLE_ENV = "ONBOARDING_STATE_TABLE"
_DEFAULT_STATE_TABLE = "OnboardingState"

# The single shared tenant every authenticated caller maps to. This deployment
# does not propagate per-user identity to the backend (see the module
# docstring): the gateway's Cognito CUSTOM_JWT authorizer gates access, and all
# authenticated callers share this one tenant. Configured via the
# ONBOARDING_TENANT env var (set from the TenantId template parameter).
_TENANT_ENV = "ONBOARDING_TENANT"
_DEFAULT_TENANT = "shared"


# ---------------------------------------------------------------------------
# The MCP server instance and its tool registrations
# ---------------------------------------------------------------------------

# In mcp 2.x, `stateless_http=True` and `host` are passed to run()
# (see main()), not to the constructor as they were on v1's FastMCP.
mcp = MCPServer(name="quick-admin-buddy-mcp")


# ---------------------------------------------------------------------------
# Lazily-constructed runtime service (import stays side-effect-free)
# ---------------------------------------------------------------------------

# A test/override hook. When set (via set_service), every tool uses this service
# instead of building the runtime one, so tests never construct AWS clients.
_service_override: OnboardingService | None = None

# The memoized runtime service, built on first use by _get_service().
_runtime_service: OnboardingService | None = None


def set_service(service: OnboardingService | None) -> None:
    """Override the service the tools delegate to (dependency injection for tests).

    Pass an :class:`OnboardingService` built on an in-memory repository and a
    mock Quick Admin client to exercise the tool layer without AWS. Pass
    ``None`` to clear the override and fall back to the lazily-built runtime
    service.
    """
    global _service_override
    _service_override = service


def _build_runtime_service() -> OnboardingService:
    """Compose the runtime service: catalog + DynamoDB repo + boto3 Quick client.

    Constructs the DynamoDB repository (which creates a boto3 resource) and the
    boto3-backed Quick Admin client. This is deferred until the first tool call
    so importing this module never requires AWS credentials or the network.
    """
    table_name = os.environ.get(_STATE_TABLE_ENV, _DEFAULT_STATE_TABLE)
    catalog = ConnectorCatalog()
    state = DynamoDbStateRepository(table_name)
    quick = BotoQuickAdminClient()
    secrets = BotoSecretResolver()
    return OnboardingService(catalog, state, quick, secrets)


def _get_service() -> OnboardingService:
    """Return the service the tools delegate to.

    Precedence: an explicit :func:`set_service` override (tests) wins; otherwise
    the runtime service is built once (lazily) and memoized. Building the runtime
    service is the only place that constructs boto3 clients, so a bare import of
    this module has no AWS side-effects.
    """
    if _service_override is not None:
        return _service_override
    global _runtime_service
    if _runtime_service is None:
        _runtime_service = _build_runtime_service()
    return _runtime_service


# ---------------------------------------------------------------------------
# Tenant resolution (single shared tenant; no per-user propagation)
# ---------------------------------------------------------------------------


def _resolve_tenant(ctx: Context | None = None) -> TenantId:
    """Return the single shared tenant every authenticated caller maps to.

    This deployment does not propagate per-user identity to the backend. The
    AgentCore Gateway's Cognito CUSTOM_JWT authorizer gates access (only a
    valid Quick admin token reaches this server), but the gateway signs the
    gateway->runtime hop with its own IAM role, so the runtime never sees the
    end user. All authenticated callers therefore share one tenant, read from
    the ``ONBOARDING_TENANT`` env var (default ``shared``).

    ``ctx`` is accepted and ignored so the tool wrappers can pass the MCP
    ``Context`` uniformly; the tenant does not depend on the request. A tenant
    is never read from tool arguments (Requirement 9.2, security).
    """
    return os.environ.get(_TENANT_ENV, _DEFAULT_TENANT)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
#
# The plain implementation functions are defined separately from the registered
# tools so tests can call them directly (passing ctx=None) without unwrapping
# the MCP tool objects. Each returns the sanitized ToolResult.to_dict()
# produced by the domain service.


def _list_connector_catalog_impl(suite: str | None = None) -> dict:
    """List the connector catalog, optionally filtered by productivity suite.

    Catalog listing needs no tenant, so it does not touch the request context.
    """
    return _get_service().list_catalog(suite).to_dict()


def _set_productivity_suite_impl(suite: str, ctx: Context | None = None) -> dict:
    """Record the tenant's productivity suite."""
    tenant = _resolve_tenant(ctx)
    return _get_service().set_suite(tenant, suite).to_dict()


def _enable_action_connector_impl(
    connector_key: str,
    auth_type: str | None = None,
    auth_params: dict | None = None,
    secret_id: str | None = None,
    config: dict | None = None,
    share_with: str | None = None,
    connector_id_prefix: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Enable a connector as an action connector (CreateActionConnector)."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .enable_action_connector(
            tenant,
            connector_key,
            auth_type,
            auth_params,
            secret_id,
            config,
            share_with=share_with,
            connector_id_prefix=connector_id_prefix,
        )
        .to_dict()
    )


def _disable_action_connector_impl(
    connector_key: str,
    connector_id_prefix: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Disable a connector's action connector integration."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .disable_action_connector(
            tenant, connector_key, connector_id_prefix=connector_id_prefix
        )
        .to_dict()
    )


def _enable_kb_integration_impl(
    connector_key: str,
    auth_type: str | None = None,
    auth_params: dict | None = None,
    secret_id: str | None = None,
    config: dict | None = None,
    share_with: str | None = None,
    connector_id_prefix: str | None = None,
    owner: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Enable a connector as a knowledge base integration (CreateKnowledgeBase)."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .enable_kb_integration(
            tenant,
            connector_key,
            auth_type,
            auth_params,
            secret_id,
            config,
            share_with=share_with,
            connector_id_prefix=connector_id_prefix,
            owner=owner,
        )
        .to_dict()
    )


def _disable_kb_integration_impl(
    connector_key: str,
    connector_id_prefix: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Disable a connector's knowledge base integration."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .disable_kb_integration(
            tenant, connector_key, connector_id_prefix=connector_id_prefix
        )
        .to_dict()
    )


def _get_tenant_configuration_impl(ctx: Context | None = None) -> dict:
    """Return the tenant's current onboarding configuration."""
    tenant = _resolve_tenant(ctx)
    return _get_service().get_configuration(tenant).to_dict()


def _share_action_connector_impl(
    connector_key: str,
    share_with: str,
    connector_id_prefix: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Share an existing action connector with a Quick user or group."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .share_action_connector(
            tenant,
            connector_key,
            share_with,
            connector_id_prefix=connector_id_prefix,
        )
        .to_dict()
    )


def _share_kb_integration_impl(
    connector_key: str,
    share_with: str,
    connector_id_prefix: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Share an existing knowledge base with a Quick user or group."""
    tenant = _resolve_tenant(ctx)
    return (
        _get_service()
        .share_kb_integration(
            tenant,
            connector_key,
            share_with,
            connector_id_prefix=connector_id_prefix,
        )
        .to_dict()
    )


def _get_onboarding_summary_impl(ctx: Context | None = None) -> dict:
    """Return a structured recap of the tenant's onboarding."""
    tenant = _resolve_tenant(ctx)
    return _get_service().get_summary(tenant).to_dict()


def _list_extensions_impl(suite: str | None = None, ctx: Context | None = None) -> dict:
    """List AWS Quick extensions and each one's admin-attested status."""
    tenant = _resolve_tenant(ctx)
    return _get_service().list_extensions(tenant, suite).to_dict()


def _set_extension_status_impl(
    extension_key: str, status: str, ctx: Context | None = None
) -> dict:
    """Record the admin-attested status of an AWS Quick extension."""
    tenant = _resolve_tenant(ctx)
    return _get_service().set_extension_status(tenant, extension_key, status).to_dict()


# ---------------------------------------------------------------------------
# Tool registrations
# ---------------------------------------------------------------------------
#
# Each registered tool is a thin wrapper over its _..._impl function. The tools
# that need a tenant declare a `ctx: Context` parameter, which the MCP SDK
# injects per request; _resolve_tenant reads the authenticated tenant from it.
# No tool accepts a tenant argument (Requirement 9.2).


@mcp.tool()
def list_connector_catalog(suite: str | None = None) -> dict:
    """List the connector catalog, optionally filtered by productivity suite."""
    return _list_connector_catalog_impl(suite)


@mcp.tool()
def set_productivity_suite(suite: str, ctx: Context) -> dict:
    """Set the tenant's productivity suite (google_workspace or microsoft_365)."""
    return _set_productivity_suite_impl(suite, ctx)


@mcp.tool()
def enable_action_connector(
    connector_key: str,
    auth_type: str,
    auth_params: dict | None = None,
    secret_id: str | None = None,
    config: dict | None = None,
    share_with: str | None = None,
    connector_id_prefix: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Enable a connector as an action connector (CreateActionConnector).

    auth_type is 'custom_oauth_app' (3LO OAuth), 'service_to_service_oauth'
    (2LO client credentials), or 'api_key' (2LO API-token auth, e.g. Atlassian
    Confluence Cloud — provide `api_key` and `domain_url`, plus optional
    `email`). Provide the
    required auth parameters either inline in auth_params (snake_case keys) or by
    referencing a Secrets Manager secret via secret_id. If parameters are
    missing, the result lists them and includes the secret contract.

    secret_id must be the Secrets Manager secret NAME/ID (e.g.
    'quick-admin-buddy/outlook-custom-oauth'), never a full
    'arn:aws:secretsmanager:...' ARN. AWS Quick redacts ARN-shaped
    arguments, so an ARN is rejected with a validation_failure; if you only
    have the ARN, pass the part after ':secret:' (keep the trailing '-xxxxxx'
    suffix).

    share_with optionally names a Quick user (e.g. the admin themselves) to
    grant owner-equivalent permissions to on the new connector immediately -
    either a full Quick user ARN, or a bare user name assumed to be in the
    default namespace. Without it, the connector is visible only to the
    server's own execution role, not to any human Quick user. Use the AWS Quick USERNAME, not the user's email
    address. A user that does not exist is rejected with validation_failure
    (data.error_type 'principal_not_found') before anything is created or
    changed.

    connector_id_prefix optionally overrides the prefix used to build the
    connector's resource id (default 'quickadminbuddy-'); the id becomes
    '<prefix><connector_key>'. It must match [\\w\\-]+ (letters, digits,
    underscores, hyphens). Supply the same prefix to disable_action_connector
    to delete the same resource.
    """
    return _enable_action_connector_impl(
        connector_key,
        auth_type,
        auth_params,
        secret_id,
        config,
        share_with,
        connector_id_prefix,
        ctx,
    )


@mcp.tool()
def disable_action_connector(
    connector_key: str,
    connector_id_prefix: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Disable a connector's action connector integration.

    connector_id_prefix must match the prefix used when the connector was
    enabled (default 'quickadminbuddy-') so the correct resource is deleted.
    """
    return _disable_action_connector_impl(connector_key, connector_id_prefix, ctx)


@mcp.tool()
def enable_kb_integration(
    connector_key: str,
    auth_type: str,
    auth_params: dict | None = None,
    secret_id: str | None = None,
    config: dict | None = None,
    share_with: str | None = None,
    connector_id_prefix: str | None = None,
    owner: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Enable a connector as a knowledge base integration.

    Creates a data source (CreateDataSource) and then the knowledge base that
    references it (CreateKnowledgeBase). Supported connectors: sharepoint,
    onedrive, google_drive, confluence.

    Knowledge bases use a DIFFERENT auth model from action connectors.
    auth_type is 'admin_managed' or 'user_managed' (the OAuth app methods used by
    action connectors are NOT valid here). admin_managed uses service credentials
    and enforces document-level ACLs. user_managed takes no credentials; this
    tool creates the knowledge base with document-level ACLs off, and each user's
    provider sign-in happens in the AWS Quick console.

    owner is REQUIRED for a knowledge base: the Quick user who should OWN it
    (a bare user name, assumed in the default namespace, or a full Quick user
    ARN). AWS Quick grants it the fixed OWNER role at creation so a human can
    manage the KB; without an owner the KB is visible only to the server's
    execution role. Ask the admin who should own the knowledge base. Use the AWS Quick USERNAME, not the user's email
    address. A user that does not exist is rejected with validation_failure
    (data.error_type 'principal_not_found') before anything is created or
    changed. (KB Actions
    must match Quick's OWNER/VIEWER roles, so there is no arbitrary share_with
    grant at create time; share_with is ignored for knowledge bases - use
    share_kb_integration afterward to grant another user the VIEWER role.)

    admin_managed secret requirements by connector (pass secret_id as the secret
    NAME/ID, never a full 'arn:aws:secretsmanager:...' ARN):
      - sharepoint: secret with certificate_thumbprint, kms_key_arn, tenant_id,
        client_id, domain_url (default name 'quick-admin-buddy/sharepoint-kb-admin');
        plus config.sites = the list of SharePoint site URLs to index. Sent inline
        as SharePointClientCredentials + SharePointParameters.
      - google_drive: secret = the downloaded Google service-account JSON plus an
        added admin_account_email (default name 'quick-admin-buddy/google-drive-kb-admin').
        Sent inline as ServiceAccountCredentials.
      - onedrive: secret with client_id, tenant_id, kms_key_arn,
        certificate_thumbprint (REQUIRED, no default name). The server resolves
        the name to its ARN and sends it as Credentials.SecretArn.
      - confluence: optional client_id/client_secret, inline in auth_params or
        in a secret; plus config.confluence_url.

    If CreateKnowledgeBase fails, or its enabled status cannot be recorded, the
    creation is rolled back: the data source (and the knowledge base, if it may
    exist) is deleted. data.rollback reports what was deleted; when
    data.rollback.complete is false, the message names what must be deleted in
    AWS Quick before retrying.

    connector_id_prefix optionally overrides the prefix used to build the
    knowledge base's resource id (default 'quickadminbuddy-'); the id becomes
    '<prefix><connector_key>'. It must match [\\w\\-]+. Supply the same prefix
    to disable_kb_integration to delete the same resource.
    """
    return _enable_kb_integration_impl(
        connector_key,
        auth_type,
        auth_params,
        secret_id,
        config,
        share_with,
        connector_id_prefix,
        owner,
        ctx,
    )


@mcp.tool()
def disable_kb_integration(
    connector_key: str,
    connector_id_prefix: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Disable a connector's knowledge base integration.

    Deletes the knowledge base (DeleteKnowledgeBase) and then its data source
    (DeleteDataSource). Once the knowledge base is deleted, or already missing in
    AWS Quick, the integration is recorded as not_configured. If the data
    source then cannot be deleted, the result is server_error with
    data.data_source_id and data.data_source_deleted=false: the knowledge base is
    disabled, but delete that data source in AWS Quick before enabling the
    knowledge base again.

    connector_id_prefix must match the prefix used when the knowledge base was
    enabled (default 'quickadminbuddy-') so the correct resource is deleted.
    """
    return _disable_kb_integration_impl(connector_key, connector_id_prefix, ctx)


@mcp.tool()
def share_action_connector(
    connector_key: str,
    share_with: str,
    connector_id_prefix: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Share an EXISTING action connector with a Quick user or group
    (UpdateActionConnectorPermissions).

    Grants the named principal the owner-equivalent action-connector permission
    set (see/describe/update/delete/list + manage permissions) on a connector
    that already exists in AWS Quick. Use this to share a connector after it
    was created - either because it was enabled without share_with, or to add
    another user/group later.

    share_with is the Quick principal to grant access to: a bare user name
    (assumed in the 'default' namespace) or a full Quick user/group ARN (pass a
    full ARN to share with a group or a non-default namespace). Use the AWS Quick USERNAME, not the user's email
    address. A user that does not exist is rejected with validation_failure
    (data.error_type 'principal_not_found') before anything is created or
    changed.

    connector_id_prefix must match the prefix used when the connector was
    created (default 'quickadminbuddy-') so the derived ActionConnectorId
    targets the same resource.
    """
    return _share_action_connector_impl(
        connector_key, share_with, connector_id_prefix, ctx
    )


@mcp.tool()
def share_kb_integration(
    connector_key: str,
    share_with: str,
    connector_id_prefix: str | None = None,
    *,
    ctx: Context,
) -> dict:
    """Share an EXISTING knowledge base with a Quick user or group
    (UpdateKnowledgeBasePermissions).

    Grants the named principal the VIEWER role (read access) on a knowledge base
    that already exists in AWS Quick. AWS Quick only accepts the fixed
    OWNER and VIEWER roles for knowledge bases, so full management stays with the
    owner set when the knowledge base was created. Use this to give more users
    or groups access after creation (enable_kb_integration ignores share_with).

    share_with is the Quick principal to grant access to: a bare user name
    (assumed in the 'default' namespace) or a full Quick user/group ARN (pass a
    full ARN to share with a group or a non-default namespace). Use the AWS Quick USERNAME, not the user's email
    address. A user that does not exist is rejected with validation_failure
    (data.error_type 'principal_not_found') before anything is created or
    changed.

    connector_id_prefix must match the prefix used when the knowledge base was
    created (default 'quickadminbuddy-') so the derived KnowledgeBaseId targets
    the same resource.
    """
    return _share_kb_integration_impl(
        connector_key, share_with, connector_id_prefix, ctx
    )


@mcp.tool()
def get_tenant_configuration(ctx: Context) -> dict:
    """Return the tenant's current onboarding configuration (suite + per-connector status)."""
    return _get_tenant_configuration_impl(ctx)


@mcp.tool()
def get_onboarding_summary(ctx: Context) -> dict:
    """Return a stage-grouped recap of the tenant's onboarding."""
    return _get_onboarding_summary_impl(ctx)


@mcp.tool()
def list_extensions(ctx: Context, suite: str | None = None) -> dict:
    """List AWS Quick extensions and their admin-attested install status.

    Extensions are client-side add-ins (the Microsoft Office add-ins — Outlook,
    Excel, Word, PowerPoint — and the Google Workspace browser extension) that
    the user installs in their app/browser. **There is no AWS Quick admin API
    for extensions**, so status is admin-declared (installed / skipped /
    not_configured), not API-verified.

    The set is suite-gated. Pass `suite` to force a suite, otherwise the tenant's
    stored suite is used; if neither is set, all extensions are returned. Each
    item includes key, display_name, suite_gate, docs_url, description, status,
    last_changed_at, and changed_by.
    """
    return _list_extensions_impl(suite, ctx)


@mcp.tool()
def set_extension_status(extension_key: str, status: str, ctx: Context) -> dict:
    """Record the admin-attested status of an AWS Quick extension.

    `status` must be one of `installed`, `skipped`, or `not_configured`. This is
    an admin declaration — no AWS Quick API call is made (extensions have
    none). The `extension_key` must be a valid extension (use `list_extensions`)
    and must match the tenant's stored suite, or a `validation_failure` is
    returned.
    """
    return _set_extension_status_impl(extension_key, status, ctx)


# ---------------------------------------------------------------------------
# Runtime entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stateless streamable-HTTP.

    Matches the ``quick-admin-buddy-mcp`` console-script entry point and
    enables ``stateless_http`` as Amazon Bedrock AgentCore Runtime requires.
    The server has no authentication of its own, so it binds to loopback
    (``127.0.0.1``) unless ``MCP_BIND_HOST`` says otherwise. The container
    image sets ``MCP_BIND_HOST`` to all interfaces (see the Dockerfile): the
    AgentCore Runtime's proxy reaches the server over the container network,
    no host port is published, and the Runtime is reachable only through the
    AgentCore Gateway (Cognito CUSTOM_JWT inbound gating plus an IAM-signed
    gateway-to-runtime hop). In mcp 2.x these are arguments to ``run()``
    rather than to the constructor.
    """
    host = os.environ.get("MCP_BIND_HOST", "127.0.0.1")
    mcp.run(transport="streamable-http", host=host, stateless_http=True)


if __name__ == "__main__":
    main()
