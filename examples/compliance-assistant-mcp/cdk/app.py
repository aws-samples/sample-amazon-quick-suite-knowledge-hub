#!/usr/bin/env python3
"""CDK app entry point for Compliance Assistant infrastructure."""

import os

import aws_cdk as cdk
from compliance_assistant_stack import ComplianceAssistantStack

app = cdk.App()

project_name = app.node.try_get_context("projectName") or "compliance-assistant-v2"
runtime_arn = app.node.try_get_context("runtimeArn") or ""
agent_id = app.node.try_get_context("agentId") or ""
agent_alias_id = app.node.try_get_context("agentAliasId") or ""

if not runtime_arn:
    print("WARNING: runtimeArn not provided. Deploy AgentCore Runtime first (Step 3).")
if not agent_id or not agent_alias_id:
    print(
        "WARNING: agentId/agentAliasId not provided. Create Bedrock Agent first (Step 2)."
    )

ComplianceAssistantStack(
    app,
    "ComplianceAssistantV2",
    project_name=project_name,
    runtime_arn=runtime_arn,
    agent_id=agent_id,
    agent_alias_id=agent_alias_id,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION")
        or os.environ.get("AWS_REGION")
        or "us-east-1",
    ),
)

app.synth()
