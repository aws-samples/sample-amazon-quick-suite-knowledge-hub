#!/usr/bin/env python3
"""
Add or remove a space from a Quick Agent.

Usage:
  # Add a space
  python3 manage_agent_space.py add \
    --account-id 111122223333 \
    --agent-id my-test-agent \
    --space-id my-kb-space

  # Remove a space
  python3 manage_agent_space.py remove \
    --account-id 111122223333 \
    --agent-id my-test-agent \
    --space-id my-kb-space

  # Show current spaces
  python3 manage_agent_space.py show \
    --account-id 111122223333 \
    --agent-id my-test-agent
"""

import argparse
import time
import boto3


def wait_for_active(qs, account_id: str, agent_id: str, timeout: int = 60):
    """Poll until agent is ACTIVE."""
    for _ in range(timeout // 5):
        resp = qs.describe_agent(AwsAccountId=account_id, AgentId=agent_id)
        status = resp["Agent"].get("AgentStatus", "")
        if status == "ACTIVE":
            return True
        print(f"  Waiting... (status: {status})")
        time.sleep(5)
    return False


def main():
    parser = argparse.ArgumentParser(description="Add/remove space from a Quick Agent")
    parser.add_argument("action", choices=["add", "remove", "show"])
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--space-id", default="")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    qs = boto3.client("quicksight", region_name=args.region)
    space_arn = f"arn:aws:quicksight:{args.region}:{args.account_id}:space/{args.space_id}"

    # Get current agent state
    detail = qs.describe_agent(AwsAccountId=args.account_id, AgentId=args.agent_id)
    agent = detail["Agent"]
    agent_name = agent["Name"]
    current_spaces = agent.get("Spaces", [])

    if args.action == "show":
        print(f"Agent: {agent_name} ({args.agent_id})")
        print(f"Status: {agent.get('AgentStatus')}")
        print(f"Spaces ({len(current_spaces)}):")
        for s in current_spaces:
            print(f"  • {s}")
        if not current_spaces:
            print("  (none)")
        return

    if not args.space_id:
        print("Error: --space-id required for add/remove")
        return

    if args.action == "add":
        if space_arn in current_spaces:
            print(f"Space already attached: {space_arn}")
            return

        print(f"Adding space to agent...")
        print(f"  Agent: {agent_name} ({args.agent_id})")
        print(f"  Space: {space_arn}")

        # Wait for ACTIVE
        if agent.get("AgentStatus") != "ACTIVE":
            if not wait_for_active(qs, args.account_id, args.agent_id):
                print("  ⚠️  Agent not ACTIVE — trying anyway")

        qs.update_agent(
            AwsAccountId=args.account_id,
            AgentId=args.agent_id,
            Name=agent_name,
            SpacesToAdd=[space_arn],
        )
        print(f"  ✓ Space added")

    elif args.action == "remove":
        if space_arn not in current_spaces:
            print(f"Space not attached: {space_arn}")
            print(f"Current spaces: {current_spaces}")
            return

        print(f"Removing space from agent...")
        print(f"  Agent: {agent_name} ({args.agent_id})")
        print(f"  Space: {space_arn}")

        # Wait for ACTIVE
        if agent.get("AgentStatus") != "ACTIVE":
            if not wait_for_active(qs, args.account_id, args.agent_id):
                print("  ⚠️  Agent not ACTIVE — trying anyway")

        qs.update_agent(
            AwsAccountId=args.account_id,
            AgentId=args.agent_id,
            Name=agent_name,
            SpacesToRemove=[space_arn],
        )
        print(f"  ✓ Space removed")

    # Verify
    time.sleep(2)
    detail = qs.describe_agent(AwsAccountId=args.account_id, AgentId=args.agent_id)
    print(f"\nCurrent spaces: {detail['Agent'].get('Spaces', [])}")


if __name__ == "__main__":
    main()
