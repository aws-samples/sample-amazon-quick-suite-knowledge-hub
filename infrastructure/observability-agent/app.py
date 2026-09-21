#!/usr/bin/env python3
"""Quick Observability MCP - CDK App"""

import aws_cdk as cdk
from cdk.quicksuite_observability_mcp_stack import QuickSuiteObservabilityMCPStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
)

QuickSuiteObservabilityMCPStack(
    app,
    "quicksuite-observability-mcp",
    env=env,
    description="Quick Observability MCP with AgentCore Gateway",
)

app.synth()
