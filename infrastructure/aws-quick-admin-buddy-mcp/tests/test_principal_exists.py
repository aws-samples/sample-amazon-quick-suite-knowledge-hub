"""Tests for the pre-flight "does this Quick user exist?" check.

AWS Quick rejects a ``Permissions`` grant to a non-existent user with a
generic ``InvalidParameterValueException`` ("One or more parameters are
invalid") that does not name the cause. The server therefore looks the user up
(``DescribeUser``) first and returns a ``validation_failure`` that says the user
does not exist and that the parameter must be the Quick USERNAME, not the email.
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.domain.types import ResultStatus, Suite
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.quick.boto_client import BotoQuickAdminClient
from quick_admin_buddy_mcp.state import InMemoryStateRepository

_ACCOUNT = "123456789012"
_REGION = "eu-west-1"
_TENANT = "acme-corp"
_EMAIL = "workshopuser@example.com"

CUSTOM_OAUTH = {
    "client_id": "cid-123",
    "client_secret": "super-secret-value",
    "domain_url": "https://graph.example.com",
    "authorization_url": "https://auth.example.com/authorize",
    "token_url": "https://auth.example.com/token",
    "redirect_url": "https://eu-west-1.quicksight.aws.amazon.com/sn/oauthcallback",
}


# ---------------------------------------------------------------------------
# BotoQuickAdminClient.principal_exists
# ---------------------------------------------------------------------------


class _FakeQuickSight:
    """Fake boto3 QuickSight client: DescribeUser knows only the given users."""

    def __init__(self, users: set[str], error_code: str | None = None) -> None:
        self.users = users
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []

    def describe_user(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if self.error_code:
            raise ClientError(
                {"Error": {"Code": self.error_code, "Message": "x"}}, "DescribeUser"
            )
        if kwargs["UserName"] not in self.users:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ResourceNotFoundException",
                        "Message": "USER not found.",
                    }
                },
                "DescribeUser",
            )
        return {"User": {"UserName": kwargs["UserName"]}}


def _boto(fake: _FakeQuickSight) -> BotoQuickAdminClient:
    return BotoQuickAdminClient(
        client=fake, region_name=_REGION, aws_account_id=_ACCOUNT
    )


def test_existing_bare_user_name() -> None:
    fake = _FakeQuickSight({"workshopuser"})
    assert _boto(fake).principal_exists("workshopuser") is True
    assert fake.calls == [
        {"AwsAccountId": _ACCOUNT, "Namespace": "default", "UserName": "workshopuser"}
    ]


def test_email_instead_of_username_is_missing() -> None:
    assert _boto(_FakeQuickSight({"workshopuser"})).principal_exists(_EMAIL) is False


def test_user_arn_is_parsed_into_namespace_and_name() -> None:
    fake = _FakeQuickSight({"someone"})
    arn = f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:user/custom-ns/someone"
    assert _boto(fake).principal_exists(arn) is True
    assert fake.calls[0]["Namespace"] == "custom-ns"


@pytest.mark.parametrize(
    "principal",
    [
        f"arn:aws:quicksight:{_REGION}:{_ACCOUNT}:group/default/analysts",
        f"arn:aws:quicksight:{_REGION}:999999999999:user/default/someone",
    ],
)
def test_group_or_foreign_account_is_unknown(principal: str) -> None:
    fake = _FakeQuickSight(set())
    assert _boto(fake).principal_exists(principal) is None
    assert fake.calls == []


def test_lookup_not_permitted_is_unknown() -> None:
    # A stack that predates the DescribeUser grant must not block requests.
    fake = _FakeQuickSight(set(), error_code="AccessDeniedException")
    assert _boto(fake).principal_exists("workshopuser") is None


# ---------------------------------------------------------------------------
# OnboardingService: missing user -> validation_failure, nothing created
# ---------------------------------------------------------------------------


def _service(missing: set[str]) -> tuple[OnboardingService, MockQuickAdminClient]:
    quick = MockQuickAdminClient(missing_principals=missing)
    state = InMemoryStateRepository()
    state.put_suite(_TENANT, Suite.MICROSOFT_365)
    return OnboardingService(ConnectorCatalog(), state, quick), quick


def _assert_missing_user(result, field_name: str) -> None:
    assert result.status == ResultStatus.VALIDATION_FAILURE
    assert f"AWS Quick user {_EMAIL!r}" in result.message
    assert "does not exist" in result.message
    assert "USERNAME, not the user's email address" in result.message
    assert "often different" in result.message
    assert "'workshopuser'" in result.message  # the email local-part hint
    assert result.data["error_type"] == "principal_not_found"
    assert result.data["invalid_principal"] == _EMAIL
    assert result.data["principal_field"] == field_name


def test_enable_action_connector_with_missing_share_with_user() -> None:
    service, quick = _service({_EMAIL})

    result = service.enable_action_connector(
        _TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH, share_with=_EMAIL
    )

    _assert_missing_user(result, "share_with")
    assert quick.calls == []
    assert "super-secret-value" not in result.message


def test_enable_action_connector_with_existing_user_still_succeeds() -> None:
    service, quick = _service({_EMAIL})

    result = service.enable_action_connector(
        _TENANT, "outlook", "custom_oauth_app", CUSTOM_OAUTH, share_with="workshopuser"
    )

    assert result.status == ResultStatus.SUCCESS
    assert quick.calls_for("create_action_connector")


def test_enable_kb_with_missing_owner() -> None:
    service, quick = _service({_EMAIL})

    result = service.enable_kb_integration(
        _TENANT, "onedrive", "user_managed", owner=_EMAIL
    )

    _assert_missing_user(result, "owner")
    assert quick.calls == []


@pytest.mark.parametrize("method", ["share_action_connector", "share_kb_integration"])
def test_share_with_missing_user(method: str) -> None:
    service, quick = _service({_EMAIL})
    key = "outlook" if method == "share_action_connector" else "onedrive"

    result = getattr(service, method)(_TENANT, key, _EMAIL)

    _assert_missing_user(result, "share_with")
    assert quick.calls == []
