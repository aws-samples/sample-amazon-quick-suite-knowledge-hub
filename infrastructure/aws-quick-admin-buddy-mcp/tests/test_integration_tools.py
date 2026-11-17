"""End-to-end integration tests of the 12 MCP tools.

Each test drives the real tool layer (``server.OnboardingTools``) through the real
:class:`OnboardingService`, the real :class:`BotoQuickAdminClient`, the real
:class:`BotoSecretResolver`, and the real :class:`DynamoDbStateRepository`.
Only the AWS endpoints are replaced:

* DynamoDB and Secrets Manager run on ``moto``, which enforces their real API
  semantics (for example DynamoDB ``UpdateExpression`` validation and Secrets
  Manager ARN suffixes).
* AWS Quick (the ``quicksight`` API) has no moto support for action connectors
  or knowledge bases, so a recording fake client stands in for it. Every
  request the fake receives is validated against the real botocore
  ``quicksight`` service model, so a malformed request fails the test exactly
  as it would fail against AWS.

These tests pin the observable behavior (result status and data, the AWS calls
made and their parameters, and the persisted state) so internal refactors of
the domain layer cannot change it unnoticed.
"""

from __future__ import annotations

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover - moto is a dev dependency
    pytest.skip("moto not installed", allow_module_level=True)

from quick_admin_buddy_mcp import server
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
from quick_admin_buddy_mcp.quick.boto_client import (
    _ACTION_CONNECTOR_SHARE_ACTIONS,
    _KB_OWNER_ACTIONS,
    _KB_VIEWER_ACTIONS,
    BotoQuickAdminClient,
)
from quick_admin_buddy_mcp.quick.secrets import BotoSecretResolver
from quick_admin_buddy_mcp.state import DynamoDbStateRepository

_REGION = "eu-west-1"
_ACCOUNT = "123456789012"
_TABLE = "OnboardingState"
_TENANT = "shared"
_ADMIN = "workshopuser"
_ADMIN_ARN = f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/default/{_ADMIN}"

_OUTLOOK_SECRET = "quick-admin-buddy/outlook-custom-oauth"  # nosec B105 - secret name
_OUTLOOK_VALUES = {
    "client_id": "11111111-2222-3333-4444-555555555555",
    "client_secret": "integration-test-client-secret-value-0123456789",
    "domain_url": "https://graph.microsoft.com",
    "authorization_url": "https://login.microsoftonline.com/t/oauth2/v2.0/authorize",
    "token_url": "https://login.microsoftonline.com/t/oauth2/v2.0/token",
    "redirect_url": f"https://{_REGION}.quicksight.aws.amazon.com/sn/oauthcallback",
}
_DRIVE_SECRET = "quick-admin-buddy/google-drive-kb-admin"  # nosec B105 - secret name
_DRIVE_VALUES = {
    "type": "_".join(("service", "account")),
    "client_email": "svc@example-project.iam.gserviceaccount.com",
    "private_key": "-----BEGIN " + "PRIVATE KEY-----\n" + "A" * 1700 + "\n-----END "
    "PRIVATE KEY-----\n",
    "admin_account_email": "admin@example.com",
}
_ONEDRIVE_SECRET = "quick-admin-buddy/onedrive-kb-admin"  # nosec B105 - secret name
_ONEDRIVE_VALUES = {
    "client_id": "client-id-value",
    "tenant_id": "tenant-id-value",
    "kms_key_arn": f"arn:aws:kms:{_REGION}:{_ACCOUNT}:key/abcd",
    "certificate_thumbprint": "AB12CD34",
}

_QS_MODEL = boto3.Session().client("quicksight", region_name=_REGION).meta.service_model


def _op_name(method: str) -> str:
    return "".join(part.capitalize() for part in method.split("_"))


