"""Tests for Gmail action-connector support (Type=GMAIL).

Covers:
* the catalog key ``gmail`` maps to the ``GMAIL`` action-connector ``Type`` and
  :meth:`_build_action_connector_params` emits it;
* the Gmail custom-OAuth auth parameters (client_id/secret, the two URLs, the
  redirect URL, and ``domain_url`` as ``BaseEndpoint``) flow from the resolved
  ``auth_params`` into the request exactly like any other ``custom_oauth_app``
  connector — so a Secrets Manager secret populates them unchanged;
* ``GMAIL`` serializes past boto3's client-side validation. No bundled botocore
  lists ``GMAIL`` in the ``Type`` enum, but botocore does NOT enforce this enum
  client-side, so no SDK workaround is needed — this test guards that
  assumption: if a future botocore started enforcing it, this test would fail
  and flag that a model fix is required.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.exceptions import ParamValidationError

from quick_admin_buddy_mcp.quick.boto_client import (
    _ACTION_TYPE_BY_KEY,
    BotoQuickAdminClient,
)

_REGION = "eu-west-1"
_ACCOUNT = "123456789012"

_GMAIL_AUTH = {
    "client_id": "cid",
    "client_secret": "csecret",
    "domain_url": "https://gmail.googleapis.com",
    "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url": "https://oauth2.googleapis.com/token",
    "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
}


def _client() -> BotoQuickAdminClient:
    return BotoQuickAdminClient(
        client=object(), region_name=_REGION, aws_account_id=_ACCOUNT
    )


def test_gmail_key_maps_to_gmail_type():
    assert _ACTION_TYPE_BY_KEY["gmail"] == "GMAIL"


# The full Google action-connector key -> native AWS Quick Type mapping.
# Pattern: display name upper-cased with underscores (per the AWS Quick
# "Supported connector types" reference). MODEL_CONTEXT_PROTOCOL is deliberately
# NOT handled here (it cannot be created through the public API).
_GOOGLE_TYPE_BY_KEY = {
    "gmail": "GMAIL",
    "google_chat": "GOOGLE_CHAT",
    "google_drive": "GOOGLE_DRIVE",
    "google_calendar": "GOOGLE_CALENDAR",
    "google_docs": "GOOGLE_DOCS",
    "google_meet": "GOOGLE_MEET",
    "google_sheets": "GOOGLE_SHEETS",
    "google_slides": "GOOGLE_SLIDES",
}


@pytest.mark.parametrize("key,expected_type", sorted(_GOOGLE_TYPE_BY_KEY.items()))
def test_google_keys_map_to_native_types(key, expected_type):
    assert _ACTION_TYPE_BY_KEY[key] == expected_type


@pytest.mark.parametrize("key,expected_type", sorted(_GOOGLE_TYPE_BY_KEY.items()))
def test_build_action_connector_params_emits_google_native_type(key, expected_type):
    client = _client()
    request = {
        "name": key,
        "auth_type": "custom_oauth_app",
        "auth_params": _GMAIL_AUTH,  # shape is identical across Google custom OAuth
    }
    params = client._build_action_connector_params(key, request)
    assert params["Type"] == expected_type
    assert params["ActionConnectorId"] == f"quickadminbuddy-{key}"


def test_build_action_connector_params_emits_gmail_type_and_auth_from_params():
    client = _client()
    request = {
        "name": "Gmail",
        "auth_type": "custom_oauth_app",
        "auth_params": _GMAIL_AUTH,
    }
    params = client._build_action_connector_params("gmail", request)
    assert params["Type"] == "GMAIL"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["ActionConnectorId"] == "quickadminbuddy-gmail"
    grant = params["AuthenticationConfig"]["AuthenticationMetadata"][
        "AuthorizationCodeGrantMetadata"
    ]
    # BaseEndpoint comes from auth_params["domain_url"] (resolved from the secret).
    assert grant["BaseEndpoint"] == "https://gmail.googleapis.com"
    assert grant["RedirectUrl"] == _GMAIL_AUTH["redirect_url"]
    assert grant["AuthorizationCodeGrantCredentialsSource"] == "PLAIN_CREDENTIALS"
    details = grant["AuthorizationCodeGrantCredentialsDetails"][
        "AuthorizationCodeGrantDetails"
    ]
    assert details["ClientId"] == "cid"
    assert details["ClientSecret"] == "csecret"
    assert details["TokenEndpoint"] == _GMAIL_AUTH["token_url"]
    assert details["AuthorizationEndpoint"] == _GMAIL_AUTH["authorization_url"]


@pytest.fixture(scope="module")
def _qs_client():
    return boto3.Session().client("quicksight", region_name=_REGION)


def test_gmail_type_is_not_rejected_by_client_side_validation(_qs_client):
    """Serialize a Type=GMAIL CreateActionConnector request through botocore's
    request serializer (no network) and assert it does NOT raise
    ParamValidationError. This guards the assumption that botocore does not
    enforce the Type enum client-side; a Gmail connector therefore needs no SDK
    workaround. If a future botocore version starts enforcing the enum, this
    fails and signals that a model fix is required.
    """
    op_model = _qs_client.meta.service_model.operation_model("CreateActionConnector")
    serializer = _qs_client._serializer
    params = {
        "AwsAccountId": _ACCOUNT,
        "ActionConnectorId": "quickadminbuddy-gmail",
        "Name": "Gmail",
        "Type": "GMAIL",
        "AuthenticationConfig": {
            "AuthenticationType": "OAUTH2_AUTHORIZATION_CODE",
            "AuthenticationMetadata": {
                "AuthorizationCodeGrantMetadata": {
                    "BaseEndpoint": "https://gmail.googleapis.com",
                    "RedirectUrl": _GMAIL_AUTH["redirect_url"],
                    "AuthorizationCodeGrantCredentialsSource": "PLAIN_CREDENTIALS",
                    "AuthorizationCodeGrantCredentialsDetails": {
                        "AuthorizationCodeGrantDetails": {
                            "ClientId": "c",
                            "ClientSecret": "s",
                            "TokenEndpoint": _GMAIL_AUTH["token_url"],
                            "AuthorizationEndpoint": _GMAIL_AUTH["authorization_url"],
                        }
                    },
                }
            },
        },
    }
    # serialize_to_request runs client-side parameter validation; it must not
    # raise for Type=GMAIL.
    try:
        serializer.serialize_to_request(params, op_model)
    except ParamValidationError as exc:  # pragma: no cover - would be the bug
        pytest.fail(
            "botocore now rejects Type=GMAIL client-side; a model/enum fix is "
            f"required: {exc}"
        )
