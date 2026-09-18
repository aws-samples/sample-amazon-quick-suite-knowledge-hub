#!/usr/bin/env python3
"""
Setup source account with 2 spaces + 2 agents (+ connector + knowledge base)
for migration testing. Fully idempotent — safe to re-run.

Space A: "ops-space" — Slack connector linked
  └── Agent A: "ops-agent" — linked to ops-space + Slack connector

Space B: "analytics-space" — S3 knowledge base linked
  └── Agent B: "analytics-agent" — linked to analytics-space (no connector)

The knowledge base is backed by an S3 bucket named
  knowledge-base-<env>-<account>
following the knowledge-base-* prefix convention (see KNOWLEDGE_BASE_IAM_SETUP.md).

Usage:
  python3 setup_source.py \
    --account-id 111122223333 \
    --region us-east-1 \
    --env dev \
    --qs-service-role aws-quicksight-service-role-v0
"""

import argparse
import time
import json
import boto3

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

DEFAULTS = {
    "account_id": "111122223333",
    "region": "us-east-1",
    "env": "dev",
    "connector_id": "slack-connector",
    "qs_service_role": "aws-quicksight-service-role-v0",
}

SPACES = {
    "ops-space": {
        "name": "Ops Space",
        "description": "Space for operations use case — with Slack connector",
        "agent_id": "ops-agent",
        "agent_name": "Ops Agent",
        "agent_instructions": (
            "You are an operations assistant. Help users manage incidents, "
            "send Slack notifications, and coordinate team responses."
        ),
        "has_connector": True,
        "has_knowledge_base": False,
    },
    "analytics-space": {
        "name": "Analytics Space",
        "description": "Space for analytics use case — with S3 knowledge base",
        "agent_id": "analytics-agent",
        "agent_name": "Analytics Agent",
        "agent_instructions": (
            "You are an analytics assistant. Help users understand data, "
            "create queries, and interpret results from the knowledge base."
        ),
        "has_connector": False,
        "has_knowledge_base": True,
        "kb_name": "Analytics S3 Knowledge Base",
    },
}

SLACK_CONNECTOR_CONFIG = {
    "Type": "SLACK",
    "AuthenticationConfig": {
        "AuthenticationType": "OAUTH2_AUTHORIZATION_CODE",
        "AuthenticationMetadata": {
            "AuthorizationCodeGrantMetadata": {
                "BaseEndpoint": "https://slack.com/api",
                "RedirectUrl": "https://us-east-1.quicksight.aws.amazon.com/sn/oauthcallback",
                "AuthorizationCodeGrantCredentialsSource": "PLAIN_CREDENTIALS",
                "AuthorizationCodeGrantCredentialsDetails": {
                    "AuthorizationCodeGrantDetails": {
                        "ClientId": "dummy-client-id",
                        "ClientSecret": "dummy-client-secret",
                        "TokenEndpoint": "https://slack.com/api/oauth.v2.access",
                        "AuthorizationEndpoint": "https://slack.com/oauth/v2/authorize",
                    }
                },
            }
        },
    },
}

SPACE_PERMISSIONS = [
    "quicksight:DescribeSpace",
    "quicksight:UpdateSpace",
    "quicksight:DeleteSpace",
    "quicksight:DescribeSpacePermissions",
    "quicksight:UpdateSpacePermissions",
    "quicksight:ListDocument",
    "quicksight:DeleteDocument",
    "quicksight:GetDocument",
    "quicksight:CreateDocument",
    "quicksight:CreateSpaceFolder",
    "quicksight:ListSpaceFolderMembers",
    "quicksight:MoveSpaceFolderMember",
    "quicksight:UpdateSpaceFolder",
    "quicksight:DeleteSpaceFolder"
]

AGENT_PERMISSIONS = [
    "quicksight:DescribeAgent",
    "quicksight:UpdateAgent",
    "quicksight:DeleteAgent",
    "quicksight:DescribeAgentPermissions",
    "quicksight:UpdateAgentPermissions",
]

