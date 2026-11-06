"""Secrets Manager resolution for integration authentication parameters.

When the agent passes a ``secret_id`` (the secret **name/id only** — never a
full ARN) instead of inline auth parameters, the server resolves the secret and
parses it as JSON. The JSON contract is a flat ``{"<param_name>": "<value>"}``
object whose keys are the ``snake_case`` parameter names for the chosen auth
type (see :mod:`quick_admin_buddy_mcp.domain.auth`).

**Name-only contract.** Every secret-accepting tool takes the secret *name*
(e.g. ``quick-admin-buddy/sharepoint-kb``), not an ``arn:aws:secretsmanager:...``
ARN. AWS Quick auto-redacts ARN-shaped strings in tool arguments, which
silently corrupts a forwarded ARN; accepting the name only removes that failure
mode. Use :func:`is_secret_arn` to detect an ARN at the tool boundary and reject
it with a clear message. When a *canonical ARN* is genuinely required downstream
(the SharePoint/OneDrive data source's ``Credentials.SecretArn``), the server
resolves it from the name via :meth:`SecretResolver.resolve_secret_arn` — the
agent still only ever passes the name.

This module defines:

* :class:`SecretResolver` — the narrow protocol the service depends on.
* :class:`SecretResolutionError` — a *client* error (the secret is missing,
  not JSON, or not an object). The service maps this to a ``validation_failure``
  so the agent can help the admin fix the secret.
* :class:`SecretAccessError` — an *internal* failure reaching Secrets Manager
  (permissions, network, timeout). The service maps this to ``internal_error``.
* :class:`BotoSecretResolver` — the ``boto3`` runtime implementation.
* :class:`InMemorySecretResolver` — a test/in-memory implementation.
* :func:`is_secret_arn` — the single predicate for the name-only rule.

Resolvers never log or echo secret values.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

#: Prefix every Secrets Manager (indeed every AWS) ARN starts with. The
#: name-only contract rejects any ``secret_id`` beginning with ``arn:``.
_ARN_PREFIX = "arn:"


def is_secret_arn(value: str | None) -> bool:
    """Return whether ``value`` looks like an ARN rather than a secret name.

    The name-only contract forbids passing an ARN as ``secret_id``. Any string
    beginning with ``arn:`` (case-insensitively, ignoring surrounding
    whitespace) is treated as an ARN. ``None``/empty is not an ARN.
    """
    if not value:
        return False
    return value.strip().lower().startswith(_ARN_PREFIX)


class SecretResolutionError(Exception):
    """The referenced secret could not be used (missing / not valid JSON object).

    This is a *caller-fixable* condition (wrong secret id, malformed secret
    body), mapped by the service to a ``validation_failure`` so the agent can
    guide the admin to correct the secret.
    """


class SecretAccessError(Exception):
    """An internal failure accessing Secrets Manager (permissions/network).

    Mapped by the service to an ``internal_error``. The message is sanitized and
    never contains secret material.
    """


@runtime_checkable
class SecretResolver(Protocol):
    """Resolve a Secrets Manager secret name to a parsed JSON object or its ARN."""

    def get_secret_json(self, secret_id: str) -> dict:
        """Return the secret's JSON body as a ``dict``.

        ``secret_id`` is the secret **name/id** (never an ARN — see the
        name-only contract in the module docstring).

        Raises :class:`SecretResolutionError` when the secret is absent or its
        body is not a JSON object, and :class:`SecretAccessError` on an
        internal/permission/network failure.
        """
        ...

    def resolve_secret_arn(self, secret_name: str) -> str:
        """Return the canonical Secrets Manager ARN for ``secret_name``.

        Used only where a real ARN must be forwarded to another AWS API (the
        SharePoint/OneDrive data source's ``Credentials.SecretArn``). The agent
        still passes a name; the server resolves the ARN here so an ARN never
        has to travel through a (Quick-redacted) tool argument.

        Raises :class:`SecretResolutionError` when the secret does not exist and
        :class:`SecretAccessError` on an internal/permission/network failure.
        """
        ...


def _parse_secret_string(secret_id: str, secret_string: str | None) -> dict:
    """Parse a secret string into a JSON object, raising on malformed content."""
    if not secret_string:
        raise SecretResolutionError(
            f"Secret {secret_id!r} has no string value to parse. Store the auth "
            "parameters as a JSON object in the secret's plaintext value."
        )
    try:
        payload = json.loads(secret_string)
    except (ValueError, TypeError) as exc:
        raise SecretResolutionError(
            f"Secret {secret_id!r} is not valid JSON. Store a JSON object whose "
            "keys are the snake_case auth parameter names."
        ) from exc
    if not isinstance(payload, dict):
        raise SecretResolutionError(
            f"Secret {secret_id!r} must be a JSON object mapping parameter names "
            "to values."
        )
    return payload


class BotoSecretResolver:
    """A ``boto3``-backed :class:`SecretResolver`.

    The underlying Secrets Manager client is created lazily and *without*
    explicit credentials, so it resolves the AgentCore Runtime execution role
    from the standard credential chain (consistent with the Quick client and the
    DynamoDB repository). No fallback credentials are ever supplied.
    """

    def __init__(
        self,
        client: Any = None,
        *,
        region_name: str | None = None,
    ) -> None:
        self._client = client
        self._region_name = region_name

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3

                kwargs: dict[str, Any] = {}
                if self._region_name is not None:
                    kwargs["region_name"] = self._region_name
                self._client = boto3.client("secretsmanager", **kwargs)
            except Exception as exc:  # noqa: BLE001 - fail fast, classify as access
                raise SecretAccessError(
                    "Could not initialize the Secrets Manager client. Verify the "
                    "execution role and region are configured, then retry."
                ) from exc
        return self._client

    def get_secret_json(self, secret_id: str) -> dict:
        client = self._get_client()
        try:
            response = client.get_secret_value(SecretId=secret_id)
        except Exception as exc:  # noqa: BLE001 - classify by error code below
            code = _error_code(exc)
            # Caller-fixable: the secret name/ARN is wrong or inaccessible as a
            # resource.
            if code in {"ResourceNotFoundException", "InvalidParameterException"}:
                raise SecretResolutionError(
                    f"The secret {secret_id!r} could not be found. Verify the "
                    "secret name or ARN and that it exists in this account/region."
                ) from exc
            # Access denied is the most common failure here and is almost always
            # the secret-naming / IAM scope: the execution role only grants
            # GetSecretValue on secrets named 'quick-admin-buddy/*' or
            # '<stack-name>-*'. Name the message so the remedy is obvious.
            if code in {"AccessDeniedException", "AccessDenied"}:
                raise SecretAccessError(
                    "Access to the secret was denied. The server may only read "
                    "secrets whose name starts with 'quick-admin-buddy/' (or the "
                    "stack's own '<stack-name>-' prefix). Rename the secret to use "
                    "that prefix, or widen the AuthSecretRead policy to include "
                    "this secret, then retry. Also pass secret_id as the secret "
                    "NAME/ID (the part after ':secret:'), not the full ARN."
                ) from exc
            # Everything else (throttling, network, service error) is a generic
            # internal access failure.
            raise SecretAccessError(
                "Could not read the referenced secret from Secrets Manager "
                "(permission or network error). Verify the execution role may "
                "read the secret, then retry."
            ) from exc
        return _parse_secret_string(secret_id, response.get("SecretString"))

    def resolve_secret_arn(self, secret_name: str) -> str:
        """Return the canonical ARN for ``secret_name`` via ``GetSecretValue``.

        ``GetSecretValue`` returns the secret's full ARN (name plus AWS's
        trailing ``-<6 chars>`` suffix) in its response, so the server can
        forward a valid ``Credentials.SecretArn`` while the agent only ever
        supplies the name. We deliberately use ``GetSecretValue`` rather than
        ``DescribeSecret`` because the ``AuthSecretRead`` execution-role policy
        grants ``GetSecretValue`` only — reusing it keeps the role least-
        privilege and avoids a second IAM permission. Errors are classified
        exactly like :meth:`get_secret_json`.
        """
        client = self._get_client()
        try:
            response = client.get_secret_value(SecretId=secret_name)
        except Exception as exc:  # noqa: BLE001 - classify by error code below
            code = _error_code(exc)
            if code in {"ResourceNotFoundException", "InvalidParameterException"}:
                raise SecretResolutionError(
                    f"The secret {secret_name!r} could not be found. Verify the "
                    "secret name and that it exists in this account/region."
                ) from exc
            if code in {"AccessDeniedException", "AccessDenied"}:
                raise SecretAccessError(
                    "Access to the secret was denied. The server may only read "
                    "secrets whose name starts with 'quick-admin-buddy/' (or the "
                    "stack's own '<stack-name>-' prefix). Rename the secret to use "
                    "that prefix, or widen the AuthSecretRead policy to include "
                    "this secret, then retry."
                ) from exc
            raise SecretAccessError(
                "Could not describe the referenced secret in Secrets Manager "
                "(permission or network error). Verify the execution role may "
                "read the secret, then retry."
            ) from exc
        arn = response.get("ARN")
        if not arn:
            raise SecretResolutionError(
                f"Secrets Manager returned no ARN for {secret_name!r}."
            )
        return arn


def _error_code(exc: Exception) -> str | None:
    """Best-effort extraction of an AWS error code from a botocore exception."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response.get("Error", {}).get("Code")
    return None


