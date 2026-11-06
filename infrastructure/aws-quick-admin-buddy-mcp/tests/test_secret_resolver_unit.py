"""Unit tests for :class:`BotoSecretResolver` error classification.

These verify how the boto3-backed secret resolver maps Secrets Manager failures,
using a hand-rolled fake Secrets Manager client (no network, no moto):

* A successful ``GetSecretValue`` with a JSON object body returns the parsed
  dict.
* ``ResourceNotFoundException`` / ``InvalidParameterException`` map to
  :class:`SecretResolutionError` (caller-fixable → ``validation_failure``).
* ``AccessDeniedException`` maps to :class:`SecretAccessError` with an actionable
  message that names the secret-naming / IAM-scope remedy (the common cause when
  the enable call returns ``internal_error`` reading the secret).
* Any other error (throttling, network, service error) maps to a generic
  :class:`SecretAccessError`.
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from quick_admin_buddy_mcp.quick.secrets import (
    BotoSecretResolver,
    InMemorySecretResolver,
    SecretAccessError,
    SecretResolutionError,
    is_secret_arn,
)


class FakeSecretsManager:
    """Minimal fake exposing ``get_secret_value``.

    Returns a configured response or raises a configured exception, recording
    the ``SecretId`` it was called with so tests can assert the resolver passes
    the id through unchanged.
    """

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response or {}

    def describe_secret(self, **kwargs):
        # resolve_secret_arn MUST NOT call DescribeSecret (the AuthSecretRead
        # policy only grants GetSecretValue). Blow up loudly if it ever does.
        raise AssertionError(
            "resolve_secret_arn must use GetSecretValue, not DescribeSecret"
        )


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "GetSecretValue")


def test_get_secret_json_parses_object_body():
    payload = {"client_id": "cid", "client_secret": "sec"}
    fake = FakeSecretsManager(response={"SecretString": json.dumps(payload)})
    resolver = BotoSecretResolver(client=fake)

    result = resolver.get_secret_json("quick-admin-buddy/outlook-custom-oauth")

    assert result == payload
    # The id is passed through verbatim as SecretId.
    assert fake.calls[0]["SecretId"] == "quick-admin-buddy/outlook-custom-oauth"


def test_resource_not_found_maps_to_resolution_error():
    fake = FakeSecretsManager(error=_client_error("ResourceNotFoundException"))
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretResolutionError) as excinfo:
        resolver.get_secret_json("missing/secret")

    assert "could not be found" in str(excinfo.value)


def test_access_denied_maps_to_access_error_with_naming_hint():
    fake = FakeSecretsManager(error=_client_error("AccessDeniedException"))
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretAccessError) as excinfo:
        resolver.get_secret_json("some-other-name/outlook")

    message = str(excinfo.value)
    # The message must point at the actual remedy: the secret-naming convention
    # the AuthSecretRead policy enforces, and the name-not-ARN rule.
    assert "quick-admin-buddy/" in message
    assert "ARN" in message


def test_other_error_maps_to_generic_access_error():
    fake = FakeSecretsManager(error=_client_error("ThrottlingException"))
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretAccessError) as excinfo:
        resolver.get_secret_json("quick-admin-buddy/outlook-custom-oauth")

    # Generic permission/network message, not the access-denied naming hint.
    assert "permission or network error" in str(excinfo.value)


def test_empty_secret_string_is_resolution_error():
    fake = FakeSecretsManager(response={"SecretString": ""})
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretResolutionError):
        resolver.get_secret_json("quick-admin-buddy/empty")


def test_non_object_json_body_is_resolution_error():
    fake = FakeSecretsManager(
        response={"SecretString": json.dumps(["not", "a", "map"])}
    )
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretResolutionError):
        resolver.get_secret_json("quick-admin-buddy/list")


# ---------------------------------------------------------------------------
# is_secret_arn — the name-only predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("arn:aws:secretsmanager:eu-west-1:1:secret:quick-admin-buddy/x-AbCdEf", True),
        ("  arn:aws:secretsmanager:eu-west-1:1:secret:x  ", True),
        ("ARN:AWS:SECRETSMANAGER:eu-west-1:1:secret:x", True),
        ("quick-admin-buddy/sharepoint-kb", False),
        ("quick-admin-buddy/sharepoint-kb-AbCdEf", False),
        ("", False),
        (None, False),
    ],
)
def test_is_secret_arn(value, expected):
    assert is_secret_arn(value) is expected


# ---------------------------------------------------------------------------
# resolve_secret_arn — name -> canonical ARN
# ---------------------------------------------------------------------------


def test_boto_resolve_secret_arn_returns_arn_from_get_secret_value():
    arn = "arn:aws:secretsmanager:eu-west-1:1:secret:quick-admin-buddy/kb-AbCdEf"
    fake = FakeSecretsManager(
        response={"ARN": arn, "Name": "quick-admin-buddy/kb", "SecretString": "{}"}
    )
    resolver = BotoSecretResolver(client=fake)

    assert resolver.resolve_secret_arn("quick-admin-buddy/kb") == arn
    # The NAME is passed through as SecretId via GetSecretValue (NOT
    # DescribeSecret, which the execution role is not permitted to call).
    assert fake.calls[0]["SecretId"] == "quick-admin-buddy/kb"


def test_boto_resolve_secret_arn_not_found_is_resolution_error():
    fake = FakeSecretsManager(error=_client_error("ResourceNotFoundException"))
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretResolutionError):
        resolver.resolve_secret_arn("missing/secret")


def test_boto_resolve_secret_arn_access_denied_is_access_error():
    fake = FakeSecretsManager(error=_client_error("AccessDeniedException"))
    resolver = BotoSecretResolver(client=fake)

    with pytest.raises(SecretAccessError):
        resolver.resolve_secret_arn("quick-admin-buddy/kb")


def test_inmemory_resolve_secret_arn_synthesizes_for_known_secret():
    resolver = InMemorySecretResolver({"quick-admin-buddy/kb": {"client_id": "c"}})

    arn = resolver.resolve_secret_arn("quick-admin-buddy/kb")

    assert arn.startswith("arn:aws:secretsmanager:")
    assert "quick-admin-buddy/kb" in arn


def test_inmemory_resolve_secret_arn_honours_explicit_mapping():
    explicit = "arn:aws:secretsmanager:eu-west-1:1:secret:quick-admin-buddy/kb-ZZ"
    resolver = InMemorySecretResolver().set_arn("quick-admin-buddy/kb", explicit)

    assert resolver.resolve_secret_arn("quick-admin-buddy/kb") == explicit


def test_inmemory_resolve_secret_arn_unknown_is_resolution_error():
    resolver = InMemorySecretResolver()

    with pytest.raises(SecretResolutionError):
        resolver.resolve_secret_arn("nope/secret")