CONNECTOR_PERMISSIONS = [
    "quicksight:DescribeActionConnector",
    "quicksight:UpdateActionConnector",
    "quicksight:DeleteActionConnector",
    "quicksight:DescribeActionConnectorPermissions",
    "quicksight:UpdateActionConnectorPermissions",
    "quicksight:ListActionConnectors",
]

KB_PERMISSIONS = [
    "quicksight:DescribeKnowledgeBase",
    "quicksight:DescribeKnowledgeBasePermissions",
    "quicksight:UpdateKnowledgeBase",
    "quicksight:UpdateKnowledgeBasePermissions",
    "quicksight:DeleteKnowledgeBase",
    "quicksight:CreateKnowledgeBaseRefreshSchedule",
    "quicksight:DescribeKnowledgeBaseRefreshSchedule",
    "quicksight:ListKnowledgeBaseRefreshSchedules",
    "quicksight:UpdateKnowledgeBaseRefreshSchedule",
    "quicksight:DeleteKnowledgeBaseRefreshSchedule",
    "quicksight:CreateKnowledgeBaseIngestion",
    "quicksight:CancelKnowledgeBaseIngestion",
    "quicksight:DescribeKnowledgeBaseIngestion",
    "quicksight:ListKnowledgeBaseIngestions",
]

MEDIA_EXTRACTION_CONFIG = {
    "imageExtractionConfiguration": {"imageExtractionStatus": "ENABLED"},
    "audioExtractionConfiguration": {"audioExtractionStatus": "ENABLED"},
    "videoExtractionConfiguration": {
        "videoExtractionStatus": "ENABLED",
        "videoExtractionType": "VISUAL_CONTENT_AND_AUDIO_TRANSCRIPTION",
    },
}


# ═══════════════════════════════════════════════════════════════
# S3 / KNOWLEDGE BASE HELPERS
# ═══════════════════════════════════════════════════════════════

def kb_bucket_name(env: str, account_id: str) -> str:
    """knowledge-base-<env>-<account> — must start with knowledge-base- for the IAM policy."""
    return f"knowledge-base-{env}-{account_id}"


def bucket_policy_for_quicksight(bucket_name: str, account_id: str, qs_service_role: str) -> dict:
    role_arn = f"arn:aws:iam::{account_id}:role/service-role/{qs_service_role}"
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowQuick",
            "Effect": "Allow",
            "Principal": {"AWS": role_arn},
            "Action": [
                "s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation",
                "s3:GetObjectVersion", "s3:ListBucketVersions",
            ],
            "Resource": [
                f"arn:aws:s3:::{bucket_name}",
                f"arn:aws:s3:::{bucket_name}/*",
            ],
        }],
    }


def create_kb_bucket(s3, bucket_name, region, account_id, qs_service_role):
    """Create bucket (idempotent) + attach QuickSight bucket policy."""
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": region})
        print(f"  ✓ Bucket created: {bucket_name}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  ✓ Bucket already exists: {bucket_name}")
    except s3.exceptions.BucketAlreadyExists:
        print(f"  ⚠️  Bucket name taken globally: {bucket_name}")
    except Exception as e:
        print(f"  ⚠️  create_bucket: {e}")
    try:
        s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(
            bucket_policy_for_quicksight(bucket_name, account_id, qs_service_role)))
        print(f"  ✓ Bucket policy attached (QuickSight role: {qs_service_role})")
    except Exception as e:
        print(f"  ⚠️  Bucket policy: {e}")


