#!/usr/bin/env python3
"""
Actuarial Analysis Solution MCP Integration Stack

CDK application entry point for deploying Actuarial Analysis tools
infrastructure with Amazon Quick integration using native constructs.
"""

import aws_cdk as cdk
from cdk.actuarial_stack import ActuarialToolsStack

app = cdk.App()

# Get environment configuration
env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
)

# Deploy the Actuarial Analysis stack
ActuarialToolsStack(
    app,
    "quicksuite-actuarial-mcp",
    env=env,
    description="Actuarial Analysis Solution MCP Integration with Amazon Quick",
)

app.synth()
