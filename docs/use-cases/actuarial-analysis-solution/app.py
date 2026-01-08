#!/usr/bin/env python3
"""
Actuarial Analysis Tools - CDK Deployment
"""

import aws_cdk as cdk
from cdk.actuarial_stack import ActuarialToolsStack

app = cdk.App()

ActuarialToolsStack(
    app,
    "ActuarialToolsStack",
    description="Actuarial analysis tools with AgentCore Gateway, SQL query engine, and Lambda",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
)

app.synth()