def kb_template_config(bucket_name: str, account_id: str) -> dict:
    return {
        "templateConfiguration": {
            "template": {
                "deletionProtectionConfiguration": {
                    "enableDeletionProtection": "false",
                    "deletionProtectionThreshold": "15",
                },
                "type": "S3V2",
                "filterConfiguration": {
                    "inclusionPatterns": [],
                    "maxFileSizeInMegaBytes": "10240",
                    "inclusionPrefixes": [],
                    "exclusionPatterns": [],
                    "exclusionPrefixes": [],
                },
                "connectionConfiguration": {
                    "bucketName": bucket_name,
                    "bucketOwnerAccountId": account_id,
                },
            }
        }
    }


def wait_for_active(qs, account_id, agent_id, timeout=60):
    for _ in range(timeout // 5):
        resp = qs.describe_agent(AwsAccountId=account_id, AgentId=agent_id)
        if resp["Agent"].get("AgentStatus") == "ACTIVE":
            return
        print(f"    Waiting... ({resp['Agent'].get('AgentStatus')})")
        time.sleep(5)


# ═══════════════════════════════════════════════════════════════
# KNOWLEDGE BASE SETUP (idempotent, deterministic ids)
# ═══════════════════════════════════════════════════════════════

def setup_knowledge_base(qs, s3, args, space_id, cfg, principal):
    """Create/update the S3 bucket + data source + knowledge base, then attach to space.

    Uses deterministic ids (<space_id>-kb / <space_id>-datasource) so re-runs
    update the existing resources instead of creating duplicates.
    Returns the KB ARN if the KB exists/was created, else None.
    """
    account_id = args.account_id
    region = args.region
    bucket_name = kb_bucket_name(args.env, account_id)
    kb_id = f"{space_id}-kb"
    ds_id = f"{space_id}-datasource"
    kb_name = cfg.get("kb_name", f"{cfg['name']} Knowledge Base")
    kb_arn = f"arn:aws:quicksight:{region}:{account_id}:knowledge-base/{kb_id}"

    # 1. Bucket + policy
    print(f"  Knowledge base — S3 bucket:")
    create_kb_bucket(s3, bucket_name, region, account_id, args.qs_service_role)

    # 2. Data source (idempotent)
    ds_arn = f"arn:aws:quicksight:{region}:{account_id}:datasource/{ds_id}"
    ds_params = {
        "AwsAccountId": account_id,
        "DataSourceId": ds_id,
        "Name": f"{kb_name} - datasource",
        "DataSourceParameters": {"S3KnowledgeBaseParameters": {"BucketUrl": f"s3://{bucket_name}"}},
    }
    # Describe-first: decide create vs update explicitly (resilient to any exception class)
    ds_exists = False
    try:
        qs.describe_data_source(AwsAccountId=account_id, DataSourceId=ds_id)
        ds_exists = True
    except qs.exceptions.ResourceNotFoundException:
        ds_exists = False
    except Exception as e:
        print(f"  ⚠️  Describe data source: {e}")
    try:
        if ds_exists:
            qs.update_data_source(**ds_params)
            print(f"  ✓ Data source updated: {ds_id}")
        else:
            resp = qs.create_data_source(Type="S3_KNOWLEDGE_BASE", **ds_params)
            ds_arn = resp.get("Arn", ds_arn)
            print(f"  ✓ Data source created: {ds_id}")
    except Exception as e:
        print(f"  ⚠️  Data source: {e}")

    # 3. Knowledge base (idempotent)
    kb_exists = False
    kb_config = kb_template_config(bucket_name, account_id)
    create_params = {
        "AwsAccountId": account_id,
        "KnowledgeBaseId": kb_id,
        "Name": kb_name,
        "DataSourceArn": ds_arn,
        "KnowledgeBaseConfiguration": kb_config,
        "MediaExtractionConfiguration": MEDIA_EXTRACTION_CONFIG,
    }
    if principal:
        create_params["PrimaryOwnerArn"] = principal
        create_params["Permissions"] = [{"Principal": principal, "Actions": KB_PERMISSIONS}]
    # Describe-first: decide create vs update explicitly
    kb_already = False
    try:
        qs.describe_knowledge_base(AwsAccountId=account_id, KnowledgeBaseId=kb_id)
        kb_already = True
    except qs.exceptions.ResourceNotFoundException:
        kb_already = False
    except Exception as e:
        print(f"  ⚠️  Describe KB: {e}")
    if kb_already:
        # update (no DataSourceArn on update)
        try:
            qs.update_knowledge_base(
                AwsAccountId=account_id, KnowledgeBaseId=kb_id, Name=kb_name,
                KnowledgeBaseConfiguration=kb_config,
                MediaExtractionConfiguration=MEDIA_EXTRACTION_CONFIG,
            )
            print(f"  ✓ Knowledge base updated: {kb_id}")
            kb_exists = True
        except Exception as e:
            print(f"  ⚠️  KB update: {e}")
        if principal:
            # KB goes into UPDATING after update_knowledge_base — wait for ACTIVE
            # before granting permissions (perms are not allowed during UPDATING).
            for _ in range(24):
                try:
                    st = qs.describe_knowledge_base(
                        AwsAccountId=account_id, KnowledgeBaseId=kb_id
                    ).get("KnowledgeBase", {}).get("Status")
                    if st == "ACTIVE":
                        break
                except Exception:
                    break
                time.sleep(5)
            try:
                qs.update_knowledge_base_permissions(
                    AwsAccountId=account_id, KnowledgeBaseId=kb_id,
                    GrantPermissions=[{"Principal": principal, "Actions": KB_PERMISSIONS}],
                )
                print(f"  ✓ KB permissions granted ({len(KB_PERMISSIONS)} actions)")
            except Exception as e:
                print(f"  ⚠️  KB perms: {e}")
    else:
        try:
            resp = qs.create_knowledge_base(**create_params)
            print(f"  ✓ Knowledge base created: {kb_id} (status: {resp.get('CreationStatus')})")
            if principal:
                print(f"  ✓ KB permissions granted ({len(KB_PERMISSIONS)} actions)")
            kb_exists = True
        except Exception as e:
            print(f"  ⚠️  Knowledge base: {e}")

    # 4. Attach KB to space (only if it exists)
    if kb_exists:
        try:
            qs.update_space_resources(
                AwsAccountId=account_id, SpaceId=space_id,
                AddResources=[{"ResourceType": "KNOWLEDGE_BASE",
                               "ResourceDetails": {"resourceArn": kb_arn}}],
            )
            print(f"  ✓ Knowledge base linked to space")
        except Exception as e:
            print(f"  ⚠️  KB link: {e}")
        return kb_arn
    return None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Setup 2 spaces + 2 agents (+ connector + KB) in source")
    parser.add_argument("--account-id", default="",
                        help="Source AWS account ID. If omitted, auto-detected from the active credentials via STS.")
    parser.add_argument("--region", default=DEFAULTS["region"])
    parser.add_argument("--env", default=DEFAULTS["env"],
                        help="Environment name (dev/prod) — used in the bucket name knowledge-base-<env>-<account>")
    parser.add_argument("--connector-id", default=DEFAULTS["connector_id"])
    parser.add_argument("--qs-service-role", default=DEFAULTS["qs_service_role"],
                        help="QuickSight service role name (no API to look this up — pass it explicitly)")
    parser.add_argument("--profile", default="",
                        help="AWS CLI profile to use. Overrides any stray AWS_* credential env vars.")
    args = parser.parse_args()

    # Build an explicit session so a named profile beats credential env vars
    # (AWS_ACCESS_KEY_ID etc. otherwise take precedence over AWS_PROFILE).
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()

    # Auto-detect the account ID from the active credentials if not provided,
    # so the bucket name and every AwsAccountId call match the account you're
    # actually authenticated as (prevents account mismatch footguns).
    if not args.account_id:
        args.account_id = session.client("sts", region_name=args.region).get_caller_identity()["Account"]
        print(f"Auto-detected account ID: {args.account_id}")

    qs = session.client("quicksight", region_name=args.region)
    s3 = session.client("s3", region_name=args.region)
    connector_arn = f"arn:aws:quicksight:{args.region}:{args.account_id}:action-connector/{args.connector_id}"

    # ── Detect principal ──
    print("Detecting principal...")
    principal = None
    try:
        users = qs.list_users(AwsAccountId=args.account_id, Namespace="default").get("UserList", [])
        admins = [u for u in users if u.get("Role") in ("ADMIN", "ADMIN_PRO")]
        principal = (admins[0] if admins else users[0])["Arn"] if users else None
        if principal:
            print(f"  ✓ {principal.split('/')[-1]}")
    except Exception as e:
        print(f"  ⚠️  {e}")

    # ── Create/update Slack connector (idempotent) ──
    print(f"\nCreating Slack connector '{args.connector_id}'...")
    try:
        qs.create_action_connector(
            AwsAccountId=args.account_id,
            ActionConnectorId=args.connector_id,
            Name=args.connector_id,
            **SLACK_CONNECTOR_CONFIG,
        )
        print(f"  ✓ Created")
    except qs.exceptions.ResourceExistsException:
        # Idempotent: update the existing connector (Type is immutable — omit it)
        try:
            qs.update_action_connector(
                AwsAccountId=args.account_id,
                ActionConnectorId=args.connector_id,
                Name=args.connector_id,
                AuthenticationConfig=SLACK_CONNECTOR_CONFIG["AuthenticationConfig"],
            )
            print(f"  ✓ Updated")
        except Exception as e:
            print(f"  ⚠️  Update: {e}")
    except Exception as e:
        print(f"  ⚠️  {e}")

    if principal:
        try:
            qs.update_action_connector_permissions(
                AwsAccountId=args.account_id,
                ActionConnectorId=args.connector_id,
                GrantPermissions=[{"Principal": principal, "Actions": CONNECTOR_PERMISSIONS}],
            )
            print(f"  ✓ Connector permissions granted")
        except Exception as e:
            print(f"  ⚠️  {e}")

    # ── Create spaces + agents ──
    for space_id, cfg in SPACES.items():
        space_arn = f"arn:aws:quicksight:{args.region}:{args.account_id}:space/{space_id}"
        print(f"\n{'='*60}")
        print(f"SPACE: {cfg['name']} ({space_id})")
        print(f"{'='*60}")

        # Create/update space (idempotent)
        print(f"  Creating space...")
        try:
            qs.create_space(
                AwsAccountId=args.account_id,
                SpaceId=space_id,
                Name=cfg["name"],
                Description=cfg["description"],
            )
            print(f"  ✓ Space created")
        except qs.exceptions.ResourceExistsException:
            try:
                qs.update_space(
                    AwsAccountId=args.account_id,
                    SpaceId=space_id,
                    Name=cfg["name"],
                    Description=cfg["description"],
                )
                print(f"  ✓ Space updated")
            except Exception as e:
                print(f"  ⚠️  Space update: {e}")

        # Grant space permissions
        if principal:
            try:
                qs.update_space_permissions(
                    AwsAccountId=args.account_id,
                    SpaceId=space_id,
                    GrantPermissions=[{"Principal": principal, "Actions": SPACE_PERMISSIONS}],
                )
                print(f"  ✓ Space permissions granted")
            except Exception as e:
                print(f"  ⚠️  Space perms: {e}")

        # Link connector to space (only ops-space)
        if cfg["has_connector"]:
            try:
                qs.update_space_resources(
                    AwsAccountId=args.account_id,
                    SpaceId=space_id,
                    AddResources=[{
                        "ResourceType": "ACTION_CONNECTOR",
                        "ResourceDetails": {"resourceArn": connector_arn},
                    }],
                )
                print(f"  ✓ Slack connector linked to space")
            except Exception as e:
                print(f"  ⚠️  Connector link: {e}")

        # Setup knowledge base (only analytics-space)
        if cfg.get("has_knowledge_base"):
            setup_knowledge_base(qs, s3, args, space_id, cfg, principal)

        # Create agent (idempotent)
        agent_id = cfg["agent_id"]
        print(f"  Creating agent '{agent_id}'...")
        custom_prompt = {
            "NewPrompt": {
                "CustomInstructions": cfg["agent_instructions"],
                "Identity": cfg["agent_name"],
                "Tone": "professional",
                "OutputStyle": "concise",
                "ResponseLength": "medium",
            }
        }
        create_params = {
            "AwsAccountId": args.account_id,
            "AgentId": agent_id,
            "Name": cfg["agent_name"],
            "AgentLifecycle": "PUBLISHED",
            "Description": cfg["description"],
            "Spaces": [space_arn],
            "WelcomeMessage": f"Hi! I'm the {cfg['agent_name']}.",
            "StarterPrompts": ["What can you help with?", "Show me what's available"],
            "CustomPromptInput": custom_prompt,
        }
        if cfg["has_connector"]:
            create_params["ActionConnectors"] = [connector_arn]

        try:
            resp = qs.create_agent(**create_params)
            print(f"  ✓ Agent created (Status: {resp['AgentStatus']})")
        except qs.exceptions.ResourceExistsException:
            print(f"  Already exists — updating...")
            wait_for_active(qs, args.account_id, agent_id)
            # Read current links so we only add what's missing (avoids duplicate ARNs)
            existing_spaces, existing_connectors = [], []
            try:
                cur = qs.describe_agent(AwsAccountId=args.account_id, AgentId=agent_id)["Agent"]
                existing_spaces = cur.get("Spaces", [])
                existing_connectors = cur.get("ActionConnectors", [])
            except Exception as e:
                print(f"  ⚠️  Describe agent: {e}")
            update_params = {
                "AwsAccountId": args.account_id,
                "AgentId": agent_id,
                "Name": cfg["agent_name"],
                "Description": cfg["description"],
                "StarterPrompts": ["What can you help with?", "Show me what's available"],
                "WelcomeMessage": f"Hi! I'm the {cfg['agent_name']}.",
                "CustomPromptInput": custom_prompt,
            }
            if space_arn not in existing_spaces:
                update_params["SpacesToAdd"] = [space_arn]
            if cfg["has_connector"] and connector_arn not in existing_connectors:
                update_params["ActionConnectorsToAdd"] = [connector_arn]
            try:
                qs.update_agent(**update_params)
                print(f"  ✓ Agent updated")
            except Exception as e:
                print(f"  ⚠️  Update: {e}")

        # Grant agent permissions
        if principal:
            try:
                wait_for_active(qs, args.account_id, agent_id)
                qs.update_agent_permissions(
                    AwsAccountId=args.account_id,
                    AgentId=agent_id,
                    GrantPermissions=[{"Principal": principal, "Actions": AGENT_PERMISSIONS}],
                )
                print(f"  ✓ Agent permissions granted")
            except Exception as e:
                print(f"  ⚠️  Agent perms: {e}")

    # ── Summary ──
    print(f"\n{'='*60}")
    print("✅ SETUP COMPLETE")
    print(f"{'='*60}")
    print(f"\n  Spaces:")
    for sid, cfg in SPACES.items():
        extras = []
        if cfg["has_connector"]:
            extras.append("Slack connector")
        if cfg.get("has_knowledge_base"):
            extras.append("S3 knowledge base")
        note = f"  ({', '.join(extras)})" if extras else ""
        print(f"    • {sid} ↔ agent: {cfg['agent_id']}{note}")
    print(f"\n  Connector: {args.connector_id} (linked to ops-space + ops-agent)")
    print(f"  KB bucket: {kb_bucket_name(args.env, args.account_id)} (linked to analytics-space)")


if __name__ == "__main__":
    main()
