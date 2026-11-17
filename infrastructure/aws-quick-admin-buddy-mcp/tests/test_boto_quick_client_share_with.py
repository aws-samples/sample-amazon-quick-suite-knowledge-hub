"""Tests for the ``share_with`` -> ``Permissions`` wiring in
:class:`BotoQuickAdminClient`.

Covers:

* Bare user name -> expanded default-namespace ARN
  (``arn:aws:quicksight:<region>:<account>:user/default/<name>``), using the
  injected ``aws_account_id`` / ``region_name`` (no real STS/network call).
* A caller-supplied full ARN is passed through unchanged.
* No ``share_with`` -> no ``Permissions`` key at all (API default).
* The owner-equivalent Actions set is attached for both
  ``CreateActionConnector`` and ``CreateKnowledgeBase`` request building.
"""

from __future__ import annotations

import boto3
import pytest
from botocore.validate import validate_parameters

from quick_admin_buddy_mcp.quick.boto_client import (
    _ACTION_CONNECTOR_SHARE_ACTIONS,
    _KB_OWNER_ACTIONS,
    _KB_VIEWER_ACTIONS,
    BotoQuickAdminClient,
)

_REGION = "eu-west-1"
_ACCOUNT = "123456789012"


def _client() -> BotoQuickAdminClient:
    # client=object() is never invoked in these tests (no _invoke call), only
    # the request-building helpers; aws_account_id/region_name are injected so
    # no real STS call or client.meta lookup happens.
    return BotoQuickAdminClient(
        client=object(), region_name=_REGION, aws_account_id=_ACCOUNT
    )


def test_resolve_principal_arn_expands_bare_name_to_default_namespace() -> None:
    client = _client()
    arn = client._resolve_principal_arn("workshopuser", _ACCOUNT, _REGION)
    assert arn == f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/workshopuser"


def test_resolve_principal_arn_passes_through_full_arn_unchanged() -> None:
    client = _client()
    full_arn = "arn:aws:quicksight:us-east-1:999999999999:user/custom-ns/someone"
    assert client._resolve_principal_arn(full_arn, _ACCOUNT, _REGION) == full_arn


def test_build_permissions_returns_none_without_share_with() -> None:
    client = _client()
    assert client._build_permissions({}, _ACTION_CONNECTOR_SHARE_ACTIONS) is None
    assert (
        client._build_permissions({"share_with": None}, _ACTION_CONNECTOR_SHARE_ACTIONS)
        is None
    )
    assert (
        client._build_permissions({"share_with": ""}, _ACTION_CONNECTOR_SHARE_ACTIONS)
        is None
    )


def test_build_permissions_expands_bare_name_with_owner_equivalent_actions() -> None:
    client = _client()
    permissions = client._build_permissions(
        {"share_with": "workshopuser"}, _ACTION_CONNECTOR_SHARE_ACTIONS
    )
    assert permissions == [
        {
            "Principal": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/workshopuser",
            "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS),
        }
    ]


def test_build_action_connector_params_includes_permissions_when_share_with_given() -> (
    None
):
    client = _client()
    request = {
        "name": "outlook",
        "auth_type": "custom_oauth_app",
        "auth_params": {
            "client_id": "cid",
            "client_secret": "csecret",
            "domain_url": "https://graph.microsoft.com",
            "authorization_url": "https://login.microsoftonline.com/authorize",
            "token_url": "https://login.microsoftonline.com/token",
            "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
        },
        "share_with": "workshopuser",
    }
    params = client._build_action_connector_params("outlook", request)
    assert params["Permissions"] == [
        {
            "Principal": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/workshopuser",
            "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS),
        }
    ]


def test_build_action_connector_params_omits_permissions_without_share_with() -> None:
    client = _client()
    request = {
        "name": "outlook",
        "auth_type": "custom_oauth_app",
        "auth_params": {
            "client_id": "cid",
            "client_secret": "csecret",
            "domain_url": "https://graph.microsoft.com",
            "authorization_url": "https://login.microsoftonline.com/authorize",
            "token_url": "https://login.microsoftonline.com/token",
            "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
        },
    }
    params = client._build_action_connector_params("outlook", request)
    assert "Permissions" not in params


