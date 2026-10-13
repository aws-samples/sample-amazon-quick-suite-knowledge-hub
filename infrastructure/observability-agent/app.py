#!/usr/bin/env python3
"""Quick Observability MCP - CDK App"""

import aws_cdk as cdk
from cdk.quick_observability_mcp_stack import QuickObservabilityMCPStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
)

QuickObservabilityMCPStack(
    app,
    "quick-observability-mcp",
    env=env,
    description="Quick Observability MCP with AgentCore Gateway",
)

app.synth()
