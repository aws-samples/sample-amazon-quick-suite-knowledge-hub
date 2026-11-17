"""Tests for Atlassian Confluence Cloud support.

Confluence action connectors support two auth flows:
* **2LO (`api_key`)** — an API token (and optional Atlassian account email),
  mapped to `AuthenticationType=API_KEY` / `ApiKeyConnectionMetadata`.
* **3LO (`custom_oauth_app`)** — the standard OAuth authorization-code flow
  (already covered by the generic custom_oauth_app path).

Confluence knowledge bases use the `CONFLUENCE` data-source type with
`ConfluenceParameters.ConfluenceUrl`.

These validate the request parameters the client builds against botocore's own
parameter validation of the live `quicksight` service model.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.validate import validate_parameters

from quick_admin_buddy_mcp.domain import auth as auth_contract
from quick_admin_buddy_mcp.domain.types import AuthType, IntegrationType
from quick_admin_buddy_mcp.quick.boto_client import (
    _ACTION_TYPE_BY_KEY,
    BotoQuickAdminClient,
)

_REGION = "eu-west-1"
_ACCOUNT = "123456789012"


def _client() -> BotoQuickAdminClient:
    return BotoQuickAdminClient(
        client=object(), region_name=_REGION, aws_account_id=_ACCOUNT
    )


@pytest.fixture(scope="module")
def _create_ac_shape():
    c = boto3.Session().client("quicksight", region_name=_REGION)
    return c.meta.service_model.operation_model("CreateActionConnector").input_shape


@pytest.fixture(scope="module")
def _create_ds_shape():
    c = boto3.Session().client("quicksight", region_name=_REGION)
    return c.meta.service_model.operation_model("CreateDataSource").input_shape


# ---------------------------------------------------------------------------
# Auth contract: api_key is a valid ACTION auth type (not a KB one)
# ---------------------------------------------------------------------------


def test_api_key_is_valid_action_auth_type_only():
    assert auth_contract.is_auth_type_allowed(
        AuthType.API_KEY, IntegrationType.ACTION_CONNECTOR
    )
    assert not auth_contract.is_auth_type_allowed(
        AuthType.API_KEY, IntegrationType.KNOWLEDGE_BASE
    )
    assert "api_key" in auth_contract.valid_auth_types_for(
        IntegrationType.ACTION_CONNECTOR
    )


def test_api_key_required_params_and_secret():
    assert auth_contract.required_params(AuthType.API_KEY) == ("api_key", "domain_url")
    # api_key must be redacted
    assert auth_contract.secret_values({"api_key": "tok-123"}) == ("tok-123",)
    # email is optional -> not in required params
    assert "email" not in auth_contract.required_params(AuthType.API_KEY)


# ---------------------------------------------------------------------------
# Confluence ACTION connector — 2LO (api_key) and 3LO (custom_oauth_app)
# ---------------------------------------------------------------------------


def test_confluence_key_maps_to_action_type():
    assert _ACTION_TYPE_BY_KEY["confluence"] == "ATLASSIAN_CONFLUENCE"


def test_confluence_api_key_auth_config(_create_ac_shape):
    client = _client()
    request = {
        "name": "Confluence",
        "auth_type": "api_key",
        "auth_params": {
            "api_key": "ATATT-the-api-token",
            "domain_url": "https://contoso.atlassian.net",
            "email": "admin@contoso.com",
        },
    }
    params = client._build_action_connector_params("confluence", request)
    assert params["Type"] == "ATLASSIAN_CONFLUENCE"
    cfg = params["AuthenticationConfig"]
    assert cfg["AuthenticationType"] == "API_KEY"
    meta = cfg["AuthenticationMetadata"]["ApiKeyConnectionMetadata"]
    assert meta["BaseEndpoint"] == "https://contoso.atlassian.net"
    assert meta["ApiKey"] == "ATATT-the-api-token"
    assert meta["Email"] == "admin@contoso.com"
    # Passes botocore validation against the live CreateActionConnector model.
    validate_parameters(params, _create_ac_shape)


def test_confluence_api_key_omits_email_when_absent(_create_ac_shape):
    client = _client()
    request = {
        "name": "Confluence",
        "auth_type": "api_key",
        "auth_params": {
            "api_key": "tok",
            "domain_url": "https://contoso.atlassian.net",
        },
    }
    params = client._build_action_connector_params("confluence", request)
    meta = params["AuthenticationConfig"]["AuthenticationMetadata"][
        "ApiKeyConnectionMetadata"
    ]
    assert "Email" not in meta
    validate_parameters(params, _create_ac_shape)


def test_confluence_3lo_uses_authorization_code(_create_ac_shape):
    client = _client()
    request = {
        "name": "Confluence",
        "auth_type": "custom_oauth_app",
        "auth_params": {
            "client_id": "cid",
            "client_secret": "sec",
            "domain_url": "https://api.atlassian.com",
            "authorization_url": "https://auth.atlassian.com/authorize",
            "token_url": "https://auth.atlassian.com/oauth/token",
            "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
        },
    }
    params = client._build_action_connector_params("confluence", request)
    assert params["Type"] == "ATLASSIAN_CONFLUENCE"
    assert params["AuthenticationConfig"]["AuthenticationType"] == (
        "OAUTH2_AUTHORIZATION_CODE"
    )
    validate_parameters(params, _create_ac_shape)


# ---------------------------------------------------------------------------
# Confluence KNOWLEDGE BASE — data source ConfluenceParameters
# ---------------------------------------------------------------------------


def test_confluence_kb_data_source_params(_create_ds_shape):
    client = _client()
    request = {
        "name": "confluence",
        "auth_type": "admin_managed",
        "config": {"confluence_url": "https://contoso.atlassian.net"},
        # OAuth client credentials are the account-connection credential the
        # backend can send inline. NOTE: the Atlassian *admin* credentials used
        # for document-level ACLs (API key / Organization ID / Directory ID) are
        # a console-wizard input, NOT modeled CreateDataSource parameters — the
        # modeled ConfluenceParameters carries only ConfluenceUrl (asserted
        # below).
        "auth_params": {"client_id": "cid", "client_secret": "sec"},
    }
    params = client._build_data_source_params("confluence", request)
    assert params["Type"] == "CONFLUENCE"
    # ConfluenceParameters is ConfluenceUrl only (verified vs the live model).
    assert params["DataSourceParameters"]["ConfluenceParameters"] == {
        "ConfluenceUrl": "https://contoso.atlassian.net"
    }
    # Inline OAuthClientCredentials for Confluence (not a certificate connector).
    assert params["Credentials"] == {
        "OAuthClientCredentials": {"ClientId": "cid", "ClientSecret": "sec"}
    }
    validate_parameters(params, _create_ds_shape)
