"""
Create a Bedrock Agent for compliance research.

This agent is used by the CrewAI compliance_analyst as a tool
(via BedrockInvokeAgentTool) to research regulatory requirements.

Creates:
  1. IAM role for the Bedrock Agent
  2. Bedrock Agent with compliance research instructions
  3. Agent alias for invocation

Usage:
    python3 scripts/create_bedrock_agent.py

Environment variables (all optional, with defaults):
    AWS_REGION  - Target region (default: us-east-1)
    AGENT_NAME  - Bedrock Agent name (default: ComplianceResearchAgent)
    MODEL_ID    - Foundation model ID (default: us.amazon.nova-pro-v1:0)
"""

import json
import os
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_NAME = os.environ.get("AGENT_NAME", "ComplianceResearchAgent")
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")

iam = boto3.client("iam")
sts = boto3.client("sts")
bedrock = boto3.client("bedrock-agent", region_name=REGION)

ACCOUNT_ID = sts.get_caller_identity()["Account"]


def create_agent_role():
    """Create IAM role for the Bedrock Agent."""
    role_name = f"{AGENT_NAME}-role"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                },
            }
        ],
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": f"arn:aws:bedrock:{REGION}::foundation-model/*",
            }
        ],
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Bedrock Agent role for compliance research",
        )
        role_arn = role["Role"]["Arn"]
        print(f"  Created role: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
        print(f"  Role exists: {role_arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="BedrockInvoke",
        PolicyDocument=json.dumps(permissions_policy),
    )

    return role_arn


def create_agent(role_arn):
    """Create the Bedrock Agent."""
    instruction = (
        "You are a compliance research assistant specializing in regulatory frameworks "
        "for financial services and technology organizations. Your expertise covers "
        "PCI DSS, HIPAA, NIST CSF, ISO 27001, SOX, GDPR, and other major regulatory "
        "frameworks. When asked about a compliance topic, provide detailed, accurate, "
        "and current information including specific requirements, control objectives, "
        "implementation guidance, and recent updates or changes to the regulations. "
        "Always cite specific section numbers and requirements when possible. "
        "Focus on actionable information that organizations can use to achieve and "
        "maintain compliance."
    )

    response = bedrock.create_agent(
        agentName=AGENT_NAME,
        agentResourceRoleArn=role_arn,
        foundationModel=MODEL_ID,
        instruction=instruction,
        description="Compliance research agent for regulatory analysis",
        idleSessionTTLInSeconds=600,
    )

    agent_id = response["agent"]["agentId"]
    agent_status = response["agent"]["agentStatus"]
    print(f"  Agent ID: {agent_id}")
    print(f"  Status: {agent_status}")

    return agent_id


def prepare_agent(agent_id):
    """Prepare the agent for use (required before creating alias)."""
    print("  Preparing agent...")
    bedrock.prepare_agent(agentId=agent_id)

    for _ in range(30):
        resp = bedrock.get_agent(agentId=agent_id)
        status = resp["agent"]["agentStatus"]
        if status == "PREPARED":
            print("  Agent prepared.")
            return
        if status == "FAILED":
            print("  Agent preparation FAILED")
            raise Exception("Agent preparation failed")
        print(f"    Status: {status}...")
        time.sleep(5)

    raise Exception("Timed out waiting for agent preparation")


def create_alias(agent_id):
    """Create an alias for the agent."""
    response = bedrock.create_agent_alias(
        agentId=agent_id,
        agentAliasName="live",
        description="Production alias",
    )

    alias_id = response["agentAlias"]["agentAliasId"]
    print(f"  Alias ID: {alias_id}")

    # Wait for alias to be ready
    for _ in range(20):
        resp = bedrock.get_agent_alias(agentId=agent_id, agentAliasId=alias_id)
        status = resp["agentAlias"]["agentAliasStatus"]
        if status == "PREPARED":
            print("  Alias ready.")
            return alias_id
        if status == "FAILED":
            raise Exception("Alias creation failed")
        print(f"    Alias status: {status}...")
        time.sleep(3)

    raise Exception("Timed out waiting for alias")


def main():
    print("=" * 60)
    print("Bedrock Agent Setup")
    print("=" * 60)

    print("\n[1/4] Creating IAM role...")
    role_arn = create_agent_role()

    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)

    print("\n[2/4] Creating Bedrock Agent...")
    agent_id = create_agent(role_arn)

    # Wait for agent to finish creating before preparing
    print("\n  Waiting for agent to finish creating...")
    for _ in range(20):
        resp = bedrock.get_agent(agentId=agent_id)
        status = resp["agent"]["agentStatus"]
        if status != "CREATING":
            print(f"  Agent status: {status}")
            break
        print(f"    Status: {status}...")
        time.sleep(5)

    print("\n[3/4] Preparing agent...")
    prepare_agent(agent_id)

    print("\n[4/4] Creating agent alias...")
    alias_id = create_alias(agent_id)

    print("\n" + "=" * 60)
    print("BEDROCK AGENT READY")
    print("=" * 60)
    print(f"\n  AGENT_ID     = {agent_id}")
    print(f"  AGENT_ALIAS_ID = {alias_id}")
    print("\nUse these values in Step 3 (agentcore deploy) and Step 4 (cdk deploy).")


if __name__ == "__main__":
    main()
