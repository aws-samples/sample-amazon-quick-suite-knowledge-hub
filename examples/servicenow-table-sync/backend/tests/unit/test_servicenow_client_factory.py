"""Tests for structured ServiceNow OAuth client construction."""

import json
from unittest.mock import patch

import pytest

from common.errors import ServiceNowAuthError
from common.services.servicenow_client_factory import (
    ServiceNowClientFactory,
    ServiceNowCredentials,
)

_VALID_SECRET = {
    "instance_url": "https://example.service-now.com",
    "client_id": "cid",
    "client_secret": "csecret",
    "grant_type": "client_credentials",
}


class FakeSecretsClient:
    def __init__(self, secret_string: str | None) -> None:
        self._secret_string = secret_string

    def get_secret_value(self, SecretId: str) -> dict[str, str]:  # noqa: N803
        return {} if self._secret_string is None else {"SecretString": self._secret_string}


def _factory(secret_string: str | None) -> ServiceNowClientFactory:
    return ServiceNowClientFactory(FakeSecretsClient(secret_string), "arn:secret")  # type: ignore[arg-type]


def test_valid_secret_builds_client_credentials_flow() -> None:
    with (
        patch("common.services.servicenow_client_factory.ServiceNowClientCredentialsFlow") as flow,
        patch("common.services.servicenow_client_factory.ServiceNowClient") as client,
    ):
        created = _factory(json.dumps(_VALID_SECRET)).create()

    flow.assert_called_once_with("cid", "csecret")
    client.assert_called_once_with("https://example.service-now.com", flow.return_value)
    assert created is client.return_value


def test_grant_type_defaults_to_client_credentials() -> None:
    secret = {key: value for key, value in _VALID_SECRET.items() if key != "grant_type"}
    credentials = ServiceNowCredentials.model_validate(secret)
    assert credentials.grant_type == "client_credentials"


def test_password_grant_fields_are_rejected() -> None:
    with pytest.raises(ValueError):
        ServiceNowCredentials.model_validate(
            {**_VALID_SECRET, "username": "user", "password": "pw"}
        )


def test_unsupported_grant_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        ServiceNowCredentials.model_validate({**_VALID_SECRET, "grant_type": "password"})


def test_missing_secret_string_raises() -> None:
    with pytest.raises(ServiceNowAuthError):
        _factory(None).create()


def test_malformed_json_raises() -> None:
    with pytest.raises(ServiceNowAuthError):
        _factory("not-json").create()


def test_incomplete_secret_raises() -> None:
    with pytest.raises(ServiceNowAuthError):
        _factory(json.dumps({"instance_url": "https://x.service-now.com"})).create()