class FakeQuickSight:
    """Recording fake of the boto3 ``quicksight`` client.

    Validates every request against the real service model, records it, and
    returns a minimal valid response. ``fail`` maps a method name to the AWS
    error code it should raise; ``users`` is the set of existing user names.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, str] = {}
        self.users: set[str] = {_ADMIN}

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def params(self, method: str) -> dict:
        return next(kwargs for name, kwargs in self.calls if name == method)

    def _handle(self, method: str, kwargs: dict) -> dict:
        validate_parameters(
            kwargs, _QS_MODEL.operation_model(_op_name(method)).input_shape
        )
        self.calls.append((method, kwargs))
        if method in self.fail:
            raise ClientError(
                {
                    "Error": {"Code": self.fail[method], "Message": "injected"},
                    "ResponseMetadata": {"RequestId": f"req-{method}"},
                },
                _op_name(method),
            )
        meta = {"ResponseMetadata": {"RequestId": f"req-{method}"}, "Status": 200}
        if method == "describe_user":
            if kwargs["UserName"] not in self.users:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ResourceNotFoundException",
                            "Message": "USER not found",
                        }
                    },
                    "DescribeUser",
                )
            return {"User": {"UserName": kwargs["UserName"]}, **meta}
        if method == "create_data_source":
            ds_id = kwargs["DataSourceId"]
            return {
                "Arn": f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:datasource/{ds_id}",
                "DataSourceId": ds_id,
                "CreationStatus": "CREATION_IN_PROGRESS",
                **meta,
            }
        if method == "create_knowledge_base":
            return {"KnowledgeBaseId": kwargs["KnowledgeBaseId"], **meta}
        return meta

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda **kwargs: self._handle(method, kwargs)


@pytest.fixture
def env(monkeypatch):
    """Wire the tool layer to real clients on moto plus the fake quicksight."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=_REGION)
        dynamodb.create_table(
            TableName=_TABLE,
            KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sm = boto3.client("secretsmanager", region_name=_REGION)
        arns = {}
        for name, values in (
            (_OUTLOOK_SECRET, _OUTLOOK_VALUES),
            (_DRIVE_SECRET, _DRIVE_VALUES),
            (_ONEDRIVE_SECRET, _ONEDRIVE_VALUES),
        ):
            arns[name] = sm.create_secret(Name=name, SecretString=json.dumps(values))[
                "ARN"
            ]
        quicksight = FakeQuickSight()
        state = DynamoDbStateRepository(_TABLE, resource=dynamodb)
        service = OnboardingService(
            ConnectorCatalog(),
            state,
            BotoQuickAdminClient(
                client=quicksight, region_name=_REGION, aws_account_id=_ACCOUNT
            ),
            BotoSecretResolver(client=sm),
        )
        yield {
            "qs": quicksight,
            "state": state,
            "arns": arns,
            "tools": server.OnboardingTools(service, _TENANT),
        }


@pytest.fixture
def tools(env) -> server.OnboardingTools:
    """The tool layer wired to the real clients in ``env``."""
    return env["tools"]


def _status(env, record_key: str) -> str | None:
    record = env["state"].get(_TENANT).connectors.get(record_key)
    return record.status.value if record else None


def _suite(tools, suite: str = "microsoft_365") -> dict:
    return tools.set_productivity_suite(suite)


# ---------------------------------------------------------------------------
# Catalog and suite
# ---------------------------------------------------------------------------


def test_list_connector_catalog(tools, env):
    full = tools.list_connector_catalog()
    assert full["status"] == "success" and len(full["data"]["connectors"]) == 14
    m365 = tools.list_connector_catalog("microsoft_365")
    assert {c["key"] for c in m365["data"]["connectors"]} == {
        "outlook",
        "ms_teams",
        "onedrive",
        "sharepoint",
        "confluence",
        "atlassian",
    }
    assert tools.list_connector_catalog("bogus")["status"] == "validation_failure"
    assert env["qs"].calls == []


def test_set_productivity_suite_lifecycle_and_conflict(tools, env):
    assert _suite(tools)["status"] == "success"
    assert _suite(tools)["status"] == "already_configured"
    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    conflict = _suite(tools, "google_workspace")
    assert conflict["status"] == "suite_conflict"
    assert conflict["data"]["conflicting_connectors"] == ["outlook"]
    assert env["state"].get(_TENANT).suite.value == "microsoft_365"


# ---------------------------------------------------------------------------
# Action connectors
# ---------------------------------------------------------------------------


def test_enable_action_connector_creates_shares_and_persists(tools, env):
    _suite(tools)
    result = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
            share_with=_ADMIN,
        )
    )

    assert result == {
        "status": "success",
        "message": "The action connector for 'outlook' has been enabled.",
        "data": {"connector_key": "outlook", "integration_type": "action_connector"},
    }
    assert env["qs"].methods() == ["describe_user", "create_action_connector"]
    sent = env["qs"].params("create_action_connector")
    assert sent["ActionConnectorId"] == "quickadminbuddy-outlook"
    grant = sent["AuthenticationConfig"]["AuthenticationMetadata"][
        "AuthorizationCodeGrantMetadata"
    ]
    details = grant["AuthorizationCodeGrantCredentialsDetails"][
        "AuthorizationCodeGrantDetails"
    ]
    assert details["ClientSecret"] == _OUTLOOK_VALUES["client_secret"]
    assert grant["BaseEndpoint"] == _OUTLOOK_VALUES["domain_url"]
    assert sent["Permissions"] == [
        {"Principal": _ADMIN_ARN, "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS)}
    ]
    assert _status(env, "outlook:action_connector") == "enabled"
    assert _OUTLOOK_VALUES["client_secret"] not in json.dumps(result)

    again = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    assert again["status"] == "already_configured"
    assert env["qs"].methods().count("create_action_connector") == 1


@pytest.mark.parametrize(
    "request_kwargs,error_type",
    [
        (
            {"auth_params": {"client_secret": "pasted-in-chat-secret-0123456789"}},
            "inline_secret_not_allowed",
        ),
        (
            {"secret_id": _OUTLOOK_SECRET, "share_with": "someone@example.com"},
            "principal_not_found",
        ),
    ],
)
def test_enable_action_connector_rejections_create_nothing(
    tools, env, request_kwargs, error_type
):
    _suite(tools)
    result = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook", auth_type="custom_oauth_app", **request_kwargs
        )
    )
    assert result["status"] == "validation_failure"
    assert result["data"]["error_type"] == error_type
    assert "create_action_connector" not in env["qs"].methods()
    assert _status(env, "outlook:action_connector") is None


