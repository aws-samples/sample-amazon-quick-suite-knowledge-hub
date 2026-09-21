#!/usr/bin/env python3
"""CDK app entry point for Quick Document Skills stack."""

import os

import aws_cdk as cdk
from document_skills_stack import DocumentSkillsStack

app = cdk.App()

DocumentSkillsStack(
    app,
    "QuickSuiteDocumentSkills",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-west-2"),
    ),
    description="Quick Document Skills via AgentCore Gateway + Runtime",
)

app.synth()
