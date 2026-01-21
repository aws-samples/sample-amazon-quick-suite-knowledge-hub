#!/usr/bin/env python3
"""
Redshift AgentCore Gateway - Amazon Redshift MCP Integration

CDK application entry point for deploying Redshift MCP integration
infrastructure with QuickSuite integration.
"""

import aws_cdk as cdk
from cdk.redshift_agentcore_stack import RedshiftAgentCoreStack

app = cdk.App()

# Get environment configuration
env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
)

# Deploy the Redshift AgentCore stack
RedshiftAgentCoreStack(
    app,
    "quicksuite-redshift-mcp",
    env=env,
    description="Amazon Redshift MCP Integration with QuickSuite",
)

app.synth()