def test_enable_action_connector_arn_secret_and_api_error(tools, env):
    _suite(tools)
    arn = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=env["arns"][_OUTLOOK_SECRET],
        )
    )
    assert arn["status"] == "validation_failure"
    assert "NAME, not an ARN" in arn["message"]

    env["qs"].fail["create_action_connector"] = "InvalidParameterValueException"
    failed = tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    assert failed["status"] == "server_error"
    assert "InvalidParameterValueException" in failed["message"]
    assert _status(env, "outlook:action_connector") is None


def test_disable_action_connector(tools, env):
    _suite(tools)
    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    result = tools.disable_action_connector(
        DisableActionConnectorRequest(connector_key="outlook")
    )
    assert result["status"] == "success"
    assert (
        env["qs"].params("delete_action_connector")["ActionConnectorId"]
        == "quickadminbuddy-outlook"
    )
    assert _status(env, "outlook:action_connector") == "not_configured"
    again = tools.disable_action_connector(
        DisableActionConnectorRequest(connector_key="outlook")
    )
    assert again["status"] == "already_configured"


def test_share_action_connector(tools, env):
    result = tools.share_action_connector(
        ShareActionConnectorRequest(connector_key="outlook", share_with=_ADMIN)
    )
    assert result["status"] == "success"
    assert result["data"]["shared_with"] == _ADMIN
    sent = env["qs"].params("update_action_connector_permissions")
    assert sent["GrantPermissions"] == [
        {"Principal": _ADMIN_ARN, "Actions": list(_ACTION_CONNECTOR_SHARE_ACTIONS)}
    ]


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------


def test_enable_google_drive_kb_with_default_secret(tools, env):
    _suite(tools, "google_workspace")
    result = tools.enable_kb_integration(
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=_ADMIN
        )
    )
    assert result["status"] == "success", result["message"]
    assert env["qs"].methods() == [
        "describe_user",
        "create_data_source",
        "create_knowledge_base",
    ]
    creds = env["qs"].params("create_data_source")["Credentials"][
        "ServiceAccountCredentials"
    ]
    assert creds["ClientEmail"] == _DRIVE_VALUES["client_email"]
    kb = env["qs"].params("create_knowledge_base")
    assert kb["DataSourceArn"].endswith("datasource/quickadminbuddy-google_drive")
    assert kb["Permissions"] == [
        {"Principal": _ADMIN_ARN, "Actions": list(_KB_OWNER_ACTIONS)}
    ]
    assert _status(env, "google_drive:knowledge_base") == "enabled"
    assert "A" * 100 not in json.dumps(result)


