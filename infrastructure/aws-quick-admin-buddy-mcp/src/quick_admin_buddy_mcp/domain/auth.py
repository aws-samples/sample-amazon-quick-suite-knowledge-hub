"""Authentication-parameter contracts for integration creation.

Both integration types — action connectors (``CreateActionConnector``) and
knowledge bases (``CreateKnowledgeBase``) — are created with one of two OAuth
authentication methods (:class:`~quick_admin_buddy_mcp.domain.types.AuthType`).
Each method requires a fixed set of parameters, named in ``snake_case``, derived
from the AWS Quick "Authentication methods" documentation:
https://docs.aws.amazon.com/quick/latest/userguide/quick-action-auth.html

The agent can supply these parameters two ways (see
:class:`~quick_admin_buddy_mcp.domain.service.OnboardingService`):

1. **Inline** — the agent collects every required value from the admin and passes
   them as ``auth_params``.
2. **Via Secrets Manager** — the admin stores a JSON secret whose keys are the
   ``snake_case`` parameter names below, and the agent passes the secret id /
   ARN. The server resolves and parses it. The JSON contract is exactly
   ``{"<param_name>": "<value>", ...}``; :func:`secret_contract_example` renders
   the template for a given auth type.

This module is pure and dependency-free: it defines the required-parameter sets,
the secret contract, and validation helpers. It never logs or returns secret
values.
"""

from __future__ import annotations

from quick_admin_buddy_mcp.domain.types import AuthType, IntegrationType

# ---------------------------------------------------------------------------
# Required parameters per auth type (snake_case).
# ---------------------------------------------------------------------------
#
# ACTION CONNECTORS (from the AWS Quick "Authentication methods" docs):
#   Custom OAuth app (3LO): Client ID, Client Secret, Domain URL, Authorization
#     URL, Token URL, Redirect URL.
#   Service-to-Service OAuth (client credentials): Client ID, Client Secret,
#     Domain URL, Token URL. (No Authorization/Redirect URL — no interactive
#     user sign-in.)
#
# KNOWLEDGE BASES use a DIFFERENT auth model (NOT the OAuth app methods above):
#   User-managed setup: the admin signs in to the source directly; no
#     credentials are passed to the backend, so no required params.
#   Admin-managed setup (service credentials): a source-specific service
#     identity (Microsoft Entra app + certificate/KMS key, or a Google service
#     account with domain-wide delegation). The concrete fields differ per
#     source and the AWS Quick console drives their creation, so the backend
#     does not demand a universal param set; the credential reference, when
#     supplied, is passed opaquely as `service_credentials_ref`.
REQUIRED_PARAMS: dict[AuthType, tuple[str, ...]] = {
    AuthType.CUSTOM_OAUTH_APP: (
        "client_id",
        "client_secret",
        "domain_url",
        "authorization_url",
        "token_url",
        "redirect_url",
    ),
    AuthType.SERVICE_TO_SERVICE_OAUTH: (
        "client_id",
        "client_secret",
        "domain_url",
        "token_url",
    ),
    # API key (2LO): the API token and the service base URL are required; an
    # associated email is optional (for Atlassian Confluence Cloud the email is
    # the Atlassian account email and the api_key is the API token).
    AuthType.API_KEY: (
        "api_key",
        "domain_url",
    ),
    # Knowledge-base auth types take no mandatory credential params at the
    # backend boundary (see note above).
    AuthType.KB_USER_MANAGED: (),
    AuthType.KB_ADMIN_MANAGED: (),
}

# Which auth types each integration type accepts. Action connectors use the two
# OAuth app methods plus API key; knowledge bases use the two KB setup methods.
# An auth type valid for one integration type is rejected for the other.
ALLOWED_AUTH_TYPES: dict[IntegrationType, tuple[AuthType, ...]] = {
    IntegrationType.ACTION_CONNECTOR: (
        AuthType.CUSTOM_OAUTH_APP,
        AuthType.SERVICE_TO_SERVICE_OAUTH,
        AuthType.API_KEY,
    ),
    IntegrationType.KNOWLEDGE_BASE: (
        AuthType.KB_USER_MANAGED,
        AuthType.KB_ADMIN_MANAGED,
    ),
}

# Parameter names whose values are sensitive and must never be echoed back in a
# Tool_Result. These are handed to the sanitize boundary as secrets to redact.
SECRET_PARAM_NAMES: frozenset[str] = frozenset(
    {"client_secret", "service_credentials_ref", "api_key"}
)

# The stable list of ALL valid auth-type string values, for generic messages.
VALID_AUTH_TYPES: tuple[str, ...] = tuple(a.value for a in AuthType)


def valid_auth_types_for(integration_type: IntegrationType) -> tuple[str, ...]:
    """Return the auth-type string values accepted for ``integration_type``."""
    return tuple(a.value for a in ALLOWED_AUTH_TYPES[integration_type])


def is_auth_type_allowed(
    auth_type: AuthType, integration_type: IntegrationType
) -> bool:
    """Return whether ``auth_type`` is valid for ``integration_type``."""
    return auth_type in ALLOWED_AUTH_TYPES[integration_type]


def parse_auth_type(value: str | None) -> AuthType | None:
    """Return the :class:`AuthType` for ``value``, or ``None`` if invalid/absent."""
    if value is None:
        return None
    try:
        return AuthType(value)
    except ValueError:
        return None


def required_params(auth_type: AuthType) -> tuple[str, ...]:
    """Return the required ``snake_case`` parameter names for ``auth_type``."""
    return REQUIRED_PARAMS[auth_type]


def missing_params(auth_type: AuthType, provided: dict) -> list[str]:
    """Return the required parameters for ``auth_type`` absent from ``provided``.

    A parameter is "missing" when it is absent, ``None``, or an empty/blank
    string. Order follows :data:`REQUIRED_PARAMS` for deterministic messages.
    """
    provided = provided or {}
    missing: list[str] = []
    for name in REQUIRED_PARAMS[auth_type]:
        value = provided.get(name)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(name)
    return missing


def secret_values(provided: dict) -> tuple[str, ...]:
    """Return the sensitive values in ``provided`` (for redaction).

    Only the parameters named in :data:`SECRET_PARAM_NAMES` with non-empty
    string values are returned; these are handed to the sanitize boundary so a
    resolved credential can never surface in a result.
    """
    provided = provided or {}
    out: list[str] = []
    for name in SECRET_PARAM_NAMES:
        value = provided.get(name)
        if isinstance(value, str) and value:
            out.append(value)
    return tuple(out)


def secret_contract_example(auth_type: AuthType) -> dict:
    """Return the Secrets Manager JSON contract template for ``auth_type``.

    The returned mapping uses the exact ``snake_case`` keys the server expects in
    the secret, with placeholder values. The ``auth_type`` key is included for
    the admin's convenience; the server validates against the auth type passed to
    the enable operation and ignores a mismatched/absent ``auth_type`` in the
    secret body.
    """
    contract = {"auth_type": auth_type.value}
    for name in REQUIRED_PARAMS[auth_type]:
        contract[name] = f"<{name}>"
    return contract
