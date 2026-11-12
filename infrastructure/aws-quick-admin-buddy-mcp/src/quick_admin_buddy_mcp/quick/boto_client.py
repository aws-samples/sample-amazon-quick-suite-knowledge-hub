"""A ``boto3``-backed implementation of the :class:`QuickAdminClient` protocol.

:class:`BotoQuickAdminClient` fronts the five AWS Quick admin API actions over
``boto3`` and maps AWS failures onto the client's failure taxonomy:

* An API-level error response (``botocore.exceptions.ClientError``) becomes a
  :class:`QuickAdminApiError` carrying the AWS error ``Code`` (the domain layer
  maps this to Result_Status ``server_error``).
* A credential-resolution / authentication failure, or a network/timeout
  failure, becomes a :class:`QuickAdminInternalError` (the domain layer maps this
  to Result_Status ``internal_error``). No fallback
  credentials are ever attempted - the operation fails fast.
* Any other unexpected exception is also surfaced as
  :class:`QuickAdminInternalError`.

The underlying ``boto3`` client is created *without* explicit credentials so it
resolves the AgentCore Runtime execution role (the ``Agent_IAM_Identity``) from
the standard credential chain. No access key, secret, session
token, or profile is ever passed in.

Boto3 method-name assumption
----------------------------
AWS Quick admin actions are exposed via the QuickSight ``boto3`` client family
(the design registers an ``AWS::QuickSight::ActionConnector`` resource). The five
actions map to the correspondingly-named, snake_cased client methods:

* ``CreateActionConnector``                -> ``create_action_connector``
* ``DescribeActionConnector``              -> ``describe_action_connector``
* ``DescribeActionConnectorPermissions``   -> ``describe_action_connector_permissions``
* ``ListActionConnectors``                 -> ``list_action_connectors``
* ``SearchActionConnectors``               -> ``search_action_connectors``

The exact boto3 method names for these newer actions may differ across botocore
versions. The mapping is centralized in :data:`_BOTO_METHODS` and resolved
dynamically via ``getattr`` so it is easy to adjust in one place if a name
changes, and the request/response shapes are extracted best-effort with
``dict.get`` rather than assuming a rigid schema.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ReadTimeoutError,
)

from quick_admin_buddy_mcp.domain.types import AuthType
from quick_admin_buddy_mcp.observability import record_request_id

from .client import (
    DEFAULT_RESOURCE_ID_PREFIX,
    ConnectorDetail,
    DataSourceDetail,
    KnowledgeBaseDetail,
    PermissionDetail,
    QuickAdminApiError,
    QuickAdminInternalError,
    resource_id,
)

# The QuickSight boto3 service exposes the AWS Quick admin actions.
_DEFAULT_SERVICE_NAME = "quicksight"

# Central mapping from protocol action -> boto3 client method name. Kept in one
# place so the (possibly version-dependent) boto3 method names are trivial to
# adjust without touching the call sites. See the module docstring.
_BOTO_METHODS = {
    "create_action_connector": "create_action_connector",
    "describe_action_connector": "describe_action_connector",
    "describe_action_connector_permissions": "describe_action_connector_permissions",
    "list_action_connectors": "list_action_connectors",
    "search_action_connectors": "search_action_connectors",
    "delete_action_connector": "delete_action_connector",
    "create_knowledge_base": "create_knowledge_base",
    "delete_knowledge_base": "delete_knowledge_base",
    "create_data_source": "create_data_source",
    "delete_data_source": "delete_data_source",
    "update_action_connector_permissions": "update_action_connector_permissions",
    "update_knowledge_base_permissions": "update_knowledge_base_permissions",
    "describe_user": "describe_user",
}

# A Quick *user* ARN: arn:<partition>:quicksight:<region>:<account>:user/<namespace>/<name>.
# Group ARNs (".../group/...") deliberately do not match, so principal_exists()
# reports them as "unknown" rather than looking them up as users.
_QUICK_USER_ARN = re.compile(
    r"^arn:[\w-]+:quicksight:[\w-]*:(?P<account>\d{12}):user/(?P<namespace>[^/]+)/(?P<name>.+)$"
)

# Best-effort mapping from a catalog connector key to the CreateActionConnector
# ``Type`` enum value. Keys without a dedicated enum value fall back to
# ``GENERIC_HTTP``. These values follow the API reference's documented Type enum;
# they are centralized here so they are trivial to adjust as the API evolves.
_ACTION_TYPE_BY_KEY = {
    "gmail": "GMAIL",
    "google_chat": "GOOGLE_CHAT",
    "google_drive": "GOOGLE_DRIVE",
    "google_calendar": "GOOGLE_CALENDAR",
    "google_docs": "GOOGLE_DOCS",
    "google_meet": "GOOGLE_MEET",
    "google_sheets": "GOOGLE_SHEETS",
    "google_slides": "GOOGLE_SLIDES",
    "outlook": "MICROSOFT_OUTLOOK",
    "ms_teams": "MICROSOFT_TEAMS",
    "onedrive": "MICROSOFT_ONEDRIVE",
    "sharepoint": "MICROSOFT_SHAREPOINT",
    "confluence": "ATLASSIAN_CONFLUENCE",
    "atlassian": "JIRA_CLOUD",
}
_DEFAULT_ACTION_TYPE = "GENERIC_HTTP"

# Note on the Google ``Type`` values (GMAIL, GOOGLE_CALENDAR, GOOGLE_DRIVE, ...)
# and GMAIL in particular: these are the native AWS Quick action-connector
# ``Type`` values the live service accepts (and reports via
# DescribeActionConnector), but no bundled botocore version (through 1.43.117)
# lists them in the ``Type`` enum. That is harmless here: botocore does NOT
# enforce this enum client-side (verified end-to-end - a fresh client sends the
# value straight to the service, which accepts it), so sending them needs no SDK
# workaround. Pattern: the connector's display name upper-cased with underscores
# (per the AWS Quick "Supported connector types" reference). Correct Google
# connector behavior also requires ``BaseEndpoint`` = the provider API base URL
# (carried via auth_params ``domain_url``), not the admin's org domain.

# Best-effort mapping from a catalog connector key to the ``CreateDataSource``
# ``Type`` enum value (verified against the live quicksight service model,
# botocore 1.43.114: valid KB-source Types include SHAREPOINT, ONE_DRIVE,
# GOOGLE_DRIVE, CONFLUENCE). An admin-managed knowledge base is created in two
# steps - first this data source, then the knowledge base that references it.
_DATA_SOURCE_TYPE_BY_KEY = {
    "onedrive": "ONE_DRIVE",
    "sharepoint": "SHAREPOINT",
    "google_drive": "GOOGLE_DRIVE",
    "confluence": "CONFLUENCE",
}

# Best-effort mapping from a catalog connector key to the KB
# ``templateConfiguration.template`` ``type`` discriminator. The documented
# values (KbTemplateConfiguration API reference) are SHAREPOINTV3, ONEDRIVEV3,
# and GOOGLEDRIVEV3. Confluence is NOT in the documented list; CONFLUENCEV3 is
# used by analogy and is UNVERIFIED - the ``template`` field is a free-form
# JSON document type (botocore does not validate its contents), so a wrong
# value is only caught server-side. Re-verify against a real Confluence KB.
# The confluence entries here are unreachable through enable_kb_integration,
# which rejects confluence as a console-only knowledge base
# (_KB_CONSOLE_ONLY_KEYS in domain/operations/base.py).
_KB_TEMPLATE_TYPE_BY_KEY = {
    "onedrive": "ONEDRIVEV3",
    "sharepoint": "SHAREPOINTV3",
    "google_drive": "GOOGLEDRIVEV3",
    "confluence": "CONFLUENCEV3",
}

# Owner-equivalent Actions grant for an initial share of a newly created
# action connector. Confirmed as ground truth on 2026-10-06 by reading
# DescribeActionConnectorPermissions on a real action connector a human admin
# created and shared with a user through the AWS Quick console (not
# guessed): the console's share grants exactly this 6-action set. A bare
# single-action probe (e.g. just "DescribeActionConnector") was independently
# confirmed valid too, but the console's own grant is the authoritative
# "share with" default this server mirrors.
_ACTION_CONNECTOR_SHARE_ACTIONS: tuple[str, ...] = (
    "quicksight:UpdateActionConnector",
    "quicksight:DescribeActionConnector",
    "quicksight:DescribeActionConnectorPermissions",
    "quicksight:DeleteActionConnector",
    "quicksight:UpdateActionConnectorPermissions",
    "quicksight:ListActionConnectors",
)

# Knowledge-base permission ROLES. Unlike action connectors (which accept an
# arbitrary owner-equivalent action set), AWS Quick validates a knowledge
# base's Permissions.Actions against a fixed set of ROLES and rejects anything
# else with InvalidParameterValueException ("... do not match a supported
# KnowledgeBase role. Supported roles: OWNER [...], VIEWER [...]"). The two
# role action sets below are VERIFIED verbatim from that API error / a real
# console grant (2026-10-26):
#   - OWNER: full management (describe/update/delete + refresh schedules +
#     ingestions + permissions). Used as the CREATE-time owner grant so a human
#     Quick user owns the KB (the server's execution role otherwise owns it).
#   - VIEWER: describe only. Used when SHARING an existing KB with a user/group.
# Do NOT reuse the action-connector share set here (it is rejected).
_KB_OWNER_ACTIONS: tuple[str, ...] = (
    "quicksight:DescribeKnowledgeBase",
    "quicksight:DescribeKnowledgeBasePermissions",
    "quicksight:UpdateKnowledgeBase",
    "quicksight:UpdateKnowledgeBasePermissions",
    "quicksight:DeleteKnowledgeBase",
    "quicksight:CreateKnowledgeBaseRefreshSchedule",
    "quicksight:DescribeKnowledgeBaseRefreshSchedule",
    "quicksight:ListKnowledgeBaseRefreshSchedules",
    "quicksight:UpdateKnowledgeBaseRefreshSchedule",
    "quicksight:DeleteKnowledgeBaseRefreshSchedule",
    "quicksight:CreateKnowledgeBaseIngestion",
    "quicksight:CancelKnowledgeBaseIngestion",
    "quicksight:DescribeKnowledgeBaseIngestion",
    "quicksight:ListKnowledgeBaseIngestions",
)
_KB_VIEWER_ACTIONS: tuple[str, ...] = ("quicksight:DescribeKnowledgeBase",)

# The resource-id helper lives in .client so the domain layer can name the
# resources it rolls back; keep the historical private names as aliases.
_DEFAULT_RESOURCE_ID_PREFIX = DEFAULT_RESOURCE_ID_PREFIX
_resource_id = resource_id


def _bare_domain(value: str | None) -> str | None:
    """Return a host with any URL scheme and trailing slash stripped.

    AWS Quick's ``SharePointParameters.SharePointDomain`` must be the bare
    host (e.g. ``contoso.sharepoint.com``); the service prepends ``https://``
    itself, so passing a full URL produces a doubled-scheme host that fails
    Microsoft Entra auth (``AADSTS500011 ... https://https://...``). Admins and
    secrets commonly store ``domain_url`` as a full URL, so normalize here.
    Returns ``None``/empty unchanged.
    """
    if not value:
        return value
    v = value.strip()
    for scheme in ("https://", "http://"):
        if v.lower().startswith(scheme):
            v = v[len(scheme) :]
            break
    return v.rstrip("/")


# Credential-resolution / authentication failure types. These indicate the
# Agent_IAM_Identity could not be resolved or was rejected, so
# we fail fast and never attempt fallback credentials. Some names are only
# present on newer botocore versions, so they are resolved defensively below.
_CREDENTIAL_ERROR_TYPES: tuple[type[BaseException], ...] = (
    NoCredentialsError,
    PartialCredentialsError,
)
for _optional_name in ("CredentialRetrievalError", "UnauthorizedSSOTokenError"):
    try:  # pragma: no cover - depends on installed botocore version
        import botocore.exceptions as _boto_exc

        _CREDENTIAL_ERROR_TYPES = _CREDENTIAL_ERROR_TYPES + (
            getattr(_boto_exc, _optional_name),
        )
    except AttributeError:  # pragma: no cover - older botocore
        pass

# Network / timeout failure types -> internal_error.
_NETWORK_ERROR_TYPES: tuple[type[BaseException], ...] = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
)


def _custom_oauth_app_config(auth_params: dict) -> dict:
    """``custom_oauth_app`` (3LO) -> ``OAUTH2_AUTHORIZATION_CODE``."""
    return {
        "AuthenticationType": "OAUTH2_AUTHORIZATION_CODE",
        "AuthenticationMetadata": {
            "AuthorizationCodeGrantMetadata": {
                "BaseEndpoint": auth_params.get("domain_url"),
                "RedirectUrl": auth_params.get("redirect_url"),
                "AuthorizationCodeGrantCredentialsSource": "PLAIN_CREDENTIALS",
                "AuthorizationCodeGrantCredentialsDetails": {
                    "AuthorizationCodeGrantDetails": {
                        "ClientId": auth_params.get("client_id"),
                        "ClientSecret": auth_params.get("client_secret"),
                        "TokenEndpoint": auth_params.get("token_url"),
                        "AuthorizationEndpoint": auth_params.get("authorization_url"),
                    }
                },
            }
        },
    }


def _service_to_service_oauth_config(auth_params: dict) -> dict:
    """``service_to_service_oauth`` (2LO client credentials) ->
    ``OAUTH2_CLIENT_CREDENTIALS``."""
    return {
        "AuthenticationType": "OAUTH2_CLIENT_CREDENTIALS",
        "AuthenticationMetadata": {
            "ClientCredentialsGrantMetadata": {
                "BaseEndpoint": auth_params.get("domain_url"),
                "ClientCredentialsSource": "PLAIN_CREDENTIALS",
                "ClientCredentialsDetails": {
                    "ClientCredentialsGrantDetails": {
                        "ClientId": auth_params.get("client_id"),
                        "ClientSecret": auth_params.get("client_secret"),
                        "TokenEndpoint": auth_params.get("token_url"),
                    }
                },
            }
        },
    }


def _api_key_config(auth_params: dict) -> dict:
    """``api_key`` (2LO API token, e.g. Atlassian Confluence Cloud) ->
    ``API_KEY`` / ``ApiKeyConnectionMetadata``.

    BaseEndpoint and ApiKey are required; Email is optional (for Confluence it
    is the Atlassian account email, and api_key is the API token). Email is
    omitted when not provided so an empty value is not sent.
    """
    metadata: dict[str, Any] = {
        "BaseEndpoint": auth_params.get("domain_url"),
        "ApiKey": auth_params.get("api_key"),
    }
    email = auth_params.get("email")
    if email:
        metadata["Email"] = email
    return {
        "AuthenticationType": "API_KEY",
        "AuthenticationMetadata": {"ApiKeyConnectionMetadata": metadata},
    }


# Action connector auth type -> AuthenticationConfig builder. The two OAuth
# methods use the PLAIN_CREDENTIALS source: the server resolves the client id
# and secret from the admin's Secrets Manager secret and passes them directly.
# Knowledge-base auth types (user_managed / admin_managed) are not action
# connector methods, so they have no entry.
_AUTH_CONFIG_BUILDERS: dict[AuthType, Callable[[dict], dict]] = {
    AuthType.CUSTOM_OAUTH_APP: _custom_oauth_app_config,
    AuthType.SERVICE_TO_SERVICE_OAUTH: _service_to_service_oauth_config,
    AuthType.API_KEY: _api_key_config,
}


class BotoQuickAdminClient:
    """A ``boto3``-backed :class:`QuickAdminClient`.

    The boto3 client, service name, and region can be injected for testability;
    by default a boto3 QuickSight client is created lazily on first use, without
    explicit credentials, so it resolves the AgentCore Runtime execution role
    (the ``Agent_IAM_Identity``).
    """

    def __init__(
        self,
        client: Any = None,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
        region_name: str | None = None,
        aws_account_id: str | None = None,
    ) -> None:
        self._client = client
        self._service_name = service_name
        self._region_name = region_name
        # ``AwsAccountId`` is a required field on every Quick admin action but
        # is never supplied by a caller; it is resolved lazily (once) via STS
        # from the same execution-role credential chain as the boto3 client
        # itself, mirroring how the client's own credentials are resolved with
        # no explicit values passed in. Injectable for tests.
        self._aws_account_id = aws_account_id

    # -- boto3 client resolution ----------------------------------------

    def _get_client(self) -> Any:
        """Return the boto3 client, creating it lazily on first use.

        The client is created WITHOUT explicit credentials so boto3 resolves the
        execution role from the standard credential chain.
        A failure to build the client (e.g. no credentials/region available) is
        surfaced as an internal error - never a silent fallback.
        """
        if self._client is None:
            try:
                import boto3  # imported lazily to keep import cost off the hot path

                kwargs: dict[str, Any] = {}
                if self._region_name is not None:
                    kwargs["region_name"] = self._region_name
                # NOTE: no aws_access_key_id / aws_secret_access_key / profile is
                # passed - the execution role is resolved by boto3 itself.
                self._client = boto3.client(self._service_name, **kwargs)
            except (*_CREDENTIAL_ERROR_TYPES, *_NETWORK_ERROR_TYPES) as exc:
                raise QuickAdminInternalError(self._auth_failure_message(exc)) from exc
            except Exception as exc:  # noqa: BLE001 - fail fast, classify as internal
                raise QuickAdminInternalError(
                    "Failed to initialize the AWS Quick admin client. "
                    "Verify the execution role and region are configured, then retry."
                ) from exc
        return self._client

    def _get_aws_account_id(self) -> str:
        """Return the caller's AWS account id, resolving it lazily via STS.

        Every Quick admin action requires ``AwsAccountId`` in its request. It
        is resolved once (then cached) from the same execution-role credential
        chain as the QuickSight client - no explicit credentials are passed.
        A resolution failure is an internal error: no
        fallback account id is ever guessed.
        """
        if self._aws_account_id is None:
            try:
                import boto3

                kwargs: dict[str, Any] = {}
                if self._region_name is not None:
                    kwargs["region_name"] = self._region_name
                sts = boto3.client("sts", **kwargs)
                self._aws_account_id = sts.get_caller_identity()["Account"]
            except (*_CREDENTIAL_ERROR_TYPES, *_NETWORK_ERROR_TYPES) as exc:
                raise QuickAdminInternalError(self._auth_failure_message(exc)) from exc
            except Exception as exc:  # noqa: BLE001 - fail fast, classify as internal
                raise QuickAdminInternalError(
                    "Could not resolve the AWS account id for the AWS Quick "
                    "admin API request (STS call failed). Verify the execution "
                    "role is configured, then retry."
                ) from exc
        return self._aws_account_id

    def _get_region(self) -> str:
        """Return the effective region the QuickSight client is bound to.

        Prefers the explicit ``region_name`` the client was constructed with;
        otherwise reads the *resolved* region off the lazily-created boto3
        client (``client.meta.region_name``), which reflects boto3's own
        resolution (explicit kwarg, then the ``AWS_REGION``/
        ``AWS_DEFAULT_REGION`` environment variable, then the shared config
        file) - the same region the AgentCore Runtime itself is deployed in,
        since that is what populates the execution environment. Used to build
        a default-namespace user ARN from a bare username (never guessed or
        hardcoded).
        """
        if self._region_name is not None:
            return self._region_name
        region = self._get_client().meta.region_name
        if not region:
            raise QuickAdminInternalError(
                "Could not determine the AWS region to build a Quick user ARN "
                "(no explicit region and the resolved boto3 client has none). "
                "Set the AWS_REGION environment variable, then retry."
            )
        return region

    @staticmethod
    def _resolve_principal_arn(principal: str, account_id: str, region: str) -> str:
        """Return a full Quick user ARN for a ``Permissions`` grant.

        ``principal`` is accepted as either:

        * a full ARN (starts with ``arn:``) - returned unchanged, so a caller
          who already knows the exact ARN (e.g. a non-default namespace, or a
          group rather than a user) is never second-guessed; or
        * a bare name - assumed to be a *user* in the ``default`` namespace
          (AWS Quick's only namespace unless an admin has created others)
          and expanded to ``arn:aws:quicksight:<region>:<account>:user/default/<name>``,
          using the region/account the AgentCore Runtime itself is deployed in.
        """
        if principal.startswith("arn:"):
            return principal
        return f"arn:aws:quicksight:{region}:{account_id}:user/default/{principal}"

    def _build_permissions(
        self, request: dict, share_actions: tuple[str, ...]
    ) -> list[dict] | None:
        """Build the ``Permissions`` list for a create request, if requested.

        ``request["share_with"]`` is the provider-agnostic field the domain
        layer sets from the ``share_with`` tool argument: a user name or ARN to
        grant the owner-equivalent ``share_actions`` to immediately on create,
        so the admin (typically themselves) can see/manage the resource right
        away instead of it being visible only to the Runtime's own execution
        role. Returns ``None`` (omit ``Permissions`` entirely) when no
        ``share_with`` was given - the API default is no extra grant.
        """
        share_with = request.get("share_with")
        if not share_with:
            return None
        principal_arn = self._resolve_principal_arn(
            share_with, self._get_aws_account_id(), self._get_region()
        )
        return [{"Principal": principal_arn, "Actions": list(share_actions)}]

    def _build_kb_owner_permissions(self, request: dict) -> list[dict] | None:
        """Build the CreateKnowledgeBase ``Permissions`` granting the OWNER role.

        ``request["owner"]`` is the provider-agnostic field the domain layer sets
        from the ``owner`` tool argument: the Quick user (bare name or ARN) who
        should OWN the knowledge base. A knowledge base created without an owner
        grant is visible only to the server's execution role, so the admin must
        name a human owner. The granted Actions are the fixed OWNER role
        (:data:`_KB_OWNER_ACTIONS`) - AWS Quick validates KB Actions against
        its OWNER/VIEWER roles and rejects anything else. Returns ``None`` (omit
        ``Permissions``) when no owner is given.
        """
        owner = request.get("owner")
        if not owner:
            return None
        principal_arn = self._resolve_principal_arn(
            owner, self._get_aws_account_id(), self._get_region()
        )
        return [{"Principal": principal_arn, "Actions": list(_KB_OWNER_ACTIONS)}]

    # -- protocol methods -----------------------------------------------

    def create_action_connector(self, key: str, request: dict) -> ConnectorDetail:
        """Create/configure an action connector (``CreateActionConnector``).

        Builds the API request from the provider-agnostic ``request`` assembled
        by the domain layer. Field extraction is best-effort / schema-tolerant.
        """
        params = self._build_action_connector_params(key, request or {})
        response = self._invoke("create_action_connector", params)
        return self._to_connector_detail(key, response)

    def delete_action_connector(
        self, key: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        """Delete/deactivate an action connector (``DeleteActionConnector``).

        ``id_prefix`` must match the prefix used when the connector was created
        so the derived ``ActionConnectorId`` targets the same resource.
        """
        response = self._invoke(
            "delete_action_connector",
            {
                "AwsAccountId": self._get_aws_account_id(),
                "ActionConnectorId": _resource_id(key, id_prefix),
            },
        )
        return self._to_connector_detail(key, response)

    def create_knowledge_base(self, key: str, request: dict) -> KnowledgeBaseDetail:
        """Create a knowledge base integration (``CreateKnowledgeBase``)."""
        params = self._build_knowledge_base_params(key, request or {})
        response = self._invoke("create_knowledge_base", params)
        return self._to_knowledge_base_detail(key, response)

    def delete_knowledge_base(
        self, key: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        """Delete a knowledge base integration (``DeleteKnowledgeBase``).

        ``id_prefix`` must match the prefix used when the knowledge base was
        created so the derived ``KnowledgeBaseId`` targets the same resource.
        """
        response = self._invoke(
            "delete_knowledge_base",
            {
                "AwsAccountId": self._get_aws_account_id(),
                "KnowledgeBaseId": _resource_id(key, id_prefix),
            },
        )
        return self._to_knowledge_base_detail(key, response)

    def create_data_source(self, key: str, request: dict) -> DataSourceDetail:
        """Create a knowledge-base data source (``CreateDataSource``).

        First half of the two-step admin-managed knowledge-base create: builds
        the source-specific ``DataSourceParameters`` and ``Credentials`` from the
        provider-agnostic ``request`` and returns the created data source's
        ``arn`` so the caller can create the knowledge base that references it.
        """
        params = self._build_data_source_params(key, request or {})
        response = self._invoke("create_data_source", params)
        return self._to_data_source_detail(key, response)

    def delete_data_source(
        self, key: str, id_prefix: str | None = None
    ) -> DataSourceDetail:
        """Delete a knowledge-base data source (``DeleteDataSource``).

        ``id_prefix`` must match the prefix used when the data source was created
        so the derived ``DataSourceId`` targets the same resource.
        """
        response = self._invoke(
            "delete_data_source",
            {
                "AwsAccountId": self._get_aws_account_id(),
                "DataSourceId": _resource_id(key, id_prefix),
            },
        )
        return self._to_data_source_detail(key, response)

    def share_action_connector(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> ConnectorDetail:
        """Share an existing action connector (``UpdateActionConnectorPermissions``).

        Grants ``principal`` the owner-equivalent action-connector permission
        set (:data:`_ACTION_CONNECTOR_SHARE_ACTIONS`) on the connector whose
        ``ActionConnectorId`` is derived from ``key`` + ``id_prefix``.
        ``principal`` is resolved to a full Quick user ARN the same way the
        share-on-create path resolves it (a bare name becomes a ``default``
        namespace user ARN; a full ``arn:`` is used unchanged, so a group ARN
        or non-default namespace is honored).
        """
        principal_arn = self._resolve_principal_arn(
            principal, self._get_aws_account_id(), self._get_region()
        )
        response = self._invoke(
            "update_action_connector_permissions",
            {
                "AwsAccountId": self._get_aws_account_id(),
                "ActionConnectorId": _resource_id(key, id_prefix),
                "GrantPermissions": [
                    {
                        "Principal": principal_arn,
                        "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS),
                    }
                ],
            },
        )
        return self._to_connector_detail(key, response)

    def share_knowledge_base(
        self, key: str, principal: str, id_prefix: str | None = None
    ) -> KnowledgeBaseDetail:
        """Share an existing knowledge base (``UpdateKnowledgeBasePermissions``).

        Grants ``principal`` the **VIEWER** knowledge-base role
        (:data:`_KB_VIEWER_ACTIONS` = ``quicksight:DescribeKnowledgeBase``) on
        the knowledge base whose ``KnowledgeBaseId`` is derived from ``key`` +
        ``id_prefix``. AWS Quick validates KB permission Actions against fixed
        roles (OWNER / VIEWER) and rejects anything else, so sharing grants the
        VIEWER role only — full management stays with the KB's owner (set at
        create time). ``principal`` is resolved to a full Quick user ARN the same
        way the share-on-create path resolves it.
        """
        principal_arn = self._resolve_principal_arn(
            principal, self._get_aws_account_id(), self._get_region()
        )
        response = self._invoke(
            "update_knowledge_base_permissions",
            {
                "AwsAccountId": self._get_aws_account_id(),
                "KnowledgeBaseId": _resource_id(key, id_prefix),
                "GrantPermissions": [
                    {
                        "Principal": principal_arn,
                        "Actions": list(_KB_VIEWER_ACTIONS),
                    }
                ],
            },
        )
        return self._to_knowledge_base_detail(key, response)

    def principal_exists(self, principal: str) -> bool | None:
        """Check that a Quick user exists (``DescribeUser``) before granting to it.

        A ``Permissions`` grant naming a user that does not exist makes
        ``CreateActionConnector`` / ``CreateKnowledgeBase`` fail with a generic
        ``InvalidParameterValueException`` ("One or more parameters are
        invalid") that does not say which parameter. Looking the user up first
        lets the caller report "user X does not exist" instead. Verified against
        the live API: a missing user (for example an email passed instead of
        the username) returns ``ResourceNotFoundException`` ("USER not found");
        user names are case-sensitive.

        Returns ``True`` / ``False`` for an existing / missing user (or
        namespace). Returns ``None`` (unknown, proceed) for a group ARN, a user
        in another account, or when the lookup itself fails - e.g. the
        execution role lacks ``quicksight:DescribeUser`` because the stack
        predates it - so this pre-flight check never blocks a valid request.
        """
        try:
            if principal.startswith("arn:"):
                match = _QUICK_USER_ARN.match(principal)
                if match is None:
                    return None  # group (or other) principal: not a user lookup
                account_id = match.group("account")
                if account_id != self._get_aws_account_id():
                    return None
                namespace, user_name = match.group("namespace"), match.group("name")
            else:
                account_id = self._get_aws_account_id()
                namespace, user_name = "default", principal
            self._invoke(
                "describe_user",
                {
                    "AwsAccountId": account_id,
                    "Namespace": namespace,
                    "UserName": user_name,
                },
            )
        except QuickAdminApiError as exc:
            if exc.code == "ResourceNotFoundException":
                return False
            return None
        except QuickAdminInternalError:
            return None
        return True

    def describe_action_connector(self, key: str) -> ConnectorDetail:
        """Return detail for a single action connector."""
        response = self._invoke("describe_action_connector", {"Key": key})
        return self._to_connector_detail(key, response)

    def describe_action_connector_permissions(self, key: str) -> PermissionDetail:
        """Return permission detail for a connector."""
        response = self._invoke("describe_action_connector_permissions", {"Key": key})
        return self._to_permission_detail(key, response)

    def list_action_connectors(self) -> list[ConnectorDetail]:
        """Return all of a tenant's action connectors."""
        response = self._invoke("list_action_connectors", {})
        return self._to_connector_list(response)

    def search_action_connectors(self, query: dict) -> list[ConnectorDetail]:
        """Return action connectors matching ``query``."""
        response = self._invoke("search_action_connectors", dict(query or {}))
        return self._to_connector_list(response)

    # -- invocation + error mapping -------------------------------------

    def _invoke(self, action: str, params: dict) -> dict:
        """Call the boto3 method for ``action`` and map failures to the taxonomy.

        * ``ClientError`` (an API-level error response) -> :class:`QuickAdminApiError`
          with a sanitized, actionable message and ``code`` set to the AWS error
          code.
        * credential-resolution / network / timeout failures ->
          :class:`QuickAdminInternalError`, with no fallback attempt.
        * any other unexpected exception -> :class:`QuickAdminInternalError`.
        """
        client = self._get_client()
        method_name = _BOTO_METHODS[action]
        method: Callable[..., dict] = getattr(client, method_name)

        try:
            response = method(**params) or {}
            record_request_id(response)
            return response
        except ClientError as exc:
            record_request_id(exc)
            code = self._error_code(exc)
            detail = self._error_message(exc)
            # Include the AWS error Code AND the service's own error message
            # (actionable, names the offending parameter) but never raw
            # credentials or stack detail. The message is produced by the AWS
            # Quick service from the request and contains no caller secrets
            # (secret values travel via Credentials.SecretArn, which the service
            # does not echo).
            message = (
                f"The AWS Quick admin API rejected the {action} request"
                + (f" (error code: {code})" if code else "")
                + (f": {detail}" if detail else "")
                + ". Review the connector configuration and permissions, then retry."
            )
            raise QuickAdminApiError(message, code=code) from exc
        except _CREDENTIAL_ERROR_TYPES as exc:
            # Auth-resolution failure: fail fast, no fallback credentials.
            raise QuickAdminInternalError(self._auth_failure_message(exc)) from exc
        except _NETWORK_ERROR_TYPES as exc:
            raise QuickAdminInternalError(
                "Could not reach the AWS Quick admin API "
                "(network or timeout error). Check connectivity and retry."
            ) from exc
        except ParamValidationError as exc:
            # The request did not match the API model and was rejected by
            # botocore BEFORE any network call. This is a request-shape defect
            # (a required field omitted, an unknown field, or a wrong nested
            # structure), not an internal/network failure - so surface it as a
            # server_error carrying the actual validation detail rather than
            # letting it fall through to the generic "unexpected internal
            # error" catch-all, which would hide the real cause (the message
            # stays actionable and carries no raw credentials). The message is produced by
            # botocore from the service model and contains no caller secrets.
            raise QuickAdminApiError(
                f"The AWS Quick admin API rejected the {action} request "
                f"(parameter validation failed): {exc}. "
                "Review the connector configuration and required fields, then retry.",
                code="ParamValidationError",
            ) from exc
        except QuickAdminApiError:
            raise
        except QuickAdminInternalError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify anything else as internal
            raise QuickAdminInternalError(
                "An unexpected internal error occurred while calling the AWS "
                "Quick admin API. Retry, and if the problem persists contact support."
            ) from exc

    @staticmethod
    def _auth_failure_message(_exc: BaseException) -> str:
        """A sanitized, actionable auth-failure message (no credential detail)."""
        return (
            "Could not authenticate to the AWS Quick admin API using the "
            "agent execution role. Verify the IAM identity is configured and "
            "authorized, then retry. No fallback credentials were attempted."
        )

    @staticmethod
    def _error_code(exc: ClientError) -> str | None:
        """Best-effort extraction of the AWS error code from a ClientError."""
        try:
            code = exc.response.get("Error", {}).get("Code")
        except AttributeError:
            return None
        return code or None

    @staticmethod
    def _error_message(exc: ClientError) -> str | None:
        """Best-effort extraction of the AWS error *message* from a ClientError.

        This is the AWS Quick service's own description of what was wrong
        (e.g. which parameter value was invalid). It is service-generated text
        and carries no caller credentials, so it is safe to surface in the
        actionable error alongside the code.
        """
        try:
            message = exc.response.get("Error", {}).get("Message")
        except AttributeError:
            return None
        return message or None

    # -- request assembly (provider-agnostic request -> API params) ------

    @staticmethod
    def _authentication_config(request: dict) -> dict:
        """Build the real ``CreateActionConnector`` ``AuthenticationConfig``.

        ``AuthenticationConfig.AuthenticationMetadata`` is a *tagged union* in
        the real API model (verified against the live ``quicksight`` service
        model, botocore 1.42.29 and 1.43.108): exactly one of
        ``AuthorizationCodeGrantMetadata`` / ``ClientCredentialsGrantMetadata`` /
        ``BasicAuthConnectionMetadata`` / ``ApiKeyConnectionMetadata`` /
        ``NoneConnectionMetadata`` / ``IamConnectionMetadata`` may be set - a
        flat dict of the domain's snake_case auth parameters is not a valid
        member and raises ``ParamValidationError``.

        The mapping is the :data:`_AUTH_CONFIG_BUILDERS` table, keyed by
        :class:`~quick_admin_buddy_mcp.domain.types.AuthType`: adding an action
        connector auth type is one entry there, and an auth type without a
        builder fails at once instead of sending a malformed request.
        """
        raw = request.get("auth_type")
        try:
            auth_type = AuthType(raw)
        except ValueError:
            auth_type = None
        builder = _AUTH_CONFIG_BUILDERS.get(auth_type) if auth_type else None
        if builder is None:
            # The domain layer validates auth_type before a create call is ever
            # reached, so this should be unreachable in practice. Fail fast
            # rather than send a request guaranteed to be rejected.
            raise QuickAdminInternalError(
                f"No AuthenticationConfig mapping for auth_type {raw!r}. "
                "This is a server defect, not a caller error - please report it."
            )
        return builder(dict(request.get("auth_params") or {}))

    def _build_action_connector_params(self, key: str, request: dict) -> dict:
        """Assemble ``CreateActionConnector`` request parameters."""
        params: dict[str, Any] = {
            "AwsAccountId": self._get_aws_account_id(),
            "ActionConnectorId": _resource_id(key, request.get("id_prefix")),
            "Name": request.get("name") or key,
            "Type": _ACTION_TYPE_BY_KEY.get(key, _DEFAULT_ACTION_TYPE),
            "AuthenticationConfig": self._authentication_config(request),
        }
        description = request.get("description")
        if description:
            params["Description"] = description
        permissions = self._build_permissions(request, _ACTION_CONNECTOR_SHARE_ACTIONS)
        if permissions:
            params["Permissions"] = permissions
        return params

    # -- knowledge-base data source assembly (CreateDataSource) ----------

    @staticmethod
    def _data_source_parameters(key: str, config: dict) -> dict:
        """Build the source-specific ``DataSourceParameters`` for a KB connector.

        Each KB connector type carries a different ``*Parameters`` member
        (verified against the live quicksight model, botocore 1.43.114):

        * SharePoint: ``SharePointParameters`` - ``SharePointDomain`` (required),
          ``TenantId``, ``ClientId``, ``AuthType`` (``TWO_LEGGED_OAUTH``).
        * OneDrive: ``OneDriveParameters`` - ``TenantId``, ``ClientId``,
          ``AuthType``.
        * Google Drive: ``GoogleDriveParameters`` - ``AuthType``
          (``SERVICE_ACCOUNT``).
        * Confluence: ``ConfluenceParameters`` - ``ConfluenceUrl`` (required).

        Values are read from the provider-agnostic ``config`` the domain layer
        assembled from the admin's inputs. ``None``/absent values are omitted so
        only required members (enforced earlier by the service's config-field
        check) are guaranteed present.
        """

        def _compact(d: dict) -> dict:
            return {k: v for k, v in d.items() if v is not None and v != ""}

        if key == "sharepoint":
            # SharePointDomain must be the BARE domain (no scheme) or the service
            # prepends https:// and fails Entra auth with a doubled-scheme host.
            # The value may arrive as domain_url (from the secret) or
            # sharepoint_domain (admin config); strip any scheme/trailing slash.
            raw_domain = config.get("sharepoint_domain") or config.get("domain_url")
            domain = _bare_domain(raw_domain)
            return {
                "SharePointParameters": _compact(
                    {
                        "SharePointDomain": domain,
                        "TenantId": config.get("tenant_id"),
                        "ClientId": config.get("client_id"),
                        "AuthType": config.get("auth_mode") or "TWO_LEGGED_OAUTH",
                    }
                )
            }
        if key == "onedrive":
            return {
                "OneDriveParameters": _compact(
                    {
                        "TenantId": config.get("tenant_id"),
                        "ClientId": config.get("client_id"),
                        "AuthType": config.get("auth_mode") or "TWO_LEGGED_OAUTH",
                    }
                )
            }
        if key == "google_drive":
            return {
                "GoogleDriveParameters": _compact(
                    {"AuthType": config.get("auth_mode") or "SERVICE_ACCOUNT"}
                )
            }
        if key == "confluence":
            return {
                "ConfluenceParameters": _compact(
                    {"ConfluenceUrl": config.get("confluence_url")}
                )
            }
        # Unknown KB connector: no modeled parameters block. Return empty so the
        # create fails fast with an actionable API/validation error rather than
        # a guessed shape.
        return {}

    @staticmethod
    def _data_source_credentials(key: str, request: dict) -> dict | None:
        """Build the ``Credentials`` for ``CreateDataSource``, if any are given.

        The credential channel depends on the connector:

        * **SharePoint (certificate-based admin-managed)** — authenticates with
          ``Credentials.SharePointClientCredentials`` whose two fields are sent
          **INLINE** (not as a SecretArn): ``CertThumbprint`` and ``KmsKeyArn``.
          The domain layer resolves these from a Secrets Manager secret (whose
          body also carries ``tenant_id`` / ``client_id`` / ``domain_url`` for
          ``SharePointParameters``) into ``request["auth_params"]`` as
          ``certificate_thumbprint`` / ``kms_key_arn``. (Verified end-to-end and
          against the live quicksight model, botocore 1.43.117+:
          ``SharePointClientCredentials`` requires exactly ``CertThumbprint`` and
          ``KmsKeyArn``; it is absent from earlier botocore.)

        * **OneDrive (certificate-based admin-managed)** — the service
          credentials live in a Secrets Manager secret that Quick's own
          ``SecretsManagerOauthCredentials`` deserializer reads; wired with
          ``Credentials.SecretArn``. The domain layer enforces that a
          ``secret_id`` is present for it before reaching here.

        * **Google Drive (admin-managed, service account / domain-wide
          delegation)** — authenticates with ``Credentials.ServiceAccountCredentials``
          whose three fields are sent **INLINE** (not as a SecretArn): the
          service-account ``ClientEmail``, its ``ServiceAccountPrivateKey``, and
          the ``AdminAccountEmail`` the account impersonates. The domain layer
          resolves these from a Secrets Manager secret (the downloaded Google
          service-account JSON plus an added ``admin_account_email``) into
          ``request["auth_params"]`` as ``client_email`` / ``private_key`` /
          ``admin_account_email``. (Verified against the live quicksight model,
          botocore 1.43.117: ``ServiceAccountCredentials`` requires exactly these
          three members; it is absent from earlier botocore.)

        * **Confluence / other** — may use a Secrets Manager secret (preferred)
          or inline ``OAuthClientCredentials`` (``client_id`` / ``client_secret``
          2LO) when the admin supplies them directly.

        Returns ``None`` when no credential is present, so ``Credentials`` is
        omitted rather than sent empty.
        """
        # SharePoint admin-managed: inline SharePointClientCredentials resolved
        # from the secret (NOT a SecretArn). The KMS key ARN + cert thumbprint
        # are sent inline; tenant/client/domain go in SharePointParameters.
        if key == "sharepoint":
            auth_params = dict(request.get("auth_params") or {})
            thumbprint = auth_params.get("certificate_thumbprint") or auth_params.get(
                "cert_thumbprint"
            )
            kms_key_arn = auth_params.get("kms_key_arn")
            if thumbprint and kms_key_arn:
                return {
                    "SharePointClientCredentials": {
                        "CertThumbprint": thumbprint,
                        "KmsKeyArn": kms_key_arn,
                    }
                }
            # Missing a required field: the domain layer validates the secret
            # schema before reaching here, so this is defensive.
            return None

        # Google Drive admin-managed: inline ServiceAccountCredentials resolved
        # from the secret (NOT a SecretArn). Checked before the SecretArn branch
        # because the secret here carries the SA JSON, not a Quick-readable
        # OAuth/credential secret, and must be expanded inline.
        if key == "google_drive":
            auth_params = dict(request.get("auth_params") or {})
            client_email = auth_params.get("client_email")
            private_key = auth_params.get("private_key")
            admin_email = auth_params.get("admin_account_email")
            if client_email and private_key and admin_email:
                return {
                    "ServiceAccountCredentials": {
                        "ClientEmail": client_email,
                        "ServiceAccountPrivateKey": private_key,
                        "AdminAccountEmail": admin_email,
                    }
                }
            # Missing a required field: the domain layer validates the secret
            # schema before reaching here, so this is defensive. Omit Credentials
            # rather than send an incomplete ServiceAccountCredentials.
            return None

        secret_arn = request.get("credentials_secret_arn")
        if secret_arn:
            return {"SecretArn": secret_arn}
        # Certificate-based connectors have no valid inline credential form: the
        # KMS key ARN and thumbprint can only travel in the secret, so never
        # fall back to OAuthClientCredentials for them.
        if key in ("sharepoint", "onedrive"):
            return None
        auth_params = dict(request.get("auth_params") or {})
        client_id = auth_params.get("client_id")
        client_secret = auth_params.get("client_secret")
        if client_id or client_secret:
            oauth = {
                k: v
                for k, v in (
                    ("ClientId", client_id),
                    ("ClientSecret", client_secret),
                )
                if v is not None and v != ""
            }
            return {"OAuthClientCredentials": oauth}
        return None

    def _build_data_source_params(self, key: str, request: dict) -> dict:
        """Assemble ``CreateDataSource`` request parameters for a KB connector."""
        config = dict(request.get("config") or {})
        # SharePoint/OneDrive carry TenantId and ClientId in DataSourceParameters,
        # but for these certificate-based connectors those values live in the
        # Secrets Manager secret (resolved into request["auth_params"]), not in
        # the admin-supplied config. Merge the resolved auth params underneath
        # config so the source parameters can read tenant_id/client_id from the
        # secret; explicit config values still win. Quick rejects the create
        # with InvalidParameterValueException when TenantId/ClientId are absent.
        source_values = dict(request.get("auth_params") or {})
        source_values.update(config)
        params: dict[str, Any] = {
            "AwsAccountId": self._get_aws_account_id(),
            "DataSourceId": _resource_id(key, request.get("id_prefix")),
            "Name": request.get("name") or key,
            "Type": _DATA_SOURCE_TYPE_BY_KEY.get(key, key.upper()),
            "DataSourceParameters": self._data_source_parameters(key, source_values),
        }
        credentials = self._data_source_credentials(key, request)
        if credentials:
            params["Credentials"] = credentials
        # NOTE: no Permissions here. A data source does not take KB OWNER/VIEWER
        # roles, and the user-facing resource is the knowledge base - the owner
        # grant is attached to CreateKnowledgeBase (see _build_knowledge_base_params).
        return params

    # -- knowledge-base assembly (CreateKnowledgeBase) -------------------

    @staticmethod
    def _kb_template(key: str, config: dict, acl_enabled: bool) -> dict:
        """Build ``templateConfiguration.template`` for a KB connector.

        The ``template`` is a free-form JSON *document* in the service model
        (botocore does not validate its interior), so this builder is the sole
        guarantor of the connector-specific shape. All connectors share a
        ``type`` discriminator and a ``dataEntityConfiguration``; the ACL switch
        is the template-level ``accessControlConfiguration.crawlAcl`` flag
        (confirmed from a real SharePoint-with-ACL knowledge base). ``siteUrls``
        is populated from the admin-supplied ``sites`` config for SharePoint /
        OneDrive; Confluence uses ``spaceUrls``.
        """
        template: dict[str, Any] = {
            "type": _KB_TEMPLATE_TYPE_BY_KEY.get(key, key.upper())
        }
        sites = config.get("sites")
        if key == "sharepoint":
            # Validated end-to-end against the live service (and matched to a
            # console-created SharePoint KB). The site list must appear BOTH in
            # dataEntityConfiguration.siteUrls AND in
            # filterConfiguration.inclusionItemPaths, or CreateKnowledgeBase
            # fails with a detail-less "Data source validation failed".
            # crawlIdentities MUST track the ACL switch: true when ACLs are on,
            # false when off (a mismatch also fails validation). There is NO
            # connectionConfiguration in the SharePoint template (unlike Google
            # Drive). deletionProtection.enableDeletionProtection is a boolean.
            site_list = sites if sites is not None else []
            template["crawlIdentities"] = acl_enabled
            template["selectedFiles"] = []
            template["dataEntityConfiguration"] = {
                "siteUrls": site_list,
                "crawlPages": True,
                "crawlFiles": True,
            }
            template["deletionProtectionConfiguration"] = {
                "enableDeletionProtection": False,
                "deletionProtectionThreshold": "15",
            }
            template["accessControlConfiguration"] = {"crawlAcl": acl_enabled}
            template["filterConfiguration"] = {
                "exclusionOneNoteSectionNamePatterns": [],
                "exclusionItemPaths": [],
                "exclusionFilePath": [],
                "inclusionOneNotePageNamePatterns": [],
                "exclusionOneNotePageNamePatterns": [],
                "exclusionFileNamePatterns": [],
                "maxFileSizeInMegaBytes": "500",
                "inclusionFileNamePatterns": [],
                "inclusionOneNoteSectionNamePatterns": [],
                "inclusionFilePath": [],
                "inclusionItemPaths": site_list,
            }
            return template
        if key == "onedrive":
            data_entity: dict[str, Any] = {"crawlFiles": True, "crawlPages": True}
            if sites is not None:
                data_entity["siteUrls"] = sites
            template["dataEntityConfiguration"] = data_entity
        elif key == "google_drive":
            # Google Drive (service-account / domain-wide delegation) requires
            # the template to declare the connection auth type, or
            # CreateKnowledgeBase fails with "Required:
            # templateConfiguration.template.connectionConfiguration.authType=
            # 'SERVICE_ACCOUNT'" (verified against the live service). The crawl*
            # flags select My Drive, shared drives, and files shared with the
            # impersonated users.
            template["connectionConfiguration"] = {"authType": "SERVICE_ACCOUNT"}
            template["dataEntityConfiguration"] = {
                "crawlMyDrive": True,
                "crawlSharedDrives": True,
                "crawlSharedWithMe": True,
            }
        elif key == "confluence":
            data_entity = {}
            if sites is not None:
                data_entity["spaceUrls"] = sites
            template["dataEntityConfiguration"] = data_entity
        if acl_enabled:
            # Template-level half of the ACL switch; the top-level
            # AccessControlConfiguration.isACLEnabled is the other half.
            template["accessControlConfiguration"] = {"crawlAcl": True}
        return template

    def _build_knowledge_base_params(self, key: str, request: dict) -> dict:
        """Assemble ``CreateKnowledgeBase`` request parameters.

        Second half of the two-step admin-managed create. ``CreateKnowledgeBase``
        has NO ``AuthenticationConfig`` (that lives on the data source); it
        requires ``AwsAccountId``, ``KnowledgeBaseId``, ``Name``, a required
        ``DataSourceArn`` (the ARN returned by :meth:`create_data_source`, passed
        in ``request["data_source_arn"]``), and a ``KnowledgeBaseConfiguration``
        of the form ``{"templateConfiguration": {"template": {...}}}`` (verified
        against the live service model and a real SharePoint knowledge base).

        Document-level ACLs are intrinsic to the auth type: admin-managed
        knowledge bases always enforce them, so the template carries
        ``accessControlConfiguration.crawlAcl=true`` AND the top-level
        ``AccessControlConfiguration.isACLEnabled=true`` (both are required to
        turn ACLs on). User-managed knowledge bases are not created through this
        backend (console-only), so ACLs default off here.
        """
        config = dict(request.get("config") or {})
        acl_enabled = request.get("auth_type") == AuthType.KB_ADMIN_MANAGED.value
        params: dict[str, Any] = {
            "AwsAccountId": self._get_aws_account_id(),
            "KnowledgeBaseId": _resource_id(key, request.get("id_prefix")),
            "Name": request.get("name") or key,
            "KnowledgeBaseConfiguration": {
                "templateConfiguration": {
                    "template": self._kb_template(key, config, acl_enabled)
                }
            },
        }
        data_source_arn = request.get("data_source_arn") or config.get(
            "data_source_arn"
        )
        if data_source_arn:
            params["DataSourceArn"] = data_source_arn
        params["AccessControlConfiguration"] = {"isACLEnabled": acl_enabled}
        description = request.get("description")
        if description:
            params["Description"] = description
        # Grant the named owner the OWNER role on the knowledge base so a human
        # Quick user (not just the server's execution role) owns and can manage
        # it. KB Actions must match the fixed OWNER/VIEWER roles.
        permissions = self._build_kb_owner_permissions(request)
        if permissions:
            params["Permissions"] = permissions
        return params

    # -- response translation -------------------------------------------

    @staticmethod
    def _to_knowledge_base_detail(key: str, response: dict) -> KnowledgeBaseDetail:
        """Translate a ``CreateKnowledgeBase``/describe response into a detail."""
        response = response or {}
        return KnowledgeBaseDetail(
            key=key,
            knowledge_base_id=response.get("KnowledgeBaseId") or "",
            arn=response.get("KnowledgeBaseArn") or response.get("Arn") or "",
            creation_status=response.get("CreationStatus") or "",
            raw=response,
        )

    @staticmethod
    def _to_data_source_detail(key: str, response: dict) -> DataSourceDetail:
        """Translate a ``CreateDataSource``/``DeleteDataSource`` response.

        Field extraction is best-effort via ``.get`` so the client tolerates
        schema variation; ``arn`` is the value the caller threads into the
        subsequent ``CreateKnowledgeBase`` call.
        """
        response = response or {}
        return DataSourceDetail(
            key=key,
            data_source_id=response.get("DataSourceId") or "",
            arn=response.get("Arn") or response.get("DataSourceArn") or "",
            creation_status=response.get("CreationStatus")
            or response.get("Status")
            or "",
            raw=response,
        )

    @staticmethod
    def _to_connector_detail(key: str, response: dict) -> ConnectorDetail:
        """Translate a boto3 response dict into a :class:`ConnectorDetail`.

        Field extraction is best-effort via ``.get`` so the client tolerates
        schema variation; the full response is preserved under ``raw``.
        """
        response = response or {}
        detail = response.get("ActionConnector") or response.get("Connector") or {}
        resolved_key = detail.get("Key") or detail.get("Id") or key
        status = detail.get("Status") or detail.get("State") or "enabled"
        config = detail.get("Config") or detail.get("Configuration") or {}
        return ConnectorDetail(
            key=resolved_key,
            status=str(status).lower() if status else "enabled",
            config=config if isinstance(config, dict) else {},
            raw=response,
        )

    @classmethod
    def _to_connector_list(cls, response: dict) -> list[ConnectorDetail]:
        """Translate a list/search boto3 response into ``ConnectorDetail`` items."""
        response = response or {}
        items = (
            response.get("ActionConnectors")
            or response.get("Connectors")
            or response.get("Items")
            or []
        )
        connectors: list[ConnectorDetail] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("Key") or item.get("Id") or ""
            status = item.get("Status") or item.get("State") or "enabled"
            config = item.get("Config") or item.get("Configuration") or {}
            connectors.append(
                ConnectorDetail(
                    key=key,
                    status=str(status).lower() if status else "enabled",
                    config=config if isinstance(config, dict) else {},
                    raw=item,
                )
            )
        return connectors

    @staticmethod
    def _to_permission_detail(key: str, response: dict) -> PermissionDetail:
        """Translate a permissions boto3 response into a :class:`PermissionDetail`.

        Extracts ``granted`` and ``authorization_url`` when present, defaulting to
        granted when the API does not report an outstanding authorization.
        """
        response = response or {}
        detail = (
            response.get("Permissions")
            or response.get("Permission")
            or response.get("ActionConnectorPermissions")
            or response
        )
        if not isinstance(detail, dict):
            detail = {}

        authorization_url = (
            detail.get("AuthorizationUrl")
            or detail.get("AuthorizationURL")
            or response.get("AuthorizationUrl")
            or response.get("AuthorizationURL")
        )

        if "Granted" in detail:
            granted = bool(detail.get("Granted"))
        elif "IsGranted" in detail:
            granted = bool(detail.get("IsGranted"))
        else:
            # No explicit grant flag: an outstanding authorization URL implies a
            # grant is still required; otherwise treat as granted.
            granted = authorization_url is None

        return PermissionDetail(
            key=key,
            granted=granted,
            authorization_url=authorization_url,
            raw=response,
        )