def test_build_knowledge_base_params_grants_owner_role_to_owner() -> None:
    client = _client()
    request = {
        "name": "sharepoint",
        "config": {"sites": ["https://contoso.sharepoint.com/sites/eng"]},
        "owner": "arn:aws:quicksight:us-east-1:999999999999:user/default/someone",
    }
    params = client._build_knowledge_base_params("sharepoint", request)
    # KB create grants the fixed OWNER role to the named owner.
    assert params["Permissions"] == [
        {
            "Principal": "arn:aws:quicksight:us-east-1:999999999999:user/default/someone",
            "Actions": list(_KB_OWNER_ACTIONS),
        }
    ]


def test_build_knowledge_base_params_omits_permissions_without_owner() -> None:
    client = _client()
    params = client._build_knowledge_base_params(
        "sharepoint",
        {"name": "sharepoint", "config": {"sites": ["https://x/sites/a"]}},
    )
    assert "Permissions" not in params


# ---------------------------------------------------------------------------
# Sharing an EXISTING resource via Update*Permissions
# (share_action_connector / share_knowledge_base). These capture the params the
# client actually sends and validate them against the live Update*Permissions
# input shapes, which require AwsAccountId + the resource id, and a
# GrantPermissions list of {Principal, Actions}.
# ---------------------------------------------------------------------------


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def update_action_connector_permissions(self, **kwargs):
        self.calls.append(("update_action_connector_permissions", kwargs))
        return {}

    def update_knowledge_base_permissions(self, **kwargs):
        self.calls.append(("update_knowledge_base_permissions", kwargs))
        return {}


@pytest.fixture(scope="module")
def _perm_models():
    client = boto3.Session().client("quicksight", region_name=_REGION)
    model = client.meta.service_model
    return {
        "update_action_connector_permissions": model.operation_model(
            "UpdateActionConnectorPermissions"
        ).input_shape,
        "update_knowledge_base_permissions": model.operation_model(
            "UpdateKnowledgeBasePermissions"
        ).input_shape,
    }


def test_share_action_connector_grants_owner_actions_to_bare_name(_perm_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    client.share_action_connector("sharepoint", "workshopuser")
    action, params = fake.calls[0]
    assert action == "update_action_connector_permissions"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["ActionConnectorId"] == "quickadminbuddy-sharepoint"
    assert params["GrantPermissions"] == [
        {
            "Principal": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/workshopuser",
            "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS),
        }
    ]
    validate_parameters(params, _perm_models["update_action_connector_permissions"])


def test_share_action_connector_passes_full_arn_and_custom_prefix(_perm_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    group_arn = "arn:aws:quicksight:us-east-1:999999999999:group/default/analysts"
    client.share_action_connector("outlook", group_arn, "my-company-")
    _, params = fake.calls[0]
    assert params["ActionConnectorId"] == "my-company-outlook"
    assert params["GrantPermissions"][0]["Principal"] == group_arn
    validate_parameters(params, _perm_models["update_action_connector_permissions"])


def test_share_knowledge_base_grants_viewer_role_to_bare_name(_perm_models):
    fake = _RecordingClient()
    client = BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )
    client.share_knowledge_base("sharepoint", "workshopuser")
    action, params = fake.calls[0]
    assert action == "update_knowledge_base_permissions"
    assert params["AwsAccountId"] == _ACCOUNT
    assert params["KnowledgeBaseId"] == "quickadminbuddy-sharepoint"
    # Sharing grants the VIEWER role only (describe) - not an owner-equivalent set.
    assert params["GrantPermissions"] == [
        {
            "Principal": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/workshopuser",
            "Actions": list(_KB_VIEWER_ACTIONS),
        }
    ]
    validate_parameters(params, _perm_models["update_knowledge_base_permissions"])