class InMemorySecretResolver:
    """An in-memory :class:`SecretResolver` for tests.

    Maps secret ids to parsed JSON objects. An unknown id raises
    :class:`SecretResolutionError`; a configured :class:`SecretAccessError`
    (via :meth:`set_access_error`) simulates an internal failure.
    """

    def __init__(self, secrets: dict[str, dict] | None = None) -> None:
        self._secrets: dict[str, dict] = dict(secrets or {})
        self._access_error_ids: set[str] = set()
        self._arns: dict[str, str] = {}

    def put(self, secret_id: str, payload: dict) -> InMemorySecretResolver:
        """Register a secret payload. Returns ``self`` to chain."""
        self._secrets[secret_id] = dict(payload)
        return self

    def set_arn(self, secret_name: str, arn: str) -> InMemorySecretResolver:
        """Register an explicit ARN for ``secret_name``. Returns ``self``."""
        self._arns[secret_name] = arn
        return self

    def set_access_error(self, secret_id: str) -> InMemorySecretResolver:
        """Make ``secret_id`` raise :class:`SecretAccessError` on resolution."""
        self._access_error_ids.add(secret_id)
        return self

    def get_secret_json(self, secret_id: str) -> dict:
        if secret_id in self._access_error_ids:
            raise SecretAccessError("injected secret access failure")
        if secret_id not in self._secrets:
            raise SecretResolutionError(f"The secret {secret_id!r} could not be found.")
        return dict(self._secrets[secret_id])

    def resolve_secret_arn(self, secret_name: str) -> str:
        if secret_name in self._access_error_ids:
            raise SecretAccessError("injected secret access failure")
        if secret_name in self._arns:
            return self._arns[secret_name]
        if secret_name not in self._secrets:
            raise SecretResolutionError(
                f"The secret {secret_name!r} could not be found."
            )
        # Synthesize a deterministic, ARN-shaped value for tests. Real ARNs end
        # with a random 6-char suffix; the exact value is irrelevant in memory.
        return (
            f"arn:aws:secretsmanager:us-east-1:000000000000:secret:{secret_name}-AbCdEf"
        )
