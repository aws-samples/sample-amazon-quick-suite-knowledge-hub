"""Tests for the single-shared-tenant contract.

This deployment does not propagate per-user identity to the backend: the
AgentCore Gateway's Cognito CUSTOM_JWT authorizer gates access, but every
authenticated caller maps to one shared tenant. That tenant is fixed when
:class:`~quick_admin_buddy_mcp.server.OnboardingTools` is built (main() takes it
from ONBOARDING_TENANT), and it is never taken from a request or a tool
argument. These tests pin that contract.
"""

from __future__ import annotations

import asyncio

import pytest

from quick_admin_buddy_mcp import server
from quick_admin_buddy_mcp.domain.catalog import ConnectorCatalog
from quick_admin_buddy_mcp.domain.service import OnboardingService
from quick_admin_buddy_mcp.quick import MockQuickAdminClient
from quick_admin_buddy_mcp.state import InMemoryStateRepository

_TENANT = "acme-shared"


@pytest.fixture
def state() -> InMemoryStateRepository:
    return InMemoryStateRepository()


@pytest.fixture
def tools(state) -> server.OnboardingTools:
    service = OnboardingService(ConnectorCatalog(), state, MockQuickAdminClient())
    return server.OnboardingTools(service, _TENANT)


def test_tools_act_on_the_tenant_they_were_built_with(tools, state) -> None:
    assert tools.tenant == _TENANT
    assert tools.set_productivity_suite("microsoft_365")["status"] == "success"
    assert state.get(_TENANT).suite.value == "microsoft_365"


def test_every_call_shares_the_one_tenant(tools, state) -> None:
    tools.set_productivity_suite("microsoft_365")
    again = tools.set_productivity_suite("microsoft_365")
    assert again["status"] == "already_configured"
    assert tools.get_tenant_configuration()["data"]["suite"] == "microsoft_365"


def test_no_registered_tool_accepts_a_tenant_argument(tools) -> None:
    listed = asyncio.run(server.build_server(tools).list_tools())
    assert len(listed) == 12
    for tool in listed:
        params = set(tool.input_schema.get("properties", {}))
        assert not {p for p in params if "tenant" in p}, tool.name


def test_mcp_call_with_a_tenant_argument_cannot_switch_tenant(tools, state) -> None:
    """A caller-supplied tenant value is not a tool parameter, so it cannot
    redirect the call to another tenant's state."""
    app = server.build_server(tools)
    asyncio.run(
        app.call_tool(
            "set_productivity_suite",
            {"suite": "google_workspace", "tenant_id": "attacker-tenant"},
        )
    )
    assert state.get("attacker-tenant").suite is None
    assert state.get(_TENANT).suite.value == "google_workspace"
