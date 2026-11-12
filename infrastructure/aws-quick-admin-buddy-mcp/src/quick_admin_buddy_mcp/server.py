"""The MCP server tool layer (``MCPServer``, mcp 2.x).

This module is the transport-facing surface of the AWS Quick Admin Buddy MCP
server. :class:`OnboardingTools` is the tool layer: one method per MCP tool,
bound to one :class:`~quick_admin_buddy_mcp.domain.service.OnboardingService`
and the single shared tenant. Each method calls the matching operation class
(one per tool, in :mod:`quick_admin_buddy_mcp.domain.operations`), writes one
structured log line (see :mod:`quick_admin_buddy_mcp.observability`), and
returns the already-sanitized
:class:`~quick_admin_buddy_mcp.domain.types.ToolResult` as a JSON-serializable
``dict``. :func:`build_server` creates an ``MCPServer`` and registers the
tools, each one closing over the :class:`OnboardingTools` it is given.

Wiring
------
:func:`main` reads the settings, builds the runtime service
(:func:`build_runtime_service`: the DynamoDB repository and the boto3-backed
AWS Quick and Secrets Manager clients), wraps it in :class:`OnboardingTools`,
and serves :func:`build_server`'s result. Every dependency is passed in
explicitly; the module keeps no service or settings state. Importing it
creates no server, boto3 client, or DynamoDB resource and makes no network
call. Tests build :class:`OnboardingTools` (or a whole server) from an
in-memory service.

Tenant identity (security)
--------------------------
This deployment authenticates callers but does NOT propagate per-user identity
to the backend. The AgentCore Gateway's Cognito CUSTOM_JWT authorizer gates
access — only a caller presenting a valid Cognito access token reaches the
server — but the gateway signs the gateway->runtime hop with its own IAM role,
so the runtime never sees the end user. Every authenticated AWS Quick admin
therefore maps to a single shared tenant.

That tenant is fixed when :class:`OnboardingTools` is built: :func:`main` takes
it from the ``ONBOARDING_TENANT`` environment variable (default ``shared``). It
never comes from a request or from tool arguments — none of the tool
signatures accept one.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from botocore.exceptions import NoRegionError
from mcp.server.mcpserver import MCPServer

from quick_admin_buddy_mcp.config import Settings, SettingsError, load_settings
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.requests import (
    DisableActionConnectorRequest,
    DisableKnowledgeBaseRequest,
    EnableActionConnectorRequest,
    EnableKnowledgeBaseRequest,
    SetExtensionStatusRequest,
    ShareActionConnectorRequest,
    ShareKnowledgeBaseRequest,
)
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.observability import (
    configure_logging,
    log_startup,
    logged_tool_call,
)
from quick_admin_buddy_mcp.quick import BotoQuickAdminClient, BotoSecretResolver
from quick_admin_buddy_mcp.state import DynamoDbStateRepository, TenantId

# The name the server reports to MCP clients.
SERVER_NAME = "quick-admin-buddy-mcp"


# ---------------------------------------------------------------------------
# The tool layer
# ---------------------------------------------------------------------------


class OnboardingTools:
    """One method per MCP tool, bound to a service and the shared tenant.

    Operations with more than one argument take their request model. Each
    method calls its own operation object (``service.operations.<tool>``),
    writes the per-call log line, and returns the sanitized
    ``ToolResult.to_dict()`` the operation produced.
    """

    def __init__(self, service: OnboardingService, tenant: TenantId) -> None:
        self._service = service
        self._tenant = tenant

    @property
    def service(self) -> OnboardingService:
        """The service the tools delegate to."""
        return self._service

    @property
    def tenant(self) -> TenantId:
        """The single shared tenant every call acts on."""
        return self._tenant

    def list_connector_catalog(self, suite: str | None = None) -> dict:
        """List the connector catalog, optionally filtered by productivity suite.

        Catalog listing needs no tenant.
        """
        ops = self._service.operations
        return logged_tool_call(
            "list_connector_catalog",
            lambda: ops.list_connector_catalog(suite).to_dict(),
        )

    def set_productivity_suite(self, suite: str) -> dict:
        """Record the tenant's productivity suite."""
        ops = self._service.operations
        return logged_tool_call(
            "set_productivity_suite",
            lambda: ops.set_productivity_suite(self._tenant, suite).to_dict(),
        )

    def enable_action_connector(self, request: EnableActionConnectorRequest) -> dict:
        """Enable a connector as an action connector (CreateActionConnector)."""
        ops = self._service.operations
        return logged_tool_call(
            "enable_action_connector",
            lambda: ops.enable_action_connector(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def disable_action_connector(self, request: DisableActionConnectorRequest) -> dict:
        """Disable a connector's action connector integration."""
        ops = self._service.operations
        return logged_tool_call(
            "disable_action_connector",
            lambda: ops.disable_action_connector(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def enable_kb_integration(self, request: EnableKnowledgeBaseRequest) -> dict:
        """Enable a connector as a knowledge base integration (CreateKnowledgeBase)."""
        ops = self._service.operations
        return logged_tool_call(
            "enable_kb_integration",
            lambda: ops.enable_kb_integration(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def disable_kb_integration(self, request: DisableKnowledgeBaseRequest) -> dict:
        """Disable a connector's knowledge base integration."""
        ops = self._service.operations
        return logged_tool_call(
            "disable_kb_integration",
            lambda: ops.disable_kb_integration(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def share_action_connector(self, request: ShareActionConnectorRequest) -> dict:
        """Share an existing action connector with a Quick user or group."""
        ops = self._service.operations
        return logged_tool_call(
            "share_action_connector",
            lambda: ops.share_action_connector(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def share_kb_integration(self, request: ShareKnowledgeBaseRequest) -> dict:
        """Share an existing knowledge base with a Quick user or group."""
        ops = self._service.operations
        return logged_tool_call(
            "share_kb_integration",
            lambda: ops.share_kb_integration(self._tenant, request).to_dict(),
            connector_key=request.connector_key,
        )

    def get_tenant_configuration(self) -> dict:
        """Return the tenant's current onboarding configuration."""
        ops = self._service.operations
        return logged_tool_call(
            "get_tenant_configuration",
            lambda: ops.get_tenant_configuration(self._tenant).to_dict(),
        )

    def get_onboarding_summary(self) -> dict:
        """Return a structured recap of the tenant's onboarding."""
        ops = self._service.operations
        return logged_tool_call(
            "get_onboarding_summary",
            lambda: ops.get_onboarding_summary(self._tenant).to_dict(),
        )

    def list_extensions(self, suite: str | None = None) -> dict:
        """List AWS Quick extensions and each one's admin-attested status."""
        ops = self._service.operations
        return logged_tool_call(
            "list_extensions",
            lambda: ops.list_extensions(self._tenant, suite).to_dict(),
        )

    def set_extension_status(self, request: SetExtensionStatusRequest) -> dict:
        """Record the admin-attested status of an AWS Quick extension."""
        ops = self._service.operations
        return logged_tool_call(
            "set_extension_status",
            lambda: ops.set_extension_status(self._tenant, request).to_dict(),
            extension_key=request.extension_key,
        )


# ---------------------------------------------------------------------------
# Runtime wiring
# ---------------------------------------------------------------------------


def build_runtime_service(settings: Settings) -> OnboardingService:
    """Compose the runtime service: catalog + DynamoDB repo + boto3 clients.

    Creates the DynamoDB resource (no network call; it needs an AWS Region).
    The AWS Quick and Secrets Manager clients are created on their first use.
    """
    return OnboardingService(
        ConnectorCatalog(),
        DynamoDbStateRepository(settings.onboarding_state_table),
        BotoQuickAdminClient(),
        BotoSecretResolver(),
    )


def _tool(app: MCPServer) -> Callable[[Callable[..., dict]], Callable[..., dict]]:
    """Register a function as a tool, with its docstring as the description.

    The docstring is passed through :func:`inspect.cleandoc`, so the
    description doesn't depend on how deeply the function is indented.
    """

    def register(fn: Callable[..., dict]) -> Callable[..., dict]:
        app.tool(description=inspect.cleandoc(fn.__doc__ or ""))(fn)
        return fn

    return register


def build_server(tools: OnboardingTools) -> MCPServer:
    """Create the MCP server and register every tool on it.

    Each registered tool is a thin wrapper that builds the operation's request
    model and calls the matching :class:`OnboardingTools` method. The tool
    parameters are the MCP tool schema the client sees. No tool accepts a
    tenant argument: the tenant is the one ``tools`` was built with.
    """
    # In mcp 2.x, `stateless_http=True` and `host` are passed to run()
    # (see main()), not to the constructor.
    app = MCPServer(name=SERVER_NAME)

    @_tool(app)
    def list_connector_catalog(suite: str | None = None) -> dict:
        """List the connector catalog, optionally filtered by productivity suite."""
        return tools.list_connector_catalog(suite)

    @_tool(app)
    def set_productivity_suite(suite: str) -> dict:
        """Set the tenant's productivity suite (google_workspace or microsoft_365)."""
        return tools.set_productivity_suite(suite)

    @_tool(app)
    def enable_action_connector(
        connector_key: str,
        auth_type: str,
        auth_params: dict | None = None,
        secret_id: str | None = None,
        config: dict | None = None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
    ) -> dict:
        """Enable a connector as an action connector (CreateActionConnector).

        auth_type is 'custom_oauth_app' (3LO OAuth), 'service_to_service_oauth'
        (2LO client credentials), or 'api_key' (2LO API-token auth, e.g. Atlassian
        Confluence Cloud — the secret holds `api_key` and `domain_url`, plus
        optional `email`). Secret values (client_secret, api_key, and so on) must
        come from an AWS Secrets Manager secret passed as secret_id: a secret value
        passed inline in auth_params is rejected with validation_failure
        (data.error_type 'inline_secret_not_allowed') before anything is created.
        auth_params may carry only non-secret values (snake_case keys, for example
        client_id or domain_url), which override the same keys in the secret. If
        parameters are missing, the result lists them and includes the secret
        contract.

        secret_id must be the Secrets Manager secret NAME/ID (e.g.
        'quick-admin-buddy/outlook-custom-oauth'), never a full
        'arn:aws:secretsmanager:...' ARN. AWS Quick redacts ARN-shaped
        arguments, so an ARN is rejected with a validation_failure; if you only
        have the ARN, pass the part after ':secret:' (keep the trailing '-xxxxxx'
        suffix). The server can read only secrets named 'quick-admin-buddy/*'.

        share_with optionally names a Quick user (e.g. the admin themselves) to
        grant owner-equivalent permissions to on the new connector immediately -
        either a full Quick user ARN, or a bare user name assumed to be in the
        default namespace. Without it, the connector is visible only to the
        server's own execution role, not to any human Quick user. Use the AWS
        Quick USERNAME, not the user's email address. A user that does not exist
        is rejected with validation_failure (data.error_type 'principal_not_found')
        before anything is created or changed.

        connector_id_prefix optionally overrides the prefix used to build the
        connector's resource id (default 'quickadminbuddy-'); the id becomes
        '<prefix><connector_key>'. It must match [\\w\\-]+ (letters, digits,
        underscores, hyphens). Supply the same prefix to disable_action_connector
        to delete the same resource.
        """
        return tools.enable_action_connector(
            EnableActionConnectorRequest(
                connector_key=connector_key,
                auth_type=auth_type,
                secret_id=secret_id,
                auth_params=auth_params,
                config=config,
                share_with=share_with,
                connector_id_prefix=connector_id_prefix,
            ),
        )

    @_tool(app)
    def disable_action_connector(
        connector_key: str,
        connector_id_prefix: str | None = None,
    ) -> dict:
        """Disable a connector's action connector integration.

        connector_id_prefix must match the prefix used when the connector was
        enabled (default 'quickadminbuddy-') so the correct resource is deleted.
        """
        return tools.disable_action_connector(
            DisableActionConnectorRequest(
                connector_key=connector_key, connector_id_prefix=connector_id_prefix
            ),
        )

    @_tool(app)
    def enable_kb_integration(
        connector_key: str,
        auth_type: str,
        auth_params: dict | None = None,
        secret_id: str | None = None,
        config: dict | None = None,
        share_with: str | None = None,
        connector_id_prefix: str | None = None,
        owner: str | None = None,
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
        execution role. Ask the admin who should own the knowledge base. Use the
        AWS Quick USERNAME, not the user's email address. A user that does not
        exist is rejected with validation_failure (data.error_type
        'principal_not_found') before anything is created or changed. (KB Actions
        must match AWS Quick's OWNER/VIEWER roles, so there is no arbitrary
        share_with grant at create time; share_with is ignored for knowledge bases -
        use share_kb_integration afterward to grant another user the VIEWER role.)

        Credentials come only from an AWS Secrets Manager secret passed as
        secret_id (the secret NAME/ID, never a full 'arn:aws:secretsmanager:...'
        ARN). A secret value passed inline in auth_params or config is rejected with
        validation_failure (data.error_type 'inline_secret_not_allowed').
        admin_managed secret requirements by connector:
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
          - confluence: optional client_id/client_secret in a secret; plus
            config.confluence_url.

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
        return tools.enable_kb_integration(
            EnableKnowledgeBaseRequest(
                connector_key=connector_key,
                auth_type=auth_type,
                secret_id=secret_id,
                auth_params=auth_params,
                config=config,
                owner=owner,
                share_with=share_with,
                connector_id_prefix=connector_id_prefix,
            ),
        )

    @_tool(app)
    def disable_kb_integration(
        connector_key: str,
        connector_id_prefix: str | None = None,
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
        return tools.disable_kb_integration(
            DisableKnowledgeBaseRequest(
                connector_key=connector_key, connector_id_prefix=connector_id_prefix
            ),
        )

    @_tool(app)
    def share_action_connector(
        connector_key: str,
        share_with: str,
        connector_id_prefix: str | None = None,
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
        full ARN to share with a group or a non-default namespace). Use the AWS
        Quick USERNAME, not the user's email address. A user that does not exist
        is rejected with validation_failure (data.error_type 'principal_not_found')
        before anything is created or changed.

        connector_id_prefix must match the prefix used when the connector was
        created (default 'quickadminbuddy-') so the derived ActionConnectorId
        targets the same resource.
        """
        return tools.share_action_connector(
            ShareActionConnectorRequest(
                connector_key=connector_key,
                share_with=share_with,
                connector_id_prefix=connector_id_prefix,
            ),
        )

    @_tool(app)
    def share_kb_integration(
        connector_key: str,
        share_with: str,
        connector_id_prefix: str | None = None,
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
        full ARN to share with a group or a non-default namespace). Use the AWS
        Quick USERNAME, not the user's email address. A user that does not exist
        is rejected with validation_failure (data.error_type 'principal_not_found')
        before anything is created or changed.

        connector_id_prefix must match the prefix used when the knowledge base was
        created (default 'quickadminbuddy-') so the derived KnowledgeBaseId targets
        the same resource.
        """
        return tools.share_kb_integration(
            ShareKnowledgeBaseRequest(
                connector_key=connector_key,
                share_with=share_with,
                connector_id_prefix=connector_id_prefix,
            ),
        )

    @_tool(app)
    def get_tenant_configuration() -> dict:
        """Return the tenant's current onboarding configuration (suite + per-connector status)."""
        return tools.get_tenant_configuration()

    @_tool(app)
    def get_onboarding_summary() -> dict:
        """Return a stage-grouped recap of the tenant's onboarding."""
        return tools.get_onboarding_summary()

    @_tool(app)
    def list_extensions(suite: str | None = None) -> dict:
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
        return tools.list_extensions(suite)

    @_tool(app)
    def set_extension_status(extension_key: str, status: str) -> dict:
        """Record the admin-attested status of an AWS Quick extension.

        `status` must be one of `installed`, `skipped`, or `not_configured`. This is
        an admin declaration — no AWS Quick API call is made (extensions have
        none). The `extension_key` must be a valid extension (use `list_extensions`)
        and must match the tenant's stored suite, or a `validation_failure` is
        returned.
        """
        return tools.set_extension_status(
            SetExtensionStatusRequest(extension_key=extension_key, status=status)
        )

    return app


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
    rather than to the constructor. Log lines go to stdout, which the Runtime
    delivers to its CloudWatch log group.

    The settings are read once here, and every dependency is built from them
    before the server listens. An invalid setting, or no AWS Region, stops the
    server with a message that names the problem; otherwise one log line
    records the resolved settings.
    """
    configure_logging()
    try:
        settings = load_settings()
        service = build_runtime_service(settings)
    except SettingsError as exc:
        raise SystemExit(str(exc)) from None
    except NoRegionError:
        raise SystemExit(
            "No AWS Region is configured. Set AWS_DEFAULT_REGION (or a region in "
            "the AWS config file) and start the server again."
        ) from None
    log_startup(settings.log_view())
    app = build_server(OnboardingTools(service, settings.onboarding_tenant))
    app.run(
        transport="streamable-http",
        host=settings.mcp_bind_host,
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
