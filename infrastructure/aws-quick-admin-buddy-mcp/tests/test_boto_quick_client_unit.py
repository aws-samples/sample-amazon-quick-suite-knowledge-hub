"""Example unit tests for :class:`BotoQuickAdminClient` (task 16.1).

These verify the boto3-backed client wiring using a hand-rolled fake boto3
client (no network, no moto):

* Each of the five protocol methods invokes the correspondingly-named boto3
  method and returns the mapped ``ConnectorDetail`` / ``PermissionDetail``
  (Requirements 7.1-7.5).
* A ``botocore.exceptions.ClientError`` (an API-level error response) is mapped
  to :class:`QuickAdminApiError` carrying the AWS error code, with a sanitized
  message (Requirement 7.6).
* A ``botocore.exceptions.NoCredentialsError`` (auth-resolution failure) is
  mapped to :class:`QuickAdminInternalError` with no retry / fallback attempt
  (Requirements 7.7, 11.2).

The dedicated, exhaustive per-action + auth-failure suite is the separate
optional task 16.2; this is a focused example set.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from quick_admin_buddy_mcp.quick import (
    BotoQuickAdminClient,
    ConnectorDetail,
    PermissionDetail,
    QuickAdminApiError,
    QuickAdminInternalError,
)


class FakeBotoClient:
    """A minimal fake exposing the AWS Quick admin boto3 methods used here.

    Covers the five action-connector read/create actions plus the knowledge-base
    create and the two delete actions. Each method records its call and returns a
    configured response, or raises a configured exception. This lets the tests
    assert both invocation and error-mapping without any AWS calls.
    """

    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def _dispatch(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if name in self.errors:
            raise self.errors[name]
        return self.responses.get(name, {})

    def create_action_connector(self, **kwargs):
        return self._dispatch("create_action_connector", **kwargs)

    def describe_action_connector(self, **kwargs):
        return self._dispatch("describe_action_connector", **kwargs)

    def describe_action_connector_permissions(self, **kwargs):
        return self._dispatch("describe_action_connector_permissions", **kwargs)

    def list_action_connectors(self, **kwargs):
        return self._dispatch("list_action_connectors", **kwargs)

    def search_action_connectors(self, **kwargs):
        return self._dispatch("search_action_connectors", **kwargs)

    def delete_action_connector(self, **kwargs):
        return self._dispatch("delete_action_connector", **kwargs)

    def create_knowledge_base(self, **kwargs):
        return self._dispatch("create_knowledge_base", **kwargs)

    def delete_knowledge_base(self, **kwargs):
        return self._dispatch("delete_knowledge_base", **kwargs)


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, operation)


# ---------------------------------------------------------------------------
# Each protocol method invokes the right boto3 method + maps the response
# (Requirements 7.1-7.5)
# ---------------------------------------------------------------------------


def test_create_action_connector_invokes_boto_and_maps_detail():
    fake = FakeBotoClient(
        responses={
            "create_action_connector": {
                "ActionConnector": {"Key": "outlook", "Status": "ENABLED"}
            }
        }
    )
    # aws_account_id is injected so the test never makes a real STS call;
    # auth_type/auth_params mirror what the domain service always supplies
    # (Requirement 7's custom_oauth_app contract).
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    detail = client.create_action_connector(
        "outlook",
        {
            "tenant": "acme",
            "auth_type": "custom_oauth_app",
            "auth_params": {
                "client_id": "cid",
                "client_secret": "csecret",
                "domain_url": "https://graph.microsoft.com",
                "authorization_url": "https://login.microsoftonline.com/authorize",
                "token_url": "https://login.microsoftonline.com/token",
                "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
            },
        },
    )

    assert [name for name, _ in fake.calls] == ["create_action_connector"]
    assert isinstance(detail, ConnectorDetail)
    assert detail.key == "outlook"
    assert detail.status == "enabled"
    # Response preserved verbatim under raw.
    assert detail.raw["ActionConnector"]["Key"] == "outlook"
    # The request sent AwsAccountId and the real tagged-union auth shape
    # (Requirement 7.1; verified against the live CreateActionConnector model).
    sent_params = fake.calls[0][1]
    assert sent_params["AwsAccountId"] == "123456789012"
    auth_config = sent_params["AuthenticationConfig"]
    assert auth_config["AuthenticationType"] == "OAUTH2_AUTHORIZATION_CODE"
    grant_details = auth_config["AuthenticationMetadata"][
        "AuthorizationCodeGrantMetadata"
    ]["AuthorizationCodeGrantCredentialsDetails"]["AuthorizationCodeGrantDetails"]
    assert grant_details["ClientId"] == "cid"
    assert grant_details["ClientSecret"] == "csecret"
    # Default resource id prefix when none is supplied.
    assert sent_params["ActionConnectorId"] == "quickadminbuddy-outlook"


def test_create_action_connector_uses_custom_id_prefix():
    fake = FakeBotoClient(responses={"create_action_connector": {}})
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    client.create_action_connector(
        "outlook",
        {
            "id_prefix": "my-company-",
            "auth_type": "custom_oauth_app",
            "auth_params": {
                "client_id": "cid",
                "client_secret": "csecret",
                "domain_url": "https://graph.microsoft.com",
                "authorization_url": "https://login.microsoftonline.com/authorize",
                "token_url": "https://login.microsoftonline.com/token",
                "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
            },
        },
    )

    # The caller-supplied prefix drives ActionConnectorId: <prefix><key>.
    assert fake.calls[0][1]["ActionConnectorId"] == "my-company-outlook"


def test_create_knowledge_base_uses_custom_id_prefix():
    fake = FakeBotoClient(responses={"create_knowledge_base": {}})
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    client.create_knowledge_base(
        "sharepoint",
        {
            "id_prefix": "acme_",
            "auth_type": "service_to_service_oauth",
            "auth_params": {
                "client_id": "cid",
                "client_secret": "csecret",
                "domain_url": "https://graph.microsoft.com",
                "token_url": "https://login.microsoftonline.com/token",
            },
            "config": {},
        },
    )

    assert fake.calls[0][1]["KnowledgeBaseId"] == "acme_sharepoint"


def test_delete_action_connector_uses_custom_id_prefix():
    fake = FakeBotoClient(responses={"delete_action_connector": {}})
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    client.delete_action_connector("outlook", "my-company-")

    assert fake.calls[0][0] == "delete_action_connector"
    assert fake.calls[0][1]["ActionConnectorId"] == "my-company-outlook"
    # AwsAccountId is required on DeleteActionConnector and must be injected.
    assert fake.calls[0][1]["AwsAccountId"] == "123456789012"


def test_delete_action_connector_defaults_prefix_when_omitted():
    fake = FakeBotoClient(responses={"delete_action_connector": {}})
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    client.delete_action_connector("outlook")

    assert fake.calls[0][1]["ActionConnectorId"] == "quickadminbuddy-outlook"
    assert fake.calls[0][1]["AwsAccountId"] == "123456789012"


def test_describe_action_connector_invokes_boto_and_maps_detail():
    fake = FakeBotoClient(
        responses={
            "describe_action_connector": {
                "ActionConnector": {"Key": "gmail", "Status": "enabled"}
            }
        }
    )
    client = BotoQuickAdminClient(client=fake)

    detail = client.describe_action_connector("gmail")

    assert fake.calls[0][0] == "describe_action_connector"
    assert fake.calls[0][1] == {"Key": "gmail"}
    assert isinstance(detail, ConnectorDetail)
    assert detail.key == "gmail"


def test_describe_action_connector_permissions_maps_grant_and_url():
    fake = FakeBotoClient(
        responses={
            "describe_action_connector_permissions": {
                "Permissions": {
                    "Granted": False,
                    "AuthorizationUrl": "https://auth.example/authorize",
                }
            }
        }
    )
    client = BotoQuickAdminClient(client=fake)

    perm = client.describe_action_connector_permissions("gdrive")

    assert fake.calls[0][0] == "describe_action_connector_permissions"
    assert isinstance(perm, PermissionDetail)
    assert perm.key == "gdrive"
    assert perm.granted is False
    assert perm.authorization_url == "https://auth.example/authorize"


def test_describe_permissions_defaults_to_granted_when_no_url():
    fake = FakeBotoClient(
        responses={"describe_action_connector_permissions": {"Permissions": {}}}
    )
    client = BotoQuickAdminClient(client=fake)

    perm = client.describe_action_connector_permissions("slack")

    assert perm.granted is True
    assert perm.authorization_url is None


def test_list_action_connectors_invokes_boto_and_maps_list():
    fake = FakeBotoClient(
        responses={
            "list_action_connectors": {
                "ActionConnectors": [
                    {"Key": "outlook", "Status": "ENABLED"},
                    {"Key": "gmail", "Status": "not_configured"},
                ]
            }
        }
    )
    client = BotoQuickAdminClient(client=fake)

    connectors = client.list_action_connectors()

    assert fake.calls[0][0] == "list_action_connectors"
    assert [c.key for c in connectors] == ["outlook", "gmail"]
    assert all(isinstance(c, ConnectorDetail) for c in connectors)
    assert connectors[0].status == "enabled"


def test_search_action_connectors_invokes_boto_with_query():
    fake = FakeBotoClient(
        responses={"search_action_connectors": {"Connectors": [{"Key": "confluence"}]}}
    )
    client = BotoQuickAdminClient(client=fake)

    connectors = client.search_action_connectors({"Filters": [{"Name": "cat"}]})

    assert fake.calls[0][0] == "search_action_connectors"
    assert fake.calls[0][1] == {"Filters": [{"Name": "cat"}]}
    assert [c.key for c in connectors] == ["confluence"]


# ---------------------------------------------------------------------------
# ClientError -> QuickAdminApiError (Requirement 7.6)
# ---------------------------------------------------------------------------


def test_client_error_maps_to_api_error_with_code_and_sanitized_message():
    fake = FakeBotoClient(
        errors={
            "create_action_connector": _client_error(
                "AccessDeniedException", "CreateActionConnector"
            )
        }
    )
    client = BotoQuickAdminClient(client=fake, aws_account_id="123456789012")

    with pytest.raises(QuickAdminApiError) as excinfo:
        client.create_action_connector(
            "outlook", {"auth_type": "custom_oauth_app", "auth_params": {}}
        )

    err = excinfo.value
    assert err.code == "AccessDeniedException"
    # Actionable, includes the AWS code AND the service's own message, but no
    # raw credential/stack text.
    assert "AccessDeniedException" in str(err)
    assert "boom" in str(err)  # the service Error.Message is surfaced
    assert "retry" in str(err).lower()
    assert "Traceback" not in str(err)


# ---------------------------------------------------------------------------
# NoCredentialsError -> QuickAdminInternalError, no fallback (Reqs 7.7, 11.2)
# ---------------------------------------------------------------------------


def test_no_credentials_error_maps_to_internal_error_without_fallback():
    fake = FakeBotoClient(errors={"list_action_connectors": NoCredentialsError()})
    client = BotoQuickAdminClient(client=fake)

    with pytest.raises(QuickAdminInternalError) as excinfo:
        client.list_action_connectors()

    # Fail fast: exactly one attempt, no retry / fallback credential attempt.
    assert len(fake.calls) == 1
    assert "authenticate" in str(excinfo.value).lower()
    assert "no fallback" in str(excinfo.value).lower()


def test_unexpected_error_maps_to_internal_error():
    fake = FakeBotoClient(errors={"describe_action_connector": RuntimeError("weird")})
    client = BotoQuickAdminClient(client=fake)

    with pytest.raises(QuickAdminInternalError):
        client.describe_action_connector("outlook")
