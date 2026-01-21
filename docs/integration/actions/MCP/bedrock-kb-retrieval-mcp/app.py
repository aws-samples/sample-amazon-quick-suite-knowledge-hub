#!/usr/bin/env python3
"""
Bedrock KB - Amazon Bedrock Knowledge Base Integration

CDK application entry point for deploying Knowledge Base
infrastructure with QuickSuite integration using native constructs.
"""

import aws_cdk as cdk
from cdk.bedrock_kb_mcp_stack import BedrockKBNativeStack

app = cdk.App()

# Get environment configuration
env = cdk.Environment(
    account=app.node.try_get_context("account") or None,
    region=app.node.try_get_context("region") or "us-east-1",
)

# Deploy the Bedrock KB stack using native constructs
BedrockKBNativeStack(
    app,
    "quicksuite-bedrock-kb-mcp",
    env=env,
    description="Amazon Bedrock Knowledge Base Integration with QuickSuite MCP",
)

app.synth()