def test_enable_onedrive_kb_forwards_secret_arn_from_describe_secret(tools, env):
    _suite(tools)
    result = tools.enable_kb_integration(
        EnableKnowledgeBaseRequest(
            connector_key="onedrive",
            auth_type="admin_managed",
            secret_id=_ONEDRIVE_SECRET,
            owner=_ADMIN,
        )
    )
    assert result["status"] == "success"
    sent = env["qs"].params("create_data_source")
    assert sent["Credentials"] == {"SecretArn": env["arns"][_ONEDRIVE_SECRET]}


def test_enable_kb_rolls_back_data_source_on_failure(tools, env):
    _suite(tools, "google_workspace")
    env["qs"].fail["create_knowledge_base"] = "InvalidParameterValueException"
    result = tools.enable_kb_integration(
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=_ADMIN
        )
    )
    assert result["status"] == "server_error"
    assert result["data"]["rollback"] == {
        "data_source_id": "quickadminbuddy-google_drive",
        "data_source_deleted": True,
        "complete": True,
    }
    assert env["qs"].methods()[-1] == "delete_data_source"
    assert _status(env, "google_drive:knowledge_base") is None


def test_disable_kb_records_disabled_even_if_data_source_delete_fails(tools, env):
    _suite(tools, "google_workspace")
    tools.enable_kb_integration(
        EnableKnowledgeBaseRequest(
            connector_key="google_drive", auth_type="admin_managed", owner=_ADMIN
        )
    )
    env["qs"].fail["delete_data_source"] = "ConflictException"
    result = tools.disable_kb_integration(
        DisableKnowledgeBaseRequest(connector_key="google_drive")
    )
    assert result["status"] == "server_error"
    assert result["data"]["data_source_deleted"] is False
    assert _status(env, "google_drive:knowledge_base") == "not_configured"
    assert env["qs"].methods()[-2:] == ["delete_knowledge_base", "delete_data_source"]


def test_share_kb_grants_viewer(tools, env):
    result = tools.share_kb_integration(
        ShareKnowledgeBaseRequest(connector_key="sharepoint", share_with=_ADMIN)
    )
    assert result["status"] == "success"
    sent = env["qs"].params("update_knowledge_base_permissions")
    assert sent["GrantPermissions"] == [
        {"Principal": _ADMIN_ARN, "Actions": list(_KB_VIEWER_ACTIONS)}
    ]


# ---------------------------------------------------------------------------
# Reads and extensions
# ---------------------------------------------------------------------------


def test_configuration_summary_and_extensions(tools, env):
    _suite(tools)
    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    ext = tools.set_extension_status(
        SetExtensionStatusRequest(extension_key="word_addin", status="installed")
    )
    assert ext == {
        "status": "success",
        "message": "Recorded the 'Microsoft Word extension' extension as 'installed'.",
        "data": {"extension_key": "word_addin", "status": "installed"},
    }
    gated = tools.set_extension_status(
        SetExtensionStatusRequest(extension_key="browser_extension", status="installed")
    )
    assert gated["status"] == "validation_failure"

    config = tools.get_tenant_configuration()
    assert config["status"] == "success"
    assert config["data"]["suite"] == "microsoft_365"
    assert config["data"]["connectors"]["outlook"]["status"] == "enabled"
    assert (
        config["data"]["connectors"]["outlook"]["integrations"]["action_connector"]
        == "enabled"
    )
    assert len(config["data"]["connectors"]) == 14

    summary = tools.get_onboarding_summary()
    assert summary["status"] == "success"
    stages = summary["data"]["stages"]
    assert stages["productivity connectors"]["connectors"]["outlook"] == "enabled"

    listed = tools.list_extensions()
    statuses = {e["key"]: e["status"] for e in listed["data"]["extensions"]}
    assert statuses["word_addin"] == "installed"
    assert "browser_extension" not in statuses


def test_state_survives_a_fresh_service(tools, env):
    """State lives in DynamoDB, not in the service: a new service sees it."""
    _suite(tools)
    tools.enable_action_connector(
        EnableActionConnectorRequest(
            connector_key="outlook",
            auth_type="custom_oauth_app",
            secret_id=_OUTLOOK_SECRET,
        )
    )
    fresh = OnboardingService(
        ConnectorCatalog(),
        env["state"],
        BotoQuickAdminClient(
            client=env["qs"], region_name=_REGION, aws_account_id=_ACCOUNT
        ),
    )
    assert (
        fresh.get_configuration(_TENANT).data["connectors"]["outlook"]["status"]
        == "enabled"
    )
