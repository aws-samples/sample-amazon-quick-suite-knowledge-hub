"""
Supply Chain Order Fulfillment Agent — Bedrock AgentCore Runtime (MCP server).

Exposed as an MCP server (streamable-http) so it can be added in Amazon Quick as a
Custom MCP connector with a CALLABLE tool. The tool runs the multi-step reasoning
agent, which internally calls the 4 business MCP gateways.

Why MCP (not the HTTP /invocations runtime protocol): a plain HTTP runtime exposes
no MCP tools, so a Quick connector to it only shows `listTools` with nothing to
call. Exposing an @mcp.tool() gives Quick a real, invocable tool.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import traceback

from mcp.server.fastmcp import FastMCP

# ═══════════════════════════════════════════════════════════════
# Configuration — all from environment (injected by template.yaml).
# Single Cognito app client; the runtime signs in as the test user
# (USER_PASSWORD_AUTH) to mint a token the gateways' CUSTOM_JWT accepts.
# ═══════════════════════════════════════════════════════════════
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
COGNITO_CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET", "")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# JSON array string of the 4 business gateway MCP urls — injected via environment.
MCP_GATEWAY_URLS = json.loads(os.environ.get("MCP_GATEWAY_URLS", "[]"))

# Bind host. AgentCore delivers requests from outside loopback and supplies
# BIND_HOST=0.0.0.0 in the container; locally this defaults to loopback.
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
# AgentCore MCP protocol contract requires the server on port 8000 at path /mcp.
BIND_PORT = int(os.environ.get("PORT", "8000"))

SYSTEM_PROMPT = """You are the Supply Chain Order Fulfillment Agent for AnyCompany Manufacturing.

You have access to 4 MCP tool servers for supply chain operations:

1. QUOTING (sc-quoting-mcp): generate_quote, get_pricing_rules, validate_quote
2. GOVERNANCE (sc-governance-mcp): check_supplier_approval, validate_budget_authority, check_regulatory_compliance, get_policy_rules, log_audit_event, get_audit_trail
3. INVOICE (sc-invoice-processing-mcp): apply_approval_rules, get_payment_queue
4. DISRUPTION (sc-disruption-alert-mcp): assess_disruption_impact, classify_severity, recommend_mitigation, get_escalation_chain

Architecture:
- DATA (products, orders, suppliers, inventory) lives in the database — queried by the supervisor (Amazon Quick) and passed to you as context
- RULES (pricing tiers, approval thresholds, compliance checks) live in the MCPs
- You provide REASONING — analyzing data, chaining tools, making decisions

For complex requests, chain tools across servers logically.
Always provide clear reasoning about which tools you're using and why."""

_agent = None
_init_error = None


def get_cognito_token():
    """Authenticate as the configured Cognito user (USER_PASSWORD_AUTH) and return
    the access token. The gateways' CUSTOM_JWT authorizer validates the pool issuer
    and this client id — a user token is accepted (no client_credentials needed)."""
    import boto3

    if not (COGNITO_CLIENT_ID and COGNITO_USERNAME and COGNITO_PASSWORD):
        raise ValueError(
            "COGNITO_CLIENT_ID, COGNITO_USERNAME and COGNITO_PASSWORD must be set via environment variables"
        )

    auth_params = {"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD}
    if COGNITO_CLIENT_SECRET:  # confidential client → SECRET_HASH
        digest = hmac.new(
            COGNITO_CLIENT_SECRET.encode("utf-8"),
            (COGNITO_USERNAME + COGNITO_CLIENT_ID).encode("utf-8"),
            hashlib.sha256,
        ).digest()
        auth_params["SECRET_HASH"] = base64.b64encode(digest).decode()

    client = boto3.client("cognito-idp", region_name=AWS_REGION)
    resp = client.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=COGNITO_CLIENT_ID,
        AuthParameters=auth_params,
    )
    return resp["AuthenticationResult"]["AccessToken"]


def _get_agent():
    """Lazily build the Strands agent wired to the 4 business MCP gateways."""
    global _agent, _init_error
    if _agent is not None:
        return _agent
    if _init_error is not None:
        raise RuntimeError(_init_error)
    try:
        from mcp.client.streamable_http import streamablehttp_client
        from strands import Agent
        from strands.models import BedrockModel
        from strands.tools.mcp import MCPClient

        token = get_cognito_token()
        headers = {"Authorization": f"Bearer {token}"}
        mcp_clients = [
            MCPClient(lambda url=url: streamablehttp_client(url, headers=headers))
            for url in MCP_GATEWAY_URLS
        ]
        model = BedrockModel(
            inference_profile_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
            temperature=0.3,
            streaming=True,
        )
        _agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=mcp_clients)
        return _agent
    except Exception:
        _init_error = traceback.format_exc()
        raise RuntimeError(_init_error) from None


# ═══════════════════════════════════════════════════════════════
# MCP server + tool
# ═══════════════════════════════════════════════════════════════
mcp = FastMCP(
    "sc-order-fulfillment-agent", host=BIND_HOST, port=BIND_PORT, stateless_http=True
)


@mcp.tool()
def run_order_fulfillment(request: str) -> str:
    """Run the Supply Chain Order Fulfillment agent for a multi-step request.

    The agent reasons across the quoting, governance, invoice-processing, and
    disruption business rules (calling those MCP tools as needed) and returns a
    consolidated answer/plan. Pass any relevant data (orders, suppliers,
    inventory, disruptions) as context inside the request text — the agent has no
    direct data connections.

    Args:
        request: Natural-language instruction plus any data context, e.g.
                 "Assess the impact of the Port of LA closure on these orders: [...]
                  and recommend mitigation."

    Returns:
        The agent's reasoning result as text.
    """
    try:
        agent = _get_agent()
        result = agent(request)
        return str(result)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    print("=== sc-order-fulfillment-agent MCP server starting ===", file=sys.stderr)
    mcp.run(transport="streamable-http")
