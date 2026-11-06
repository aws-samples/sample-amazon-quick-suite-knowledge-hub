"""Unit tests for `_resolve_tenant`'s single-shared-tenant behavior.

This deployment does not propagate per-user identity to the backend: the
AgentCore Gateway's Cognito CUSTOM_JWT authorizer gates access, but every
authenticated caller maps to one shared tenant read from the ONBOARDING_TENANT
env var (Requirement 9.2 — the tenant is never taken from tool arguments). These
tests pin that contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from quick_admin_buddy_mcp import server


def _ctx() -> SimpleNamespace:
    """A stand-in MCP Context; _resolve_tenant accepts but ignores it."""
    return SimpleNamespace(request_context=SimpleNamespace(headers={}))


def test_returns_configured_shared_tenant(monkeypatch) -> None:
    monkeypatch.setenv(server._TENANT_ENV, "acme-shared")
    assert server._resolve_tenant() == "acme-shared"


def test_defaults_to_shared_when_env_absent(monkeypatch) -> None:
    monkeypatch.delenv(server._TENANT_ENV, raising=False)
    assert server._resolve_tenant() == server._DEFAULT_TENANT == "shared"


def test_ctx_is_ignored_and_does_not_affect_result(monkeypatch) -> None:
    """The tenant does not depend on the request context: a ctx carrying
    spoofable identity hints must not change the resolved tenant."""
    monkeypatch.setenv(server._TENANT_ENV, "acme-shared")
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            headers={
                "Authorization": "Bearer whatever",
                "X-Tenant-Id": "attacker-tenant",
            }
        )
    )
    assert server._resolve_tenant(ctx) == "acme-shared"


def test_same_tenant_regardless_of_caller(monkeypatch) -> None:
    """Every authenticated caller resolves to the identical shared tenant."""
    monkeypatch.setenv(server._TENANT_ENV, "acme-shared")
    assert server._resolve_tenant(_ctx()) == server._resolve_tenant(_ctx())
