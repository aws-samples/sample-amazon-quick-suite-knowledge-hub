"""Tests for Atlassian Jira Cloud support (action connector only).

Jira Cloud is a distinct action connector from Confluence (`Type=JIRA_CLOUD`
vs `ATLASSIAN_CONFLUENCE`) and is action-only (no knowledge base). In this
skill the catalog key `atlassian` maps to Jira Cloud.

It supports the same two auth flows as Confluence's action connector:
* **2LO (`api_key`)** -> `AuthenticationType=API_KEY` / `ApiKeyConnectionMetadata`.
* **3LO (`custom_oauth_app`)** -> `OAUTH2_AUTHORIZATION_CODE`.

These validate the request parameters the client builds against botocore's own
parameter validation of the live `quicksight` service model.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.validate import validate_parameters

from quick_admin_buddy_mcp.domain import catalog as catalog_mod
from quick_admin_buddy_mcp.quick.boto_client import (
    _ACTION_TYPE_BY_KEY,
    BotoQuickAdminClient,
)

_REGION = "eu-west-1"
_ACCOUNT = "123456789012"

# The catalog key for the Jira Cloud action connector.
_JIRA_KEY = "atlassian"


def _client() -> BotoQuickAdminClient:
    return BotoQuickAdminClient(
        client=object(), region_name=_REGION, aws_account_id=_ACCOUNT
    )


@pytest.fixture(scope="module")
def _create_ac_shape():
    c = boto3.Session().client("quicksight", region_name=_REGION)
    return c.meta.service_model.operation_model("CreateActionConnector").input_shape


# ---------------------------------------------------------------------------
# Catalog: Jira is a distinct, action-only connector keyed `atlassian`
# ---------------------------------------------------------------------------


def test_jira_key_maps_to_jira_cloud_type():
    assert _ACTION_TYPE_BY_KEY[_JIRA_KEY] == "JIRA_CLOUD"
    # Distinct from Confluence.
    assert _ACTION_TYPE_BY_KEY["confluence"] == "ATLASSIAN_CONFLUENCE"


def test_jira_is_action_only_in_catalog():
    cat = catalog_mod.ConnectorCatalog()
    assert cat.supports_action(_JIRA_KEY)
    assert not cat.supports_kb(_JIRA_KEY)


# ---------------------------------------------------------------------------
# Jira ACTION connector — 2LO (api_key) and 3LO (custom_oauth_app)
# ---------------------------------------------------------------------------


def test_jira_api_key_auth_config(_create_ac_shape):
    client = _client()
    request = {
        "name": "Jira",
        "auth_type": "api_key",
        "auth_params": {
            "api_key": "ATATT-the-api-token",
            "domain_url": "https://api.atlassian.com/ex/jira/abc-123",
            "email": "admin@contoso.com",
        },
    }
    params = client._build_action_connector_params(_JIRA_KEY, request)
    assert params["Type"] == "JIRA_CLOUD"
    cfg = params["AuthenticationConfig"]
    assert cfg["AuthenticationType"] == "API_KEY"
    meta = cfg["AuthenticationMetadata"]["ApiKeyConnectionMetadata"]
    assert meta["BaseEndpoint"] == "https://api.atlassian.com/ex/jira/abc-123"
    assert meta["ApiKey"] == "ATATT-the-api-token"
    assert meta["Email"] == "admin@contoso.com"
    validate_parameters(params, _create_ac_shape)


def test_jira_api_key_omits_email_when_absent(_create_ac_shape):
    client = _client()
    request = {
        "name": "Jira",
        "auth_type": "api_key",
        "auth_params": {
            "api_key": "tok",
            "domain_url": "https://api.atlassian.com/ex/jira/abc-123",
        },
    }
    params = client._build_action_connector_params(_JIRA_KEY, request)
    meta = params["AuthenticationConfig"]["AuthenticationMetadata"][
        "ApiKeyConnectionMetadata"
    ]
    assert "Email" not in meta
    validate_parameters(params, _create_ac_shape)


def test_jira_3lo_uses_authorization_code(_create_ac_shape):
    client = _client()
    request = {
        "name": "Jira",
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
    params = client._build_action_connector_params(_JIRA_KEY, request)
    assert params["Type"] == "JIRA_CLOUD"
    assert params["AuthenticationConfig"]["AuthenticationType"] == (
        "OAUTH2_AUTHORIZATION_CODE"
    )
    validate_parameters(params, _create_ac_shape)
