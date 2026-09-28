#!/usr/bin/env python3
"""
Quick Resource Migrator — Bedrock AgentCore MCP Server
════════════════════════════════════════════════════════

Migrates Quick Agents, Action Connectors, S3 Knowledge Bases, and Spaces
between AWS accounts. Resource-driven: you select a resource type
(agent | connector | knowledge_base | space) and choose resources by id, by
name, or all.

Agents are recreated with their Action Connectors attached (remapped to the
target account). Spaces are recreated and re-linked to their resources (agents,
connectors, knowledge bases) with ARNs remapped to the target account — so the
linked resources should be migrated first. Connectors carry sanitized
placeholder secrets and must be re-authenticated in the target UI. Knowledge
bases provision the target bucket + data source + KB (documents are not copied).

Permissions are NOT hardcoded — the server DESCRIBES the permissions on each
source resource and copies the exact same actions to the target.

Env vars (configure in AgentCore):
  SOURCE_ROLE_ARN  — IAM role in source account (read-only QuickSight + S3)
  TARGET_ROLE_ARN  — IAM role in target account (read+write QuickSight + S3)

Tool inputs:
  source_account_id, target_account_id, region
  resource_type: agent | connector | knowledge_base
  search_by: id | name | all
  value: the id or name to match (ignored when search_by = all)
  source_env, target_env: environment names used in KB bucket names
                          (knowledge-base-<env>-<account>)
  qs_service_role: QuickSight service role name (no API to look this up)
"""

import json
import logging
import os
import re
import sys
import traceback
import uuid
from datetime import datetime, timezone
from threading import Event

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from mcp.server.fastmcp import FastMCP

# Configure logging for CloudWatch (AgentCore captures stdout/stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
logger.info("=== Quick Resource Migrator MCP Server starting ===")
logger.info(
    f"SOURCE_ROLE_ARN configured: {'YES' if os.environ.get('SOURCE_ROLE_ARN') else 'NO'}"
)
logger.info(
    f"TARGET_ROLE_ARN configured: {'YES' if os.environ.get('TARGET_ROLE_ARN') else 'NO'}"
)

_POLL_IDLE = Event()


def _poll_wait(seconds: float) -> None:
    """Wait ``seconds`` between polls."""
    _POLL_IDLE.wait(timeout=seconds)


# ═══════════════════════════════════════════════════════════════════
# ENV CONFIG (set these in AgentCore runtime config)
# ═══════════════════════════════════════════════════════════════════

SOURCE_ROLE_ARN = os.environ.get("SOURCE_ROLE_ARN", "")
TARGET_ROLE_ARN = os.environ.get("TARGET_ROLE_ARN", "")

# Bucket (in the runner/central account where this runtime executes) for
# pre-update backups of target resources. Created by runner-role.yaml. Blank
# disables backups. The runtime writes here directly via its execution role
# (no assume-role) since the bucket lives in the runtime's own account.
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "")

DEFAULT_QS_SERVICE_ROLE = "aws-quicksight-service-role-v0"

# Connector Types accepted by create_action_connector (write model). Note the
# describe/read model can return additional types (e.g. MODEL_CONTEXT_PROTOCOL)
# that CANNOT be recreated via the API — those must be skipped with a clear msg.
CREATABLE_CONNECTOR_TYPES = {
    "GENERIC_HTTP",
    "SERVICENOW_NOW_PLATFORM",
    "SALESFORCE_CRM",
    "MICROSOFT_OUTLOOK",
    "PAGERDUTY_ADVANCE",
    "JIRA_CLOUD",
    "ATLASSIAN_CONFLUENCE",
    "AMAZON_S3",
    "AMAZON_BEDROCK_AGENT_RUNTIME",
    "AMAZON_BEDROCK_RUNTIME",
    "AMAZON_BEDROCK_DATA_AUTOMATION_RUNTIME",
    "AMAZON_TEXTRACT",
    "AMAZON_COMPREHEND",
    "AMAZON_COMPREHEND_MEDICAL",
    "MICROSOFT_ONEDRIVE",
    "MICROSOFT_SHAREPOINT",
    "MICROSOFT_TEAMS",
    "SAP_BUSINESSPARTNER",
    "SAP_PRODUCTMASTERDATA",
    "SAP_PHYSICALINVENTORY",
    "SAP_BILLOFMATERIALS",
    "SAP_MATERIALSTOCK",
    "ZENDESK_SUITE",
    "SMARTSHEET",
    "SLACK",
    "ASANA",
    "BAMBOO_HR",
}

MEDIA_EXTRACTION_CONFIG = {
    "imageExtractionConfiguration": {"imageExtractionStatus": "ENABLED"},
    "audioExtractionConfiguration": {"audioExtractionStatus": "ENABLED"},
    "videoExtractionConfiguration": {
        "videoExtractionStatus": "ENABLED",
        "videoExtractionType": "VISUAL_CONTENT_AND_AUDIO_TRANSCRIPTION",
    },
}


# ═══════════════════════════════════════════════════════════════════
# ERROR HELPERS
# ═══════════════════════════════════════════════════════════════════


def classify_error(e: Exception) -> dict:
    """Classify an AWS error into a user-friendly message."""
    error_info = {
        "error_type": type(e).__name__,
        "raw_message": str(e),
        "user_message": str(e),
        "is_retryable": False,
        "is_kms": False,
        "is_access_denied": False,
    }

    if isinstance(e, ClientError):
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", "")
        error_info["error_code"] = error_code

        if (
            "KMS" in error_msg
            or "key" in error_msg.lower()
            or "encrypt" in error_msg.lower()
        ):
            error_info["is_kms"] = True
            error_info["user_message"] = (
                f"Encryption key error: {error_msg}. "
                "The resource may reference a deleted or inaccessible KMS key. "
                "The resource may need to be recreated."
            )
        elif error_code == "AccessDeniedException":
            error_info["is_access_denied"] = True
            if (
                "KMS" in error_msg
                or "key" in error_msg.lower()
                or "encrypt" in error_msg.lower()
            ):
                error_info["is_kms"] = True
                error_info["user_message"] = (
                    f"Access denied due to encryption key issue: {error_msg}. "
                    "Required encryption key not found or inaccessible. "
                    "The resource may need to be recreated."
                )
            else:
                error_info["user_message"] = (
                    f"Access denied: {error_msg}. "
                    "Check that the IAM role has the required permissions."
                )
        elif error_code == "ResourceNotFoundException":
            error_info["user_message"] = f"Resource not found: {error_msg}"
        elif error_code == "ResourceExistsException":
            error_info["user_message"] = f"Resource already exists: {error_msg}"
        elif error_code == "ThrottlingException":
            error_info["is_retryable"] = True
            error_info["user_message"] = (
                f"API rate limit hit: {error_msg}. Try again shortly."
            )
        elif error_code == "ConflictException":
            error_info["is_retryable"] = True
            error_info["user_message"] = (
                f"Resource conflict: {error_msg}. The resource may be in a transitional state."
            )
        elif error_code == "InvalidParameterValueException":
            error_info["user_message"] = f"Invalid parameter: {error_msg}"
        else:
            error_info["user_message"] = f"{error_code}: {error_msg}"

    elif isinstance(e, BotoCoreError):
        error_info["user_message"] = f"AWS SDK error: {str(e)}"
        error_info["is_retryable"] = True

    return error_info


def format_error_for_report(context: str, e: Exception) -> dict:
    classified = classify_error(e)
    return {
        "context": context,
        "error_type": classified["error_type"],
        "error_code": classified.get("error_code", "Unknown"),
        "user_message": classified["user_message"],
        "is_kms": classified["is_kms"],
        "is_access_denied": classified["is_access_denied"],
    }


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════


def assume_role_client(service: str, role_arn: str, region: str):
    """Assume an IAM role and return a boto3 client."""
    logger.info(f"Assuming role: {role_arn} (service={service}, region={region})")
    try:
        sts = boto3.client("sts")
        creds = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="resource-migrator",
            DurationSeconds=3600,
        )["Credentials"]
        client = boto3.client(
            service,
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            config=Config(retries={"max_attempts": 3, "mode": "adaptive"}),
        )
        logger.info(f"  ✓ Role assumed successfully ({service})")
        return client
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", "")
        logger.error(f"  ✗ Failed to assume role: {error_code} - {error_msg}")
        raise RuntimeError(
            f"Failed to assume role {role_arn}: {error_code} - {error_msg}. "
            "Verify the role ARN exists and the trust policy allows this account to assume it."
        ) from e
    except Exception as e:
        logger.error(f"  ✗ Unexpected error assuming role: {e}")
        raise RuntimeError(
            f"Unexpected error assuming role {role_arn}: {str(e)}"
        ) from e


def remap_arn(arn: str, target_account: str, target_region: str) -> str:
    """Swap account + region in a QuickSight ARN."""
    parts = arn.split(":")
    parts[3] = target_region
    parts[4] = target_account
    return ":".join(parts)


def remap_principal(principal_arn: str, target_account: str, target_region: str) -> str:
    """Swap account + region in a QuickSight user/group principal ARN."""
    if not principal_arn or ":" not in principal_arn:
        return principal_arn
    parts = principal_arn.split(":")
    if len(parts) > 4:
        parts[3] = target_region
        parts[4] = target_account
    return ":".join(parts)


def sanitize_auth_config(auth_config: dict) -> dict:
    """
    Convert AuthenticationConfig from describe (read model) to create (write model).
    Uses PLACEHOLDER values for secrets (connector must be re-authenticated in target UI).
    """
    if not auth_config:
        return auth_config

    sanitized = json.loads(json.dumps(auth_config, default=str))
    metadata = sanitized.get("AuthenticationMetadata", {})
    if not metadata:
        return sanitized

    if "AuthorizationCodeGrantMetadata" in metadata:
        acg = metadata["AuthorizationCodeGrantMetadata"]
        read_details = acg.pop("ReadAuthorizationCodeGrantCredentialsDetails", {})
        read_grant = read_details.get("ReadAuthorizationCodeGrantDetails", {})
        acg["AuthorizationCodeGrantCredentialsSource"] = "PLAIN_CREDENTIALS"
        acg["AuthorizationCodeGrantCredentialsDetails"] = {
            "AuthorizationCodeGrantDetails": {
                "ClientId": read_grant.get("ClientId", "PLACEHOLDER_REQUIRES_REAUTH"),
                "ClientSecret": "PLACEHOLDER_REQUIRES_REAUTH",
                "TokenEndpoint": read_grant.get(
                    "TokenEndpoint", "https://example.com/token"
                ),
                "AuthorizationEndpoint": read_grant.get(
                    "AuthorizationEndpoint", "https://example.com/authorize"
                ),
            }
        }
        metadata["AuthorizationCodeGrantMetadata"] = acg

    if "ClientCredentialsGrantMetadata" in metadata:
        ccg = metadata["ClientCredentialsGrantMetadata"]
        read_details = ccg.pop("ReadClientCredentialsDetails", {})
        read_grant = read_details.get("ReadClientCredentialsGrantDetails", {})
        ccg["ClientCredentialsSource"] = "PLAIN_CREDENTIALS"
        ccg["ClientCredentialsDetails"] = {
            "ClientCredentialsGrantDetails": {
                "ClientId": read_grant.get("ClientId", "PLACEHOLDER_REQUIRES_REAUTH"),
                "ClientSecret": "PLACEHOLDER_REQUIRES_REAUTH",
                "TokenEndpoint": read_grant.get(
                    "TokenEndpoint", "https://example.com/token"
                ),
            }
        }
        metadata["ClientCredentialsGrantMetadata"] = ccg

    if "BasicAuthConnectionMetadata" in metadata:
        metadata["BasicAuthConnectionMetadata"].setdefault(
            "Password", "PLACEHOLDER_REQUIRES_REAUTH"
        )

    if "ApiKeyConnectionMetadata" in metadata:
        metadata["ApiKeyConnectionMetadata"].setdefault(
            "ApiKey", "PLACEHOLDER_REQUIRES_REAUTH"
        )

    if "IamConnectionMetadata" in metadata:
        metadata["IamConnectionMetadata"].pop("SourceArn", None)

    sanitized["AuthenticationMetadata"] = metadata
    return sanitized


def wait_for_active(client, account_id: str, agent_id: str, timeout: int = 60):
    """Poll until agent is ACTIVE."""
    for _ in range(timeout // 5):
        try:
            resp = client.describe_agent(AwsAccountId=account_id, AgentId=agent_id)
            if resp["Agent"].get("AgentStatus") == "ACTIVE":
                return True
        except ClientError as e:
            error_info = classify_error(e)
            logger.warning(
                f"  Poll agent '{agent_id}' failed: {error_info['user_message']}"
            )
            if not error_info["is_retryable"]:
                return False
        _poll_wait(5)
    return False


def wait_for_kb_active(client, account_id: str, kb_id: str, timeout: int = 120):
    """Poll until a knowledge base is ACTIVE.

    After create/update the KB goes into CREATING/UPDATING and permission
    grants are rejected until it returns to ACTIVE.
    """
    for _ in range(timeout // 5):
        try:
            resp = client.describe_knowledge_base(
                AwsAccountId=account_id, KnowledgeBaseId=kb_id
            )
            if resp.get("KnowledgeBase", {}).get("Status") == "ACTIVE":
                return True
        except ClientError:
            return False
        _poll_wait(5)
    return False


def copy_agent_permissions(
    source_qs, target_qs, source_account, target_account, region, agent_id, report
):
    try:
        perms = source_qs.describe_agent_permissions(
            AwsAccountId=source_account, AgentId=agent_id
        ).get("Permissions", [])
    except ClientError as e:
        logger.warning(
            f"  ⚠ describe_agent_permissions '{agent_id}': {classify_error(e)['user_message']}"
        )
        return
    _grant(target_qs, target_account, region, "agent", agent_id, perms, report)


def copy_space_permissions(
    source_qs, target_qs, source_account, target_account, region, space_id, report
):
    try:
        perms = source_qs.describe_space_permissions(
            AwsAccountId=source_account, SpaceId=space_id
        ).get("Permissions", [])
    except ClientError as e:
        logger.warning(
            f"  ⚠ describe_space_permissions '{space_id}': {classify_error(e)['user_message']}"
        )
        return
    _grant(target_qs, target_account, region, "space", space_id, perms, report)


def copy_connector_permissions(
    source_qs, target_qs, source_account, target_account, region, connector_id, report
):
    try:
        perms = source_qs.describe_action_connector_permissions(
            AwsAccountId=source_account, ActionConnectorId=connector_id
        ).get("Permissions", [])
    except ClientError as e:
        logger.warning(
            f"  ⚠ describe_action_connector_permissions '{connector_id}': {classify_error(e)['user_message']}"
        )
        return
    _grant(target_qs, target_account, region, "connector", connector_id, perms, report)


def copy_kb_permissions(
    source_qs, target_qs, source_account, target_account, region, kb_id, report
):
    try:
        perms = source_qs.describe_knowledge_base_permissions(
            AwsAccountId=source_account, KnowledgeBaseId=kb_id
        ).get("Permissions", [])
    except ClientError as e:
        logger.warning(
            f"  ⚠ describe_knowledge_base_permissions '{kb_id}': {classify_error(e)['user_message']}"
        )
        return
    _grant(target_qs, target_account, region, "kb", kb_id, perms, report)


def copy_flow_permissions(
    source_qs, target_qs, source_account, target_account, region,
    source_flow_id, target_flow_id, report
):
    """Copy flow permissions. Flows use get/update_flow_permissions (not
    describe_*), and — because CreateFlow mints a NEW id — the grant is applied
    to the TARGET flow id, which differs from the source flow id."""
    try:
        perms = source_qs.get_flow_permissions(
            AwsAccountId=source_account, FlowId=source_flow_id
        ).get("Permissions", [])
    except ClientError as e:
        logger.warning(
            f"  ⚠ get_flow_permissions '{source_flow_id}': {classify_error(e)['user_message']}"
        )
        return
    _grant(target_qs, target_account, region, "flow", target_flow_id, perms, report)


def _parse_principal_arn(principal_arn):
    """Split a QuickSight principal ARN into (kind, namespace, name).

    ARN tail looks like: user/<namespace>/<user-name...> or
    group/<namespace>/<group-name...>.
    """
    resource = principal_arn.split(":", 5)[-1]
    segments = resource.split("/")
    kind = segments[0] if segments else ""
    namespace = segments[1] if len(segments) > 1 else "default"
    name = "/".join(segments[2:]) if len(segments) > 2 else ""
    return kind, namespace, name


# Module-level memoization caches (persist across calls, but avoid the
# mutable-default-argument footgun where the shared dict is exposed as a
# rebindable parameter). Keyed as documented on each function below.
_TARGET_USERS_CACHE: dict = {}
_TARGET_PRINCIPAL_CACHE: dict = {}


def _list_target_users(target_qs, target_account, namespace):
    """List and cache all registered QuickSight users in the target namespace."""
    key = (target_account, namespace)
    if key in _TARGET_USERS_CACHE:
        return _TARGET_USERS_CACHE[key]
    users = []
    try:
        paginator = target_qs.get_paginator("list_users")
        for page in paginator.paginate(
            AwsAccountId=target_account, Namespace=namespace
        ):
            users.extend(page.get("UserList", []))
    except Exception:
        try:
            resp = target_qs.list_users(
                AwsAccountId=target_account, Namespace=namespace, MaxResults=100
            )
            users = resp.get("UserList", [])
        except ClientError:
            users = []
    _TARGET_USERS_CACHE[key] = users
    return users


def _resolve_target_principal(
    target_qs,
    target_account,
    region,
    principal_arn,
):
    """Resolve a remapped source principal to a real principal ARN that is
    registered in the target account.

    QuickSight federated users are registered under an ARN that embeds the IAM
    role + session name (e.g. user/default/<IAM-Role>/<session-name>). The
    same human in the target account may be registered under a different role,
    so a blind account-swap of the ARN often doesn't exist. We therefore:
      1. Use the remapped ARN as-is if it is already registered.
      2. Otherwise list the target users and match by (a) identical UserName,
         (b) same trailing session/user name, then (c) same email.
      3. Fall back to the sole registered user if there is exactly one.
    Returns the resolved principal ARN, or None if nothing suitable was found.
    """
    if not principal_arn or ":" not in principal_arn:
        return None
    if principal_arn in _TARGET_PRINCIPAL_CACHE:
        return _TARGET_PRINCIPAL_CACHE[principal_arn]

    kind, namespace, name = _parse_principal_arn(principal_arn)
    resolved = None

    # 1. Direct hit — already registered in the target.
    try:
        if kind == "user":
            target_qs.describe_user(
                AwsAccountId=target_account, Namespace=namespace, UserName=name
            )
            resolved = principal_arn
        elif kind == "group":
            target_qs.describe_group(
                AwsAccountId=target_account, Namespace=namespace, GroupName=name
            )
            resolved = principal_arn
    except ClientError:
        resolved = None

    # 2. For users, try to match a registered target user by name/email.
    if resolved is None and kind == "user":
        users = _list_target_users(target_qs, target_account, namespace)
        if users:
            src_tail = name.split("/")[-1].lower()  # e.g. <session-name>
            src_full = name.lower()

            # (a) identical UserName, (b) same trailing session name, (c) same email
            def _match(u):
                un = u.get("UserName", "")
                return un.lower() == src_full or un.split("/")[-1].lower() == src_tail

            candidates = [u for u in users if _match(u)]
            if not candidates:
                # (c) email local-part match (session name often equals email alias)
                candidates = [
                    u
                    for u in users
                    if u.get("Email", "").split("@")[0].lower() == src_tail
                ]
            # (d) last resort: if exactly one user is registered, use it.
            if not candidates and len(users) == 1:
                candidates = users
            if candidates:
                resolved = candidates[0].get("Arn")

    _TARGET_PRINCIPAL_CACHE[principal_arn] = resolved
    return resolved


def _grant(target_qs, target_account, region, kind, resource_id, perms, report):
    """Grant the copied permissions on the target resource, remapping principals."""
    if not perms:
        return
    grant = []
    unresolved = []
    remapped_note = []
    for p in perms:
        original = remap_principal(p.get("Principal", ""), target_account, region)
        actions = p.get("Actions", [])
        if not (original and actions):
            continue
        # Resolve to a principal that actually exists in the target account —
        # QuickSight rejects the whole grant call if any principal is unknown.
        principal = _resolve_target_principal(
            target_qs, target_account, region, original
        )
        if not principal:
            unresolved.append(original)
            continue
        if principal != original:
            remapped_note.append({"from": original, "to": principal})
        grant.append({"Principal": principal, "Actions": actions})
    if remapped_note:
        for m in remapped_note:
            logger.info(
                f"  ⓘ {kind} '{resource_id}': resolved principal {m['from']} → {m['to']}"
            )
    if unresolved:
        report.setdefault("skipped_permissions", []).append(
            {
                "resource_type": kind,
                "resource_id": resource_id,
                "unresolved_principals": unresolved,
                "reason": "No registered QuickSight user could be matched in the "
                "target account. Register/log into QuickSight in the target "
                "account, then re-run to copy these permissions.",
            }
        )
        logger.info(
            f"  ⓘ {kind} '{resource_id}': {len(unresolved)} principal(s) could not be resolved — see skipped_permissions in report"
        )
    if not grant:
        return
    try:
        if kind == "agent":
            target_qs.update_agent_permissions(
                AwsAccountId=target_account, AgentId=resource_id, GrantPermissions=grant
            )
        elif kind == "connector":
            target_qs.update_action_connector_permissions(
                AwsAccountId=target_account,
                ActionConnectorId=resource_id,
                GrantPermissions=grant,
            )
        elif kind == "kb":
            target_qs.update_knowledge_base_permissions(
                AwsAccountId=target_account,
                KnowledgeBaseId=resource_id,
                GrantPermissions=grant,
            )
        elif kind == "space":
            target_qs.update_space_permissions(
                AwsAccountId=target_account,
                SpaceId=resource_id,
                GrantPermissions=grant,
            )
        elif kind == "flow":
            target_qs.update_flow_permissions(
                AwsAccountId=target_account,
                FlowId=resource_id,
                GrantPermissions=grant,
            )
        logger.info(
            f"  ✓ Copied {kind} permissions to '{resource_id}' ({len(grant)} principals)"
        )
    except ClientError as e:
        logger.warning(
            f"  ⚠ Grant {kind} perms '{resource_id}': {classify_error(e)['user_message']}"
        )


# ── S3 bucket creation ──────────────────────────────────────────────


def kb_bucket_name(env: str, account_id: str) -> str:
    """knowledge-base-<env>-<account> — must start with knowledge-base- for the IAM policy."""
    return f"knowledge-base-{env}-{account_id}"


def bucket_policy_for_quicksight(
    bucket_name: str, account_id: str, qs_service_role: str
) -> dict:
    role_arn = f"arn:aws:iam::{account_id}:role/service-role/{qs_service_role}"
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowQuick",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                    "s3:GetObjectVersion",
                    "s3:ListBucketVersions",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            }
        ],
    }


# Expected trust policy for the QuickSight service role.
EXPECTED_QS_TRUST = {
    "principal_service": "quicksight.amazonaws.com",
    "actions": {"sts:AssumeRole", "sts:TagSession"},
}


def verify_qs_service_role(
    iam_client, account_id: str, qs_service_role: str, report: dict
) -> bool:
    """
    Preflight: confirm the QuickSight service role exists (via iam:GetRole) and
    that its trust policy allows quicksight.amazonaws.com to assume it.

    Returns True if the role exists and the trust policy looks correct.
    Records a clear error in the report (and returns False) otherwise, so a
    name mismatch or bad trust policy fails loudly instead of producing a
    bucket QuickSight can't read.
    """
    role_name = qs_service_role
    try:
        resp = iam_client.get_role(RoleName=role_name)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchEntity", "NoSuchEntityException"):
            report["errors"].append(
                {
                    "context": f"verify_qs_service_role({role_name})",
                    "user_message": (
                        f"QuickSight service role '{role_name}' not found in account {account_id}. "
                        "Pass the correct qs_service_role (e.g. aws-quicksight-service-role-v0) "
                        "or create it before migrating knowledge bases."
                    ),
                }
            )
        else:
            report["errors"].append(
                format_error_for_report(f"verify_qs_service_role({role_name})", e)
            )
        return False

    # Validate the trust policy.
    role = resp.get("Role", {})
    trust = role.get("AssumeRolePolicyDocument", {})
    # boto3 may return the trust doc URL-encoded as a string
    if isinstance(trust, str):
        try:
            import urllib.parse

            trust = json.loads(urllib.parse.unquote(trust))
        except Exception:
            trust = {}

    services = set()
    actions = set()
    for stmt in trust.get("Statement", []):
        if stmt.get("Effect") != "Allow":
            continue
        principal = stmt.get("Principal", {})
        svc = principal.get("Service")
        if isinstance(svc, str):
            services.add(svc)
        elif isinstance(svc, list):
            services.update(svc)
        act = stmt.get("Action")
        if isinstance(act, str):
            actions.add(act)
        elif isinstance(act, list):
            actions.update(act)

    if EXPECTED_QS_TRUST["principal_service"] not in services:
        report["errors"].append(
            {
                "context": f"verify_qs_service_role({role_name})",
                "user_message": (
                    f"QuickSight service role '{role_name}' trust policy does not allow "
                    f"'{EXPECTED_QS_TRUST['principal_service']}' to assume it "
                    f"(found principals: {sorted(services) or 'none'}). "
                    "QuickSight will not be able to read the knowledge base bucket."
                ),
            }
        )
        return False

    if "sts:AssumeRole" not in actions:
        report["errors"].append(
            {
                "context": f"verify_qs_service_role({role_name})",
                "user_message": (
                    f"QuickSight service role '{role_name}' trust policy is missing 'sts:AssumeRole' "
                    f"(found actions: {sorted(actions) or 'none'})."
                ),
            }
        )
        return False

    missing = EXPECTED_QS_TRUST["actions"] - actions
    if missing:
        # sts:TagSession missing is a warning, not fatal — QuickSight may still work.
        logger.warning(
            f"  ⚠ QuickSight service role '{role_name}' trust policy missing {sorted(missing)} "
            "(non-fatal, but recommended)."
        )

    logger.info(
        f"  ✓ QuickSight service role '{role_name}' verified (trust allows quicksight.amazonaws.com)"
    )
    return True


def create_kb_bucket(s3, bucket_name, region, account_id, qs_service_role, report):
    """Create bucket (idempotent) + attach QuickSight bucket policy."""
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        logger.info(f"  ✓ Bucket created: {bucket_name}")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            logger.info(f"  ℹ Bucket exists: {bucket_name} ({code})")
        else:
            logger.warning(
                f"  ⚠ create_bucket '{bucket_name}': {classify_error(e)['user_message']}"
            )
            report["errors"].append(
                format_error_for_report(f"create_bucket({bucket_name})", e)
            )
    try:
        s3.put_bucket_policy(
            Bucket=bucket_name,
            Policy=json.dumps(
                bucket_policy_for_quicksight(bucket_name, account_id, qs_service_role)
            ),
        )
        logger.info(f"  ✓ Bucket policy attached: {bucket_name}")
    except ClientError as e:
        logger.warning(
            f"  ⚠ put_bucket_policy '{bucket_name}': {classify_error(e)['user_message']}"
        )


# ═══════════════════════════════════════════════════════════════════
# MIGRATION LOGIC
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# SELECTION-BASED MIGRATION (explicit nested resource tree — no discovery)
# ═══════════════════════════════════════════════════════════════════

_TYPE_ALIASES = {
    "kb": "knowledge_base",
    "knowledgebase": "knowledge_base",
    "action_connector": "connector",
    "actionconnector": "connector",
}


def _normalize_type(t):
    t = (t or "").strip().lower()
    return _TYPE_ALIASES.get(t, t)


def _paginate(client, op_name, result_key, **kwargs):
    """Iterate all items for a QuickSight list_* op via NextToken.

    Works whether or not the op supports get_paginator (list_agents does
    NOT). Returns the accumulated list under result_key.
    """
    items, token = [], None
    op = getattr(client, op_name)
    while True:
        if token:
            kwargs["NextToken"] = token
        resp = op(**kwargs)
        items += resp.get(result_key, [])
        token = resp.get("NextToken")
        if not token:
            break
    return items


def do_migrate_resources(
    source_account_id,
    target_account_id,
    resource_type,
    ids,
    region,
    source_env,
    target_env,
    qs_service_role,
) -> dict:
    """Migrate a flat set of resources of a single type from source to target.

    Resource-driven: NO spaces are created, described, or linked. Agents are
    recreated with their Action Connectors attached (exactly as in the source)
    but with an empty Spaces attachment. Connectors and knowledge bases are
    recreated standalone. Permissions are copied by describing the source and
    replaying the identical grants in the target.

    Args:
        resource_type: agent | connector | knowledge_base
        ids: concrete resource IDs (already resolved from id/name/all)
    """
    rtype = _normalize_type(resource_type)
    logger.info("=" * 60)
    logger.info("RESOURCE MIGRATION STARTED")
    logger.info(
        f"  Source: {source_account_id} (env={source_env})  Target: {target_account_id} (env={target_env})"
    )
    logger.info(f"  Type: {rtype}  IDs: {ids}")
    logger.info("=" * 60)

    report = {
        "source_account": source_account_id,
        "target_account": target_account_id,
        "region": region,
        "source_env": source_env,
        "target_env": target_env,
        "resource_type": rtype,
        "migrated": {
            "agents": [],
            "connectors": [],
            "knowledge_bases": [],
            "spaces": [],
            "flows": [],
            "buckets": [],
        },
        "skipped_permissions": [],
        "errors": [],
        "steps": [],
    }

    # — Assume roles —
    try:
        source_qs = assume_role_client("quicksight", SOURCE_ROLE_ARN, region)
    except RuntimeError as e:
        report["errors"].append(
            {"context": "assume_source_role", "user_message": str(e)}
        )
        report["overall_status"] = "FAILED"
        return report
    try:
        target_qs = assume_role_client("quicksight", TARGET_ROLE_ARN, region)
        target_s3 = assume_role_client("s3", TARGET_ROLE_ARN, region)
        target_iam = assume_role_client("iam", TARGET_ROLE_ARN, region)
    except RuntimeError as e:
        report["errors"].append(
            {"context": "assume_target_role", "user_message": str(e)}
        )
        report["overall_status"] = "FAILED"
        return report

    # S3 client for pre-update backups. The backup bucket lives in THIS (runner)
    # account, so use the runtime's own credentials — no assume-role.
    backup_s3 = boto3.client("s3", region_name=region)

    ids = [i for i in ids if i and i.strip()]
    if not ids:
        report["errors"].append(
            {
                "context": "resolve_resources",
                "user_message": f"No {rtype} resources to migrate.",
            }
        )
        report["overall_status"] = "FAILED"
        return report
    report["steps"].append({"step": "selection", "resource_type": rtype, "ids": ids})

    # Upsert semantics: each per-type migrator creates the resource if its id is
    # absent in the target, or updates the SAME resource if the id already
    # exists (a pre-update backup is taken first). No separate skip/create mode.
    created_key = {
        "connector": "connectors",
        "knowledge_base": "knowledge_bases",
        "agent": "agents",
        "space": "spaces",
        "flow": "flows",
    }[rtype]

    if rtype == "connector":
        _migrate_connectors(
            source_qs,
            target_qs,
            backup_s3,
            source_account_id,
            target_account_id,
            region,
            ids,
            report,
        )
    elif rtype == "knowledge_base":
        _migrate_knowledge_bases(
            source_qs,
            target_qs,
            target_s3,
            target_iam,
            backup_s3,
            source_account_id,
            target_account_id,
            region,
            ids,
            target_env,
            qs_service_role,
            report,
        )
    elif rtype == "agent":
        _migrate_agents(
            source_qs,
            target_qs,
            backup_s3,
            source_account_id,
            target_account_id,
            region,
            ids,
            report,
        )
    elif rtype == "space":
        _migrate_spaces(
            source_qs,
            target_qs,
            backup_s3,
            source_account_id,
            target_account_id,
            region,
            ids,
            report,
        )
    elif rtype == "flow":
        _migrate_flows(
            source_qs,
            target_qs,
            backup_s3,
            source_account_id,
            target_account_id,
            region,
            ids,
            report,
        )

    # Post-migration snapshot: record EVERY successfully created/updated target
    # resource to the backup bucket so it shows up in list_backups (creates land
    # as v1). Non-fatal — never affects migration success.
    _snap_id_key = {
        "connector": "connector_id",
        "knowledge_base": "knowledge_base_id",
        "agent": "agent_id",
        "space": "space_id",
        "flow": "flow_id",
    }[rtype]
    for m in report["migrated"][created_key]:
        st = str(m.get("status", ""))
        if "FAILED" in st or "SKIPPED" in st:
            continue
        action = "UPDATED" if "UPDATED" in st else "CREATED"
        # Flows reuse a NEW target id; snapshot that one, not the source id.
        snap_id = m.get("target_flow_id") if rtype == "flow" else m.get(_snap_id_key)
        if not snap_id:
            continue
        snapshot_after_migrate(
            target_qs, backup_s3, target_account_id, rtype, snap_id,
            m.get("name"), action, report,
        )

    any_ok = any(
        "FAILED" not in str(m.get("status", ""))
        and "SKIPPED" not in str(m.get("status", ""))
        for m in report["migrated"][created_key]
    )
    has_errors = len(report["errors"]) > 0
    if not any_ok:
        report["overall_status"] = "FAILED"
    elif has_errors:
        report["overall_status"] = "COMPLETED_WITH_ERRORS"
    else:
        report["overall_status"] = "COMPLETE"

    logger.info("=" * 60)
    logger.info(
        f"RESOURCE MIGRATION {report['overall_status']} — {rtype}: "
        f"{len(report['migrated'][created_key])}  errors: {len(report['errors'])}"
    )
    logger.info("=" * 60)
    return report


def _migrate_connectors(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    connector_ids,
    report,
):
    """Recreate the given connectors in the target + copy permissions (no spaces)."""
    for connector_id in connector_ids:
        try:
            src_cfg = source_qs.describe_action_connector(
                AwsAccountId=source_account_id, ActionConnectorId=connector_id
            )
            connector_data = src_cfg.get("ActionConnector", src_cfg)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_action_connector({connector_id})", e)
            )
            report["migrated"]["connectors"].append(
                {
                    "connector_id": connector_id,
                    "name": connector_id,
                    "type": "UNKNOWN (failed to describe)",
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        ctype = connector_data.get("Type", "GENERIC_HTTP")
        if ctype not in CREATABLE_CONNECTOR_TYPES:
            msg = (
                f"Connector type '{ctype}' cannot be recreated via the QuickSight API "
                f"(create_action_connector does not accept this type). Recreate it "
                f"manually in the target account."
            )
            report["errors"].append(
                {
                    "context": f"create_action_connector({connector_id})",
                    "user_message": msg,
                }
            )
            report["migrated"]["connectors"].append(
                {
                    "connector_id": connector_id,
                    "name": connector_data.get("Name", connector_id),
                    "type": ctype,
                    "status": f"SKIPPED: {msg}",
                }
            )
            continue

        auth = sanitize_auth_config(
            connector_data.get(
                "AuthenticationConfig",
                {
                    "AuthenticationType": "NONE",
                    "AuthenticationMetadata": {
                        "NoneConnectionMetadata": {
                            "BaseEndpoint": "https://example.com"
                        }
                    },
                },
            )
        )
        try:
            target_qs.create_action_connector(
                AwsAccountId=target_account_id,
                ActionConnectorId=connector_id,
                Name=connector_data.get("Name", connector_id),
                Type=ctype,
                AuthenticationConfig=auth,
            )
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs, backup_s3, target_account_id, "connector",
                    connector_id, connector_data.get("Name", connector_id), report,
                ):
                    status = "FAILED: backup before update failed (not modified)"
                    report["migrated"]["connectors"].append(
                        {
                            "connector_id": connector_id,
                            "name": connector_data.get("Name", connector_id),
                            "type": connector_data.get("Type", "UNKNOWN"),
                            "status": status,
                        }
                    )
                    continue
                try:
                    upd = {
                        "AwsAccountId": target_account_id,
                        "ActionConnectorId": connector_id,
                        "Name": connector_data.get("Name", connector_id),
                        "AuthenticationConfig": auth,
                    }
                    if connector_data.get("Description"):
                        upd["Description"] = connector_data["Description"]
                    target_qs.update_action_connector(**upd)
                    status = "UPDATED"
                except ClientError as ue:
                    report["errors"].append(
                        format_error_for_report(
                            f"update_action_connector({connector_id})", ue
                        )
                    )
                    status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                report["errors"].append(
                    format_error_for_report(
                        f"create_action_connector({connector_id})", e
                    )
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        copy_connector_permissions(
            source_qs,
            target_qs,
            source_account_id,
            target_account_id,
            region,
            connector_id,
            report,
        )
        report["migrated"]["connectors"].append(
            {
                "connector_id": connector_id,
                "name": connector_data.get("Name", connector_id),
                "type": connector_data.get("Type", "UNKNOWN"),
                "status": status,
            }
        )


def _migrate_knowledge_bases(
    source_qs,
    target_qs,
    target_s3,
    target_iam,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    kb_ids,
    target_env,
    qs_service_role,
    report,
):
    """Recreate the given knowledge bases (bucket + data source + KB) + copy permissions (no spaces)."""
    target_bucket = kb_bucket_name(target_env, target_account_id)
    if not verify_qs_service_role(
        target_iam, target_account_id, qs_service_role, report
    ):
        logger.error(
            "  ✗ QuickSight service role preflight failed — skipping KB migration."
        )
        report["steps"].append({"step": "kb_service_role_check", "status": "FAILED"})
        return
    create_kb_bucket(
        target_s3, target_bucket, region, target_account_id, qs_service_role, report
    )
    report["migrated"]["buckets"].append(
        {"bucket": target_bucket, "env": target_env, "account": target_account_id}
    )

    for kb_id in kb_ids:
        try:
            kb_detail = source_qs.describe_knowledge_base(
                AwsAccountId=source_account_id, KnowledgeBaseId=kb_id
            ).get("KnowledgeBase", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_knowledge_base({kb_id})", e)
            )
            report["migrated"]["knowledge_bases"].append(
                {
                    "knowledge_base_id": kb_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        kb_name = kb_detail.get("Name", kb_id)
        kb_config = {
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
                        "bucketName": target_bucket,
                        "bucketOwnerAccountId": target_account_id,
                    },
                }
            }
        }

        existing_kb = None
        try:
            existing_kb = target_qs.describe_knowledge_base(
                AwsAccountId=target_account_id, KnowledgeBaseId=kb_id
            ).get("KnowledgeBase")
        except ClientError as e:
            if (
                e.response.get("Error", {}).get("Code", "")
                != "ResourceNotFoundException"
            ):
                logger.warning(
                    f"  ⚠ describe target KB '{kb_id}': {classify_error(e)['user_message']}"
                )

        if existing_kb:
            if not maybe_backup_before_update(
                target_qs, backup_s3, target_account_id, "knowledge_base",
                kb_id, kb_name, report,
            ):
                report["migrated"]["knowledge_bases"].append(
                    {
                        "knowledge_base_id": kb_id,
                        "name": kb_name,
                        "status": "FAILED: backup before update failed (not modified)",
                    }
                )
                continue
            status = "UPDATED"
            existing_ds_arn = existing_kb.get("DataSourceArn", "")
            existing_ds_id = existing_ds_arn.split("/")[-1] if existing_ds_arn else None
            if existing_ds_id:
                try:
                    target_qs.update_data_source(
                        AwsAccountId=target_account_id,
                        DataSourceId=existing_ds_id,
                        Name=f"{kb_name} - datasource",
                        DataSourceParameters={
                            "S3KnowledgeBaseParameters": {
                                "BucketUrl": f"s3://{target_bucket}"
                            }
                        },
                    )
                except ClientError as ue:
                    logger.warning(
                        f"  ⚠ update_data_source(kb {kb_id}): {classify_error(ue)['user_message']}"
                    )
            try:
                target_qs.update_knowledge_base(
                    AwsAccountId=target_account_id,
                    KnowledgeBaseId=kb_id,
                    Name=kb_name,
                    KnowledgeBaseConfiguration=kb_config,
                    MediaExtractionConfiguration=MEDIA_EXTRACTION_CONFIG,
                )
            except ClientError as ue:
                report["errors"].append(
                    format_error_for_report(f"update_knowledge_base({kb_id})", ue)
                )
                status = f"FAILED: {classify_error(ue)['user_message']}"
        else:
            new_ds_id = str(uuid.uuid4())
            ds_arn = None
            try:
                ds_resp = target_qs.create_data_source(
                    AwsAccountId=target_account_id,
                    DataSourceId=new_ds_id,
                    Name=f"{kb_name} - datasource",
                    Type="S3_KNOWLEDGE_BASE",
                    DataSourceParameters={
                        "S3KnowledgeBaseParameters": {
                            "BucketUrl": f"s3://{target_bucket}"
                        }
                    },
                )
                ds_arn = ds_resp.get(
                    "Arn",
                    f"arn:aws:quicksight:{region}:{target_account_id}:datasource/{new_ds_id}",
                )
            except ClientError as e:
                report["errors"].append(
                    format_error_for_report(f"create_data_source(kb {kb_id})", e)
                )
            status = "FAILED: data source not created"
            if ds_arn:
                try:
                    target_qs.create_knowledge_base(
                        AwsAccountId=target_account_id,
                        KnowledgeBaseId=kb_id,
                        Name=kb_name,
                        DataSourceArn=ds_arn,
                        KnowledgeBaseConfiguration=kb_config,
                        MediaExtractionConfiguration=MEDIA_EXTRACTION_CONFIG,
                    )
                    status = "CREATED"
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(f"create_knowledge_base({kb_id})", e)
                    )
                    status = f"FAILED: {classify_error(e)['user_message']}"

        wait_for_kb_active(target_qs, target_account_id, kb_id)
        copy_kb_permissions(
            source_qs,
            target_qs,
            source_account_id,
            target_account_id,
            region,
            kb_id,
            report,
        )
        report["migrated"]["knowledge_bases"].append(
            {
                "knowledge_base_id": kb_id,
                "name": kb_name,
                "status": status,
                "target_bucket": target_bucket,
            }
        )


def _migrate_agents(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    agent_ids,
    report,
):
    """Recreate the given agents in the target + copy permissions.

    Action Connectors referenced by the agent are remapped to the target account
    and attached, exactly as in the source. Space linkage is handled separately
    by the space migration (which re-links its resources), not here.
    """
    for agent_id in agent_ids:
        try:
            agent = source_qs.describe_agent(
                AwsAccountId=source_account_id, AgentId=agent_id
            ).get("Agent", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_agent({agent_id})", e)
            )
            report["migrated"]["agents"].append(
                {
                    "agent_id": agent_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        custom_prompt_input = None
        prompt = agent.get("CustomPromptInterface") or {}
        new_prompt = {
            k: v
            for k, v in {
                "CustomInstructions": prompt.get("CustomInstructions"),
                "Identity": prompt.get("Identity"),
                "Tone": prompt.get("Tone"),
                "OutputStyle": prompt.get("OutputStyle"),
                "ResponseLength": prompt.get("ResponseLength"),
            }.items()
            if v
        }
        if new_prompt:
            custom_prompt_input = {"NewPrompt": new_prompt}

        target_connector_arns = [
            remap_arn(a, target_account_id, region)
            for a in agent.get("ActionConnectors", [])
        ]
        create_params = {
            "AwsAccountId": target_account_id,
            "AgentId": agent_id,
            "Name": agent.get("Name", agent_id),
            "AgentLifecycle": agent.get("AgentLifecycle", "PUBLISHED"),
        }
        if target_connector_arns:
            create_params["ActionConnectors"] = target_connector_arns
        if agent.get("Description"):
            create_params["Description"] = agent["Description"]
        if custom_prompt_input:
            create_params["CustomPromptInput"] = custom_prompt_input
        if agent.get("StarterPrompts"):
            create_params["StarterPrompts"] = agent["StarterPrompts"]
        if agent.get("WelcomeMessage"):
            create_params["WelcomeMessage"] = agent["WelcomeMessage"]
        if agent.get("IconId"):
            create_params["IconId"] = agent["IconId"]

        try:
            target_qs.create_agent(**create_params)
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs, backup_s3, target_account_id, "agent",
                    agent_id, agent.get("Name", agent_id), report,
                ):
                    report["migrated"]["agents"].append(
                        {
                            "agent_id": agent_id,
                            "name": agent.get("Name", agent_id),
                            "status": "FAILED: backup before update failed (not modified)",
                            "connectors": [
                                a.split("/")[-1] for a in agent.get("ActionConnectors", [])
                            ],
                        }
                    )
                    continue
                try:
                    wait_for_active(target_qs, target_account_id, agent_id)
                    existing_connectors = set()
                    try:
                        cur = target_qs.describe_agent(
                            AwsAccountId=target_account_id, AgentId=agent_id
                        ).get("Agent", {})
                        existing_connectors = set(cur.get("ActionConnectors", []) or [])
                    except ClientError:
                        pass
                    upd_params = {
                        "AwsAccountId": target_account_id,
                        "AgentId": agent_id,
                        "Name": agent.get("Name", agent_id),
                    }
                    if agent.get("Description"):
                        upd_params["Description"] = agent["Description"]
                    if custom_prompt_input:
                        upd_params["CustomPromptInput"] = custom_prompt_input
                    if agent.get("StarterPrompts"):
                        upd_params["StarterPrompts"] = agent["StarterPrompts"]
                    if agent.get("WelcomeMessage"):
                        upd_params["WelcomeMessage"] = agent["WelcomeMessage"]
                    if agent.get("IconId"):
                        upd_params["IconId"] = agent["IconId"]
                    connectors_to_add = [
                        a for a in target_connector_arns if a not in existing_connectors
                    ]
                    if connectors_to_add:
                        upd_params["ActionConnectorsToAdd"] = connectors_to_add
                    target_qs.update_agent(**upd_params)
                    status = "UPDATED"
                except ClientError as ue:
                    ucode = ue.response.get("Error", {}).get("Code", "")
                    if ucode == "ConflictException":
                        status = "SKIPPED_UPDATING (agent busy — retry later)"
                        logger.warning(
                            f"  ⚠ update_agent '{agent_id}' conflict: agent is UPDATING"
                        )
                    else:
                        report["errors"].append(
                            format_error_for_report(f"update_agent({agent_id})", ue)
                        )
                        status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                report["errors"].append(
                    format_error_for_report(f"create_agent({agent_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        if "FAILED" not in status:
            wait_for_active(target_qs, target_account_id, agent_id)
            copy_agent_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                agent_id,
                report,
            )

        report["migrated"]["agents"].append(
            {
                "agent_id": agent_id,
                "name": agent.get("Name", agent_id),
                "status": status,
                "connectors": [
                    a.split("/")[-1] for a in agent.get("ActionConnectors", [])
                ],
            }
        )


def _migrate_spaces(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    space_ids,
    report,
):
    """Recreate the given spaces in the target, re-link their resources, copy permissions.

    Each space's linked resources (agents, connectors, knowledge bases) are
    referenced by ARN; those ARNs are remapped to the target account before the
    space is re-linked. The linked resources themselves must already be migrated
    for the target ARNs to resolve.
    """
    for space_id in space_ids:
        try:
            space = source_qs.describe_space(
                AwsAccountId=source_account_id, SpaceId=space_id
            ).get("Space", {})
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_space({space_id})", e)
            )
            report["migrated"]["spaces"].append(
                {
                    "space_id": space_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        space_name = space.get("name", space_id)
        create_params = {
            "AwsAccountId": target_account_id,
            "SpaceId": space_id,
            "Name": space_name,
        }
        if space.get("description"):
            create_params["Description"] = space["description"]

        try:
            target_qs.create_space(**create_params)
            status = "CREATED"
        except ClientError as e:
            if e.response.get("Error", {}).get("Code", "") == "ResourceExistsException":
                if not maybe_backup_before_update(
                    target_qs, backup_s3, target_account_id, "space",
                    space_id, space_name, report,
                ):
                    status = "FAILED: backup before update failed (not modified)"
                else:
                    try:
                        upd = {
                            "AwsAccountId": target_account_id,
                            "SpaceId": space_id,
                            "Name": space_name,
                        }
                        if space.get("description"):
                            upd["Description"] = space["description"]
                        target_qs.update_space(**upd)
                        status = "UPDATED"
                    except ClientError as ue:
                        report["errors"].append(
                            format_error_for_report(f"update_space({space_id})", ue)
                        )
                        status = f"FAILED: {classify_error(ue)['user_message']}"
            else:
                report["errors"].append(
                    format_error_for_report(f"create_space({space_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        # Re-link the space's resources, remapping each ARN to the target account.
        linked = []
        if "FAILED" not in status:
            add_resources = []
            for res in space.get("resources", []) or []:
                src_arn = (res.get("resourceDetails") or {}).get("resourceArn")
                if not src_arn:
                    continue
                add_resources.append(
                    {
                        "ResourceType": res.get("resourceType"),
                        "ResourceDetails": {
                            "resourceArn": remap_arn(src_arn, target_account_id, region)
                        },
                    }
                )
                linked.append(src_arn.split("/")[-1])
            if add_resources:
                try:
                    target_qs.update_space_resources(
                        AwsAccountId=target_account_id,
                        SpaceId=space_id,
                        AddResources=add_resources,
                    )
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(
                            f"update_space_resources({space_id})", e
                        )
                    )

            copy_space_permissions(
                source_qs,
                target_qs,
                source_account_id,
                target_account_id,
                region,
                space_id,
                report,
            )

        report["migrated"]["spaces"].append(
            {
                "space_id": space_id,
                "name": space_name,
                "status": status,
                "linked_resources": linked,
            }
        )


def _describe_flow(qs, account_id, flow_id):
    """Describe a flow. DescribeFlow REQUIRES PublishState; we always use the
    PUBLISHED version (that's what gets migrated/backed up). Returns the Flow
    dict."""
    resp = qs.describe_flow(
        AwsAccountId=account_id, FlowId=flow_id, PublishState="PUBLISHED"
    )
    return resp.get("Flow", {})


def _find_target_flows_by_name(target_qs, target_account_id, name):
    """Return the list of target FlowIds whose name matches (case-insensitive).

    Flows are matched by NAME (not id) because CreateFlow mints a new FlowId in
    the target — the source id can never be reused there.
    """
    want = (name or "").strip().lower()
    matches = []
    token = None
    while True:
        kwargs = {"AwsAccountId": target_account_id}
        if token:
            kwargs["NextToken"] = token
        resp = target_qs.list_flows(**kwargs)
        for s in resp.get("FlowSummaryList", []) or []:
            if (s.get("Name", "") or "").strip().lower() == want:
                fid = s.get("FlowId")
                if fid:
                    matches.append(fid)
        token = resp.get("NextToken")
        if not token:
            break
    return matches


def _migrate_flows(
    source_qs,
    target_qs,
    backup_s3,
    source_account_id,
    target_account_id,
    region,
    flow_ids,
    report,
):
    """Migrate flows. Unlike the other types, flows are matched by NAME because
    CreateFlow does not accept a client-supplied FlowId (the target assigns a
    new one). If a target flow with the same name exists it is updated (after a
    pre-update backup); otherwise a new flow is created. Duplicate names in the
    target are treated as ambiguous and skipped (no mutation)."""
    for flow_id in flow_ids:
        try:
            flow = _describe_flow(source_qs, source_account_id, flow_id)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"describe_flow({flow_id})", e)
            )
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "status": f"FAILED: {classify_error(e)['user_message']}",
                }
            )
            continue

        name = flow.get("Name", flow_id)
        definition = flow.get("FlowDefinition")
        description = flow.get("Description")
        if not definition:
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "name": name,
                    "status": "FAILED: source flow has no FlowDefinition to copy",
                }
            )
            continue

        # Match by name in the target.
        try:
            existing = _find_target_flows_by_name(target_qs, target_account_id, name)
        except ClientError as e:
            report["errors"].append(
                format_error_for_report(f"list_flows(match {name!r})", e)
            )
            report["migrated"]["flows"].append(
                {"flow_id": flow_id, "name": name,
                 "status": f"FAILED: {classify_error(e)['user_message']}"}
            )
            continue

        if len(existing) > 1:
            report["migrated"]["flows"].append(
                {
                    "flow_id": flow_id,
                    "name": name,
                    "status": (
                        f"SKIPPED: {len(existing)} target flows already named "
                        f"{name!r} — ambiguous, not modified"
                    ),
                }
            )
            continue

        target_flow_id = None
        if len(existing) == 1:
            # UPDATE the existing target flow (back it up first).
            target_flow_id = existing[0]
            if not maybe_backup_before_update(
                target_qs, backup_s3, target_account_id, "flow",
                target_flow_id, name, report,
            ):
                report["migrated"]["flows"].append(
                    {
                        "flow_id": flow_id,
                        "target_flow_id": target_flow_id,
                        "name": name,
                        "status": "FAILED: backup before update failed (not modified)",
                    }
                )
                continue
            try:
                upd = {
                    "AwsAccountId": target_account_id,
                    "FlowId": target_flow_id,
                    "Name": name,
                    "FlowDefinition": definition,
                }
                if description:
                    upd["Description"] = description
                target_qs.update_flow(**upd)
                status = "UPDATED"
            except ClientError as e:
                report["errors"].append(
                    format_error_for_report(f"update_flow({target_flow_id})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"
        else:
            # CREATE a new target flow (target assigns the id).
            try:
                params = {
                    "AwsAccountId": target_account_id,
                    "Name": name,
                    "FlowDefinition": definition,
                }
                if description:
                    params["Description"] = description
                resp = target_qs.create_flow(**params)
                target_flow_id = resp.get("FlowId")
                status = "CREATED"
            except ClientError as e:
                report["errors"].append(
                    format_error_for_report(f"create_flow({name})", e)
                )
                status = f"FAILED: {classify_error(e)['user_message']}"

        if "FAILED" not in status and target_flow_id:
            copy_flow_permissions(
                source_qs, target_qs, source_account_id, target_account_id,
                region, flow_id, target_flow_id, report,
            )

        report["migrated"]["flows"].append(
            {
                "flow_id": flow_id,
                "target_flow_id": target_flow_id,
                "name": name,
                "status": status,
                "matched_by": "name",
            }
        )


# ═══════════════════════════════════════════════════════════════════
# RESOURCE RESOLUTION (resource_type + id | name | all)
# ═══════════════════════════════════════════════════════════════════

# The resource types this server can migrate.
_MIGRATABLE_TYPES = {"agent", "connector", "knowledge_base", "space", "flow"}

# Per-type QuickSight list op, its result key, the id field, and the name field.
_RESOURCE_LISTERS = {
    "agent": ("list_agents", "AgentSummaries", "AgentId", "Name"),
    "connector": (
        "list_action_connectors",
        "ActionConnectorSummaries",
        "ActionConnectorId",
        "Name",
    ),
    "knowledge_base": (
        "list_knowledge_bases",
        "KnowledgeBaseSummaries",
        "KnowledgeBaseId",
        "Name",
    ),
    # ListSpaces returns SpaceSummaries with lowercase spaceId / name fields.
    "space": ("list_spaces", "SpaceSummaries", "spaceId", "name"),
    # ListFlows returns FlowSummaryList; ids are FlowId, names are Name.
    "flow": ("list_flows", "FlowSummaryList", "FlowId", "Name"),
}


def _summary_id(summary, id_field):
    """Return a resource id from a list_* summary, falling back to the ARN tail."""
    return summary.get(id_field) or (summary.get("Arn", "").split("/")[-1] or None)


# Per-type describe op + response key, used to check if a resource already
# exists in the target account (matched by the same id the migrator reuses).
_RESOURCE_DESCRIBERS = {
    "agent": ("describe_agent", "AgentId"),
    "connector": ("describe_action_connector", "ActionConnectorId"),
    "knowledge_base": ("describe_knowledge_base", "KnowledgeBaseId"),
    "space": ("describe_space", "SpaceId"),
    "flow": ("describe_flow", "FlowId"),
}


def target_resource_exists(target_qs, target_account_id, rtype, resource_id):
    """Return (exists, error) for a resource id in the target account.

    The migrator reuses the source id in the target, so existence is a direct
    describe_* by that id. A ResourceNotFoundException means "does not exist"
    (exists=False, no error). Any other ClientError is surfaced as an error so
    the caller can decide (we do NOT silently treat it as absent, which could
    cause an unwanted create).
    """
    op_name, id_kwarg = _RESOURCE_DESCRIBERS[rtype]
    op = getattr(target_qs, op_name)
    try:
        op(**{"AwsAccountId": target_account_id, id_kwarg: resource_id})
        return True, None
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            return False, None
        return None, classify_error(e)["user_message"]


# Response envelope + object key per type, for describing a matched target asset.
_RESOURCE_DESCRIBE_KEYS = {
    "agent": ("describe_agent", "AgentId", "Agent"),
    "connector": ("describe_action_connector", "ActionConnectorId", "ActionConnector"),
    "knowledge_base": ("describe_knowledge_base", "KnowledgeBaseId", "KnowledgeBase"),
    "space": ("describe_space", "SpaceId", "Space"),
    "flow": ("describe_flow", "FlowId", "Flow"),
}

# Per-type describe-*-permissions op + id kwarg (permissions are read from both
# source and target so the FE can display/compare access per resource).
_RESOURCE_PERMISSION_DESCRIBERS = {
    "agent": ("describe_agent_permissions", "AgentId"),
    "connector": ("describe_action_connector_permissions", "ActionConnectorId"),
    "knowledge_base": ("describe_knowledge_base_permissions", "KnowledgeBaseId"),
    "space": ("describe_space_permissions", "SpaceId"),
    # Flows read permissions via get_flow_permissions (not describe_*).
    "flow": ("get_flow_permissions", "FlowId"),
}


def describe_resource_permissions(qs, account_id, rtype, resource_id):
    """Return (permissions, error) for a resource's QuickSight permissions.

    permissions is a normalized list of {"principal": <arn>, "actions": [...]}
    (empty list if none). error is a user-facing string if the read failed
    (permissions is [] in that case). Read-only.
    """
    op_name, id_kwarg = _RESOURCE_PERMISSION_DESCRIBERS[rtype]
    op = getattr(qs, op_name)
    try:
        raw = op(**{"AwsAccountId": account_id, id_kwarg: resource_id}).get(
            "Permissions", []
        )
    except ClientError as e:
        return [], classify_error(e)["user_message"]
    normalized = [
        {
            "principal": p.get("Principal"),
            "actions": p.get("Actions", []),
        }
        for p in (raw or [])
    ]
    return normalized, None


def describe_target_resource(target_qs, target_account_id, rtype, resource_id):
    """Return (obj, exists, error) for a matched target resource.

    Only ever called for ids that already exist in the SOURCE — the target is
    never enumerated, and target-only assets are never fetched (migration scope
    is source→target). obj is a compact dict describing the target resource, or
    None when it does not exist (exists=False) or the lookup failed (error set).
    """
    op_name, id_kwarg, obj_key = _RESOURCE_DESCRIBE_KEYS[rtype]
    op = getattr(target_qs, op_name)
    # DescribeFlow requires PublishState; describe others take just the id.
    call_kwargs = {"AwsAccountId": target_account_id, id_kwarg: resource_id}
    if rtype == "flow":
        call_kwargs["PublishState"] = "PUBLISHED"
    try:
        raw = op(**call_kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            return None, False, None
        return None, None, classify_error(e)["user_message"]

    data = raw.get(obj_key, {}) or {}
    # Compact, FE-friendly shape (mirrors the source inventory fields).
    if rtype == "agent":
        obj = {
            "agent_id": data.get("AgentId", resource_id),
            "arn": data.get("Arn"),
            "name": data.get("Name"),
            "agent_status": data.get("AgentStatus"),
        }
    elif rtype == "connector":
        obj = {
            "connector_id": data.get("ActionConnectorId", resource_id),
            "arn": data.get("Arn"),
            "name": data.get("Name"),
            "type": data.get("Type", "UNKNOWN"),
            "status": data.get("Status"),
        }
    elif rtype == "knowledge_base":
        obj = {
            "knowledge_base_id": resource_id,
            "name": data.get("Name", resource_id),
            "type": data.get("Type", "UNKNOWN"),
            "status": data.get("Status", "UNKNOWN"),
        }
    else:  # space
        obj = {
            "space_id": resource_id,
            "name": data.get("name", resource_id),
            "description": data.get("description"),
        }
    return obj, True, None


# QuickSight resource-id pattern (shared by agents, connectors, knowledge bases).
_ID_PATTERN = re.compile(r"^[0-9a-zA-Z\-_=.+]+$")

# JSON keys a caller might wrap an id in, e.g. {"knowledge_base_id": "..."}.
_ID_KEYS = (
    "knowledge_base_id",
    "KnowledgeBaseId",
    "connector_id",
    "ActionConnectorId",
    "agent_id",
    "AgentId",
    "space_id",
    "spaceId",
    "resource_id",
    "id",
    "Id",
    "value",
)


def _extract_id_from_obj(obj):
    """Pull a bare id string out of a dict that wraps one under a known key."""
    for key in _ID_KEYS:
        if key in obj and isinstance(obj[key], str) and obj[key].strip():
            return obj[key].strip()
    return None


def _coerce_selection_value(value):
    """Normalize a search_by=id 'value' into a list of bare resource ids.

    A well-behaved caller passes a plain id (or comma-separated ids). Some
    callers instead pass a JSON object like {"knowledge_base_id": "<uuid>"},
    a JSON array, or a JSON-encoded string. Accept all of these, extract the
    bare id(s), and validate them against the QuickSight id pattern so a
    malformed selection fails with a clear message instead of being sent to
    the API verbatim (which surfaces as a confusing ValidationException).

    Returns (ids, error): ids is a list of validated id strings; error is a
    user-facing string or None.
    """
    raw = (value or "").strip()
    if not raw:
        return [], "search_by='id' requires a non-empty 'value'."

    candidates = []

    # Try to interpret the value as JSON (object, array, or quoted string).
    parsed = None
    if raw[0] in "[{\"":
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None

    if isinstance(parsed, dict):
        extracted = _extract_id_from_obj(parsed)
        if not extracted:
            return [], (
                f"Could not find a resource id in the provided object {raw!r}. "
                f"Pass a bare id, or an object with one of: "
                f"knowledge_base_id, connector_id, agent_id, space_id, id."
            )
        candidates = [extracted]
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, str):
                candidates.append(item.strip())
            elif isinstance(item, dict):
                extracted = _extract_id_from_obj(item)
                if extracted:
                    candidates.append(extracted)
    elif isinstance(parsed, str):
        # JSON-encoded string, e.g. "\"abc-123\"" -> abc-123.
        candidates = [parsed.strip()]
    else:
        # Plain (non-JSON) input: allow a comma-separated list of ids.
        candidates = [part.strip() for part in raw.split(",")]

    candidates = [c for c in candidates if c]
    if not candidates:
        return [], f"No usable resource id found in 'value' {raw!r}."

    invalid = [c for c in candidates if not _ID_PATTERN.match(c)]
    if invalid:
        return [], (
            f"Invalid resource id(s) {invalid!r}: an id must match "
            f"[0-9a-zA-Z-_=.+]+ (no braces, quotes, or spaces). "
            f"Pass the bare resource id, not a wrapped/JSON value."
        )

    # De-duplicate while preserving order.
    seen = set()
    ids = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ids.append(c)
    return ids, None


class BackupError(Exception):
    """Raised when a pre-update backup cannot be written (blocks the update)."""


def _safe_folder_component(name: str) -> str:
    """Make an asset name safe for use as a single S3 key path segment.

    Removes characters awkward in S3 keys — notably '/' (which would create
    phantom subfolders) and control chars — and collapses whitespace. Keeps it
    human-readable (spaces and parentheses are fine in S3 keys).
    """
    name = (name or "").strip()
    # Drop control characters and forward slashes / backslashes.
    cleaned = "".join(
        c for c in name if c not in "/\\" and (ord(c) >= 32)
    ).strip()
    return cleaned or "unnamed"


def _next_backup_version(s3, bucket, prefix, ext):
    """Return the next version integer N for versioned backup objects under the
    asset folder. Matches both the current filename form '<asset_id>_v<N>.<ext>'
    and the legacy 'json_v<N>.<ext>' so version numbers keep incrementing across
    the rename. Returns max(N)+1 (1 if none).
    """
    highest = 0
    token = None
    # Any '<something>_v<N>.<ext>' at the end of the key (covers <id>_v.. + json_v..).
    pat = re.compile(rf"_v(\d+)\.{re.escape(ext)}$")
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            m = pat.search(obj["Key"])
            if m:
                highest = max(highest, int(m.group(1)))
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return highest + 1


def backup_target_resource(s3, bucket, rtype, resource_id, name, described_obj):
    """Write a pre-update snapshot of a target resource to the backup bucket.

    Layout:  "<Asset Name> (asset_id)/json_v<N>.json"  (N auto-increments).
    Returns the s3 key written. Raises BackupError if the backup cannot be
    written — callers MUST treat that as "do not proceed with the update".
    """
    if not bucket:
        raise BackupError(
            "no backup bucket is configured (BACKUP_BUCKET is empty); refusing to "
            "update without a backup"
        )
    folder = f"{_safe_folder_component(name or resource_id)} ({resource_id})"
    prefix = f"{folder}/"
    try:
        version = _next_backup_version(s3, bucket, prefix, "json")
        # Filename carries the asset id: "<asset_id>_v<N>.json".
        key = f"{prefix}{resource_id}_v{version}.json"
        body = json.dumps(described_obj, indent=2, default=str).encode("utf-8")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        logger.info(f"  ✓ Backup written: s3://{bucket}/{key}")
        return key
    except ClientError as e:
        raise BackupError(classify_error(e)["user_message"]) from e


BACKUP_SCHEMA_VERSION = 2  # v2 = dependency-capturing envelope


# ── Backup catalog + restore helpers ────────────────────────────────
_BACKUP_FOLDER_RE = re.compile(r"^(?P<name>.*) \((?P<id>[^()/]+)\)$")


def _parse_backup_folder(folder: str):
    """Split '<Name> (asset_id)' → (name, asset_id), or (None, None)."""
    m = _BACKUP_FOLDER_RE.match(folder)
    if not m:
        return None, None
    return m.group("name"), m.group("id")


def list_backup_catalog(s3, bucket, query=""):
    """List backup assets (optionally filtered by name/id substring) and their
    versions. Returns [{name, asset_id, folder, versions:[{version,key,
    last_modified,size}]}]. Read-only."""
    if not bucket:
        return [], "no backup bucket configured (BACKUP_BUCKET is empty)"
    q = (query or "").strip().lower()
    assets = {}  # folder -> {name, asset_id, versions:[]}
    # Matches both '<asset_id>_v<N>.json' (current) and 'json_v<N>.json' (legacy).
    ver_re = re.compile(r"_v(\d+)\.json$")
    token = None
    try:
        while True:
            kwargs = {"Bucket": bucket}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                key = obj["Key"]
                if "/" not in key:
                    continue
                folder, fname = key.rsplit("/", 1)
                m = ver_re.search(fname)
                if not m:
                    continue
                name, asset_id = _parse_backup_folder(folder)
                if asset_id is None:
                    continue
                # Filter by name or id substring.
                if q and q not in (name or "").lower() and q not in asset_id.lower():
                    continue
                a = assets.setdefault(
                    folder, {"name": name, "asset_id": asset_id, "folder": folder, "versions": []}
                )
                a["versions"].append(
                    {
                        "version": int(m.group(1)),
                        "key": key,
                        "last_modified": obj.get("LastModified"),
                        "size": obj.get("Size"),
                    }
                )
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
    except ClientError as e:
        return [], classify_error(e)["user_message"]

    out = list(assets.values())
    for a in out:
        a["versions"].sort(key=lambda v: v["version"])
        a["latest_version"] = a["versions"][-1]["version"] if a["versions"] else None
    out.sort(key=lambda a: (a["name"] or "").lower())
    return out, None


def read_backup_envelope(s3, bucket, key):
    """Read + parse a backup object into its envelope dict. Read-only."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"].read()
    return json.loads(body)


def _collect_dependencies(target_qs, target_account_id, rtype, resource_obj):
    """Best-effort snapshot of a resource's dependencies (for restore context).

    Returns a dict describing what the resource links to. We capture the
    reference (arn/id) and, where cheap and safe, a describe of the dependency.
    Failures are non-fatal — a dependency we cannot read is recorded with an
    "error" note rather than aborting the backup.
    """
    deps = {}
    try:
        if rtype == "agent":
            # Agents reference action connectors by ARN.
            conn_arns = resource_obj.get("ActionConnectors", []) or []
            connectors = []
            for arn in conn_arns:
                cid = arn.split("/")[-1]
                entry = {"action_connector_id": cid, "arn": arn}
                try:
                    c = target_qs.describe_action_connector(
                        AwsAccountId=target_account_id, ActionConnectorId=cid
                    ).get("ActionConnector", {})
                    entry["name"] = c.get("Name")
                    entry["type"] = c.get("Type")
                except ClientError as e:
                    entry["error"] = classify_error(e)["user_message"]
                connectors.append(entry)
            deps["action_connectors"] = connectors

        elif rtype == "space":
            # Spaces link agents / connectors / KBs / flows by ARN.
            linked = []
            for r in resource_obj.get("resources", []) or []:
                arn = (r.get("resourceDetails") or {}).get("resourceArn")
                linked.append(
                    {
                        "resource_type": r.get("resourceType"),
                        "resource_arn": arn,
                        "resource_id": arn.split("/")[-1] if arn else None,
                    }
                )
            deps["linked_resources"] = linked

        elif rtype == "knowledge_base":
            # KBs reference a data source by ARN.
            ds_arn = resource_obj.get("DataSourceArn", "")
            entry = {"data_source_arn": ds_arn or None}
            if ds_arn:
                ds_id = ds_arn.split("/")[-1]
                entry["data_source_id"] = ds_id
            deps["data_source"] = entry

        elif rtype == "flow":
            # A flow's FlowDefinition is self-contained but may embed resource
            # ARNs; store the definition itself so restore can replay it.
            deps["flow_definition_present"] = bool(resource_obj.get("FlowDefinition"))

        # connectors have no outbound dependencies to capture.
    except Exception as exc:  # never let dependency capture break the backup
        deps["_capture_error"] = str(exc)
    return deps


def _build_backup_envelope(target_qs, target_account_id, rtype, resource_id, name):
    """Describe the target resource + capture its dependencies into an envelope.

    Envelope (schema v2):
      { schema_version, backed_up_at, account_id, resource_type, resource_id,
        name, resource: <full describe>, dependencies: {...} }
    """
    op_name, id_kwarg, obj_key = _RESOURCE_DESCRIBE_KEYS[rtype]
    op = getattr(target_qs, op_name)
    call_kwargs = {"AwsAccountId": target_account_id, id_kwarg: resource_id}
    if rtype == "flow":
        call_kwargs["PublishState"] = "PUBLISHED"  # DescribeFlow requires it
    raw = op(**call_kwargs)
    resource_obj = raw.get(obj_key, raw)
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "account_id": target_account_id,
        "resource_type": rtype,
        "resource_id": resource_id,
        "name": name,
        "resource": resource_obj,
        "dependencies": _collect_dependencies(
            target_qs, target_account_id, rtype, resource_obj
        ),
    }


def snapshot_after_migrate(
    target_qs, s3_backup, target_account_id, rtype, resource_id, name,
    action, report
):
    """Write a post-migration snapshot of the target resource to the backup
    bucket, so EVERY successfully migrated asset (create OR update) is listed by
    list_backups. Non-fatal: a snapshot failure never fails the migration — it
    is only recorded in the report.

    action: "CREATED" | "UPDATED" — stamped into the envelope for the timeline.
    """
    if not BACKUP_BUCKET or not resource_id:
        return
    try:
        envelope = _build_backup_envelope(
            target_qs, target_account_id, rtype, resource_id, name
        )
        envelope["migration_action"] = action
        key = backup_target_resource(
            s3_backup, BACKUP_BUCKET, rtype, resource_id, name, envelope
        )
        report.setdefault("snapshots", []).append(
            {"resource_id": resource_id, "s3_key": key, "action": action}
        )
    except (ClientError, BackupError) as e:
        # Snapshot is best-effort — the migration already succeeded.
        logger.warning(
            f"  ⚠ post-migration snapshot failed for {rtype} '{name or resource_id}' "
            f"({resource_id}): {e}"
        )
        report.setdefault("snapshot_errors", []).append(
            {"resource_id": resource_id, "error": str(e)}
        )


def maybe_backup_before_update(
    target_qs, s3_backup, target_account_id, rtype, resource_id, name, report
):
    """Snapshot the current target resource (+ dependencies) before an update.

    Returns True if it is safe to proceed with the update (backup written), or
    False if the backup failed — in which case a meaningful error is recorded
    in the report and the caller MUST skip the update (fail-safe).
    """
    try:
        envelope = _build_backup_envelope(
            target_qs, target_account_id, rtype, resource_id, name
        )
    except ClientError as e:
        msg = (
            f"Unable to take a backup before updating {rtype} '{name or resource_id}' "
            f"({resource_id}): could not read the current target resource "
            f"({classify_error(e)['user_message']}). The resource was NOT modified."
        )
        report["errors"].append({"context": f"backup({resource_id})", "user_message": msg})
        return False
    try:
        key = backup_target_resource(
            s3_backup, BACKUP_BUCKET, rtype, resource_id, name, envelope
        )
        report.setdefault("backups", []).append(
            {"resource_id": resource_id, "s3_key": key, "bucket": BACKUP_BUCKET}
        )
        return True
    except BackupError as e:
        msg = (
            f"Unable to take a backup before updating {rtype} '{name or resource_id}' "
            f"({resource_id}): {e}. The resource was NOT modified. "
            f"Fix the backup bucket/permissions and retry."
        )
        report["errors"].append({"context": f"backup({resource_id})", "user_message": msg})
        return False


def resolve_resource_ids(qs, account_id, resource_type, search_by, value):
    """Resolve a (resource_type, search_by, value) selection to concrete IDs.

    Args:
        qs: a QuickSight client (already assumed into the source account).
        account_id: source AWS account ID.
        resource_type: one of agent | connector | knowledge_base.
        search_by: one of id | name | all.
            - "all":  every resource of this type in the account.
            - "id":   the single resource whose id == value.
            - "name": every resource whose Name matches value (case-insensitive).
        value: the id or name to match; ignored when search_by == "all".

    Returns:
        (ids, error) — ids is a list of resource IDs (possibly empty); error is
        a user-facing string or None. Raises nothing for "not found" — that is
        surfaced via an empty list + error message.
    """
    rtype = _normalize_type(resource_type)
    if rtype not in _MIGRATABLE_TYPES:
        return [], (
            f"Invalid resource_type {resource_type!r}. "
            f"Valid: agent, connector, knowledge_base."
        )
    mode = (search_by or "all").strip().lower()
    if mode not in {"id", "name", "all"}:
        return [], f"Invalid search_by {search_by!r}. Valid: id, name, all."
    if mode in {"id", "name"} and not (value or "").strip():
        return [], f"search_by={mode!r} requires a non-empty 'value'."

    op_name, result_key, id_field, name_field = _RESOURCE_LISTERS[rtype]

    # search_by=id does not need a full listing — normalize the value into
    # bare id(s) (tolerating JSON/wrapped/comma-separated input) and validate
    # them, then let the downstream describe/create surface a not-found error
    # if an id is well-formed but bogus.
    if mode == "id":
        return _coerce_selection_value(value)

    try:
        summaries = _paginate(qs, op_name, result_key, AwsAccountId=account_id)
    except ClientError as e:
        return [], classify_error(e)["user_message"]

    if mode == "all":
        ids = [i for i in (_summary_id(s, id_field) for s in summaries) if i]
        return ids, None

    # mode == "name": case-insensitive exact match on the Name field.
    want = value.strip().lower()
    ids = [
        _summary_id(s, id_field)
        for s in summaries
        if (s.get(name_field, "") or "").strip().lower() == want
    ]
    ids = [i for i in ids if i]
    if not ids:
        return [], f"No {rtype} found with name {value!r} in account {account_id}."
    return ids, None


# ═══════════════════════════════════════════════════════════════════
# MCP SERVER
# ═══════════════════════════════════════════════════════════════════

# Bind host. Amazon Bedrock AgentCore delivers requests to the container from
# outside its loopback interface, so in the runtime the server must listen on
# all interfaces to receive them. AgentCore supplies BIND_HOST=0.0.0.0 in the
# container environment; locally the server defaults to loopback. Binding to
# all interfaces is not a public exposure here: the container runs behind
# AgentCore's managed ingress, inbound is gated by the Cognito JWT authorizer,
# and the runtime operates in VPC network mode (private subnets).
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")

mcp = FastMCP("quick-resource-migrator", host=BIND_HOST, stateless_http=True)


@mcp.tool()
def migrate_resources(
    source_account_id: str,
    target_account_id: str,
    resource_type: str,
    search_by: str = "all",
    value: str = "",
    region: str = "us-east-1",
    source_env: str = "dev",
    target_env: str = "prod",
    qs_service_role: str = DEFAULT_QS_SERVICE_ROLE,
) -> str:
    """
    Migrate Quick resources (Agents, Action Connectors, S3 Knowledge Bases, or
    Spaces) from a source account to a target account.

    Selection model:
      resource_type: which kind of resource to migrate — one of
                     agent | connector | knowledge_base | space.
      search_by:     how to select within that type — one of:
                       "all"  → every resource of this type in the account
                       "id"   → the single resource whose id == value
                       "name" → all resources whose name matches value (case-insensitive)
      value:         the id or name when search_by is id/name; ignored for "all".

    Behavior:
      - Agents are recreated with their Action Connectors attached (remapped to
        the target account).
      - Spaces are recreated and re-linked to their resources (agents,
        connectors, knowledge bases) with ARNs remapped to the target account;
        migrate those linked resources first so the target ARNs resolve.
      - Connectors are recreated with sanitized (placeholder-secret) auth config
        and must be re-authenticated in the target UI.
      - Knowledge bases provision the target bucket + data source + KB
        (documents are NOT copied). Requires a valid QuickSight service role.
      - Permissions are copied by describing the source and replaying the exact
        grants, with principals resolved to registered target users.

    Args:
        source_account_id: 12-digit source AWS account ID
        target_account_id: 12-digit target AWS account ID
        resource_type: agent | connector | knowledge_base | space
        search_by: id | name | all (default: all)
        value: id or name to match (required when search_by is id or name)
        region: AWS region (default: us-east-1)
        source_env: env name used in the source KB bucket (knowledge-base-<env>-<account>)
        target_env: env name used in the target KB bucket (knowledge-base-<env>-<account>)
        qs_service_role: QuickSight service role name for the S3 bucket policy

    Behavior on conflict:
        Upsert — if a resource's id is not present in the target it is created;
        if it already exists it is updated in place (a pre-update backup of the
        target resource is written to the backup bucket first; if that backup
        cannot be written, the update is aborted for that resource).

    Returns:
        JSON migration report with migrated agents/connectors/knowledge_bases,
        buckets, skipped_permissions, and errors.
    """
    logger.info(
        f"[TOOL] migrate_resources: source={source_account_id}({source_env}) "
        f"target={target_account_id}({target_env}) type={resource_type} "
        f"search_by={search_by} value={value!r}"
    )
    try:
        source_qs = assume_role_client("quicksight", SOURCE_ROLE_ARN, region)
    except RuntimeError as e:
        return json.dumps(
            {"overall_status": "FAILED", "user_message": f"Migration failed: {str(e)}"},
            indent=2,
        )

    ids, err = resolve_resource_ids(
        source_qs, source_account_id, resource_type, search_by, value
    )
    if err and not ids:
        return json.dumps({"overall_status": "FAILED", "user_message": err}, indent=2)

    try:
        result = do_migrate_resources(
            source_account_id,
            target_account_id,
            _normalize_type(resource_type),
            ids,
            region,
            source_env,
            target_env,
            qs_service_role,
        )
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        logger.error(f"[TOOL] migrate_resources fatal: {traceback.format_exc()}")
        return json.dumps(
            {
                "overall_status": "FAILED",
                "error": str(e),
                "user_message": f"Migration failed unexpectedly: {str(e)}. Check CloudWatch logs.",
            },
            indent=2,
        )


@mcp.tool()
def preview_migration(
    source_account_id: str,
    resource_type: str = "all",
    search_by: str = "all",
    value: str = "",
    region: str = "us-east-1",
    target_account_id: str = "",
    include_permissions: bool = False,
) -> str:
    """
    Discovery / dry run. Read-only inventory of the resources that would be
    migrated. Uses the same selection model as migrate_resources.

    Selection model:
      resource_type: agent | connector | knowledge_base | space | all
                     ("all" inventories every type in the account).
      search_by:     id | name | all
                       "all"  → every resource of the given type(s)
                       "id"   → the single resource whose id == value
                       "name" → resources whose name matches value (case-insensitive)
      value:         id or name when search_by is id/name; ignored for "all".

    Target mapping (optional):
      target_account_id: when provided, the response additionally includes a
                         "target" inventory and a "mapping" array. For each
                         resource found in the SOURCE, the tool looks it up in
                         the target account BY THE SAME ID the migrator reuses:
                           - if it exists in target → the target object is added
                             to "target" and the mapping entry is
                             {in_source:true, in_target:true, action:"UPDATE"}
                           - if it does not exist   → nothing is fetched from the
                             target, and the mapping entry is
                             {in_source:true, in_target:false, action:"CREATE"}
                         The target inventory is intentionally SCOPED to ids that
                         exist in the source: target-only assets are never
                         enumerated or returned (migration is source→target, so
                         out-of-scope target assets are out of scope here too).
                         Read-only — no changes are made to either account.

    Args:
        source_account_id: Source AWS account ID
        resource_type: agent | connector | knowledge_base | space | all (default: all)
        search_by: id | name | all (default: all)
        value: id or name to match (required when search_by is id or name)
        region: AWS region
        target_account_id: optional target AWS account ID for the source→target mapping

    Returns:
        JSON with:
          source_account_id, target_account_id,
          source:  {agents, connectors, knowledge_bases, spaces}  (full source inventory)
          target:  {agents, connectors, knowledge_bases, spaces}  (only source-matched ids; present when target_account_id given)
          mapping: [{type, id, name, in_source, in_target, action}]  (present when target_account_id given)
          status, errors
    """
    logger.info(
        f"[TOOL] preview_migration: source={source_account_id} "
        f"target={target_account_id or '-'} type={resource_type} "
        f"search_by={search_by} value={value!r}"
    )
    try:
        source_qs = assume_role_client("quicksight", SOURCE_ROLE_ARN, region)
    except RuntimeError as e:
        return json.dumps(
            {"status": "FAILED", "user_message": f"Preview failed: {str(e)}"}, indent=2
        )

    # Optionally assume the target role to map CREATE vs UPDATE.
    target_qs = None
    if target_account_id:
        try:
            target_qs = assume_role_client("quicksight", TARGET_ROLE_ARN, region)
        except RuntimeError as e:
            # Non-fatal: still return the source inventory, just without mapping.
            logger.warning(f"  ⚠ target role assume failed, skipping mapping: {e}")

    rtype = _normalize_type(resource_type)
    if rtype != "all" and rtype not in _MIGRATABLE_TYPES:
        return json.dumps(
            {
                "status": "FAILED",
                "user_message": f"Invalid resource_type {resource_type!r}. "
                f"Valid: agent, connector, knowledge_base, space, flow, all.",
            },
            indent=2,
        )

    all_types = ["agent", "connector", "knowledge_base", "space", "flow"]
    if rtype == "all":
        types = all_types
    else:
        # Always include spaces in the preview even when the user filtered to a
        # single type: the SPACE is the only resource that knows what it links
        # to (agents/connectors/KBs/flows), so surfacing spaces lets the FE show
        # the relationship mapping. (This applies to preview only — migrate
        # stays scoped to exactly the type the user selected.)
        types = [rtype] if rtype == "space" else [rtype, "space"]

    inventory = {
        "agents": [],
        "connectors": [],
        "knowledge_bases": [],
        "spaces": [],
        "flows": [],
        "errors": [],
    }

    for t in types:
        ids, err = resolve_resource_ids(
            source_qs, source_account_id, t, search_by, value
        )
        if err and not ids:
            # In multi-type "all" mode a name that matches only one type is
            # expected to miss the others — record as info, not a hard failure.
            inventory["errors"].append(
                {"context": f"resolve({t})", "user_message": err}
            )
            continue

        if t == "connector":
            for cid in ids:
                try:
                    c = source_qs.describe_action_connector(
                        AwsAccountId=source_account_id, ActionConnectorId=cid
                    ).get("ActionConnector", {})
                    auth_cfg = c.get("AuthenticationConfig", {}) or {}
                    inventory["connectors"].append(
                        {
                            "connector_id": c.get("ActionConnectorId", cid),
                            "arn": c.get("Arn"),
                            "name": c.get("Name", cid),
                            "type": c.get("Type", "UNKNOWN"),
                            "description": c.get("Description"),
                            "status": c.get("Status"),
                            "authentication_type": auth_cfg.get("AuthenticationType"),
                            "enabled_actions": c.get("EnabledActions", []),
                            "created_time": c.get("CreatedTime"),
                            "last_updated_time": c.get("LastUpdatedTime"),
                        }
                    )
                except ClientError as e:
                    inventory["errors"].append(
                        {
                            "context": f"describe_action_connector({cid})",
                            "user_message": classify_error(e)["user_message"],
                        }
                    )
        elif t == "knowledge_base":
            for kid in ids:
                try:
                    kb = source_qs.describe_knowledge_base(
                        AwsAccountId=source_account_id, KnowledgeBaseId=kid
                    ).get("KnowledgeBase", {})
                    inventory["knowledge_bases"].append(
                        {
                            "knowledge_base_id": kid,
                            "name": kb.get("Name", kid),
                            "type": kb.get("Type", "UNKNOWN"),
                            "status": kb.get("Status", "UNKNOWN"),
                        }
                    )
                except ClientError as e:
                    inventory["errors"].append(
                        {
                            "context": f"describe_knowledge_base({kid})",
                            "user_message": classify_error(e)["user_message"],
                        }
                    )
        elif t == "agent":
            for aid in ids:
                try:
                    agent = source_qs.describe_agent(
                        AwsAccountId=source_account_id, AgentId=aid
                    ).get("Agent", {})
                    prompt = agent.get("CustomPromptInterface", {}) or {}
                    inventory["agents"].append(
                        {
                            "agent_id": agent.get("AgentId", aid),
                            "arn": agent.get("Arn"),
                            "name": agent.get("Name"),
                            "description": agent.get("Description"),
                            "connectors": [
                                a.split("/")[-1]
                                for a in agent.get("ActionConnectors", [])
                            ],
                            "agent_lifecycle": agent.get("AgentLifecycle"),
                            "agent_status": agent.get("AgentStatus"),
                            "icon_id": agent.get("IconId"),
                            "welcome_message": agent.get("WelcomeMessage"),
                            "starter_prompts": agent.get("StarterPrompts", []),
                            "created_at": agent.get("CreatedAt"),
                            "updated_at": agent.get("UpdatedAt"),
                            "creator": agent.get("Creator"),
                            "has_instructions": bool(prompt.get("CustomInstructions")),
                            "custom_prompt": {
                                "custom_instructions": prompt.get("CustomInstructions"),
                                "identity": prompt.get("Identity"),
                                "tone": prompt.get("Tone"),
                                "output_style": prompt.get("OutputStyle"),
                                "response_length": prompt.get("ResponseLength"),
                            }
                            if prompt
                            else None,
                        }
                    )
                except ClientError as e:
                    inventory["errors"].append(
                        {
                            "context": f"describe_agent({aid})",
                            "user_message": classify_error(e)["user_message"],
                        }
                    )
        elif t == "space":
            for sid in ids:
                try:
                    space = source_qs.describe_space(
                        AwsAccountId=source_account_id, SpaceId=sid
                    ).get("Space", {})
                    inventory["spaces"].append(
                        {
                            "space_id": sid,
                            "name": space.get("name", sid),
                            "description": space.get("description"),
                            "resources_count": len(space.get("resources", []) or []),
                            "linked_resources": [
                                {
                                    "resource_type": r.get("resourceType"),
                                    "resource_arn": (
                                        r.get("resourceDetails") or {}
                                    ).get("resourceArn"),
                                }
                                for r in (space.get("resources", []) or [])
                            ],
                            "created_at": space.get("createdAt"),
                            "updated_at": space.get("updatedAt"),
                        }
                    )
                except ClientError as e:
                    inventory["errors"].append(
                        {
                            "context": f"describe_space({sid})",
                            "user_message": classify_error(e)["user_message"],
                        }
                    )
        elif t == "flow":
            for fid in ids:
                try:
                    flow = _describe_flow(source_qs, source_account_id, fid)
                    inventory["flows"].append(
                        {
                            "flow_id": flow.get("FlowId", fid),
                            "arn": flow.get("Arn"),
                            "name": flow.get("Name", fid),
                            "description": flow.get("Description"),
                            "publish_state": flow.get("PublishState"),
                            "created_time": flow.get("CreatedTime"),
                            "last_updated_time": flow.get("LastUpdatedTime"),
                            # Flows are matched by NAME (CreateFlow mints a new id).
                            "match_by": "name",
                        }
                    )
                except ClientError as e:
                    inventory["errors"].append(
                        {
                            "context": f"describe_flow({fid})",
                            "user_message": classify_error(e)["user_message"],
                        }
                    )

    # Attach source permissions to every source inventory item (read-only).
    _src_type_key = {
        "agents": ("agent", "agent_id"),
        "connectors": ("connector", "connector_id"),
        "knowledge_bases": ("knowledge_base", "knowledge_base_id"),
        "spaces": ("space", "space_id"),
        "flows": ("flow", "flow_id"),
    }
    for coll_key, (rtype_name, id_key) in _src_type_key.items():
        for item in inventory[coll_key]:
            rid = item.get(id_key)
            if not rid:
                item["permissions"] = []
                continue
            if not include_permissions:
                # Permissions are expensive (a describe-permissions call per
                # resource). Skip by default; callers opt in via
                # include_permissions=true for the detailed view.
                continue
            perms, perr = describe_resource_permissions(
                source_qs, source_account_id, rtype_name, rid
            )
            item["permissions"] = perms
            if perr:
                item["permissions_error"] = perr

    # Build the source→target mapping against the target account (read-only),
    # if requested. Only ids present in the SOURCE are ever looked up in the
    # target; target-only assets are never enumerated or returned.
    result = {
        "source_account_id": source_account_id,
        "source": {
            "agents": inventory["agents"],
            "connectors": inventory["connectors"],
            "knowledge_bases": inventory["knowledge_bases"],
            "spaces": inventory["spaces"],
            "flows": inventory["flows"],
        },
        "errors": inventory["errors"],
    }

    if target_qs is not None:
        target_inv = {
            "agents": [],
            "connectors": [],
            "knowledge_bases": [],
            "spaces": [],
            "flows": [],
        }
        mapping = []
        # Cache target name-sets per type: list each target type at most ONCE
        # (not once per resource) so name_match doesn't trigger N full listings.
        _target_names_cache = {}

        def _target_name_set(rt):
            if rt in _target_names_cache:
                return _target_names_cache[rt]
            try:
                op_name, result_key, id_field, name_field = _RESOURCE_LISTERS[rt]
                summaries = _paginate(
                    target_qs, op_name, result_key, AwsAccountId=target_account_id
                )
                names = {
                    (s.get(name_field, "") or "").strip().lower()
                    for s in summaries
                }
                _target_names_cache[rt] = names
            except ClientError:
                _target_names_cache[rt] = None  # unknown (list failed)
            return _target_names_cache[rt]

        for coll_key, (rtype_name, id_key) in _src_type_key.items():
            for item in inventory[coll_key]:
                rid = item.get(id_key)
                name = item.get("name")
                src_perms = item.get("permissions", [])
                if not rid:
                    continue

                if rtype_name == "flow":
                    # Flows match by NAME (CreateFlow mints a new target id).
                    try:
                        matches = _find_target_flows_by_name(
                            target_qs, target_account_id, name
                        )
                    except ClientError as e:
                        mapping.append({
                            "type": "flow", "id": rid, "name": name,
                            "in_source": True, "in_target": None,
                            "id_match": False, "name_match": None,
                            "matched_by": "none", "match_by": "name",
                            "action": "UNKNOWN",
                            "error": classify_error(e)["user_message"],
                            "source_permissions": src_perms,
                        })
                        continue
                    if matches:
                        # Fetch the (first) target flow to include in target inv.
                        tgt_obj, _, _ = describe_target_resource(
                            target_qs, target_account_id, "flow", matches[0]
                        )
                        tgt_perms = []
                        if include_permissions:
                            tgt_perms, _ = describe_resource_permissions(
                                target_qs, target_account_id, "flow", matches[0]
                            )
                        if tgt_obj is not None:
                            if include_permissions:
                                tgt_obj["permissions"] = tgt_perms
                            target_inv["flows"].append(tgt_obj)
                        mapping.append({
                            "type": "flow", "id": rid, "name": name,
                            "in_source": True, "in_target": True,
                            "id_match": False, "name_match": True,
                            "matched_by": "name", "match_by": "name",
                            "target_id": matches[0],
                            "ambiguous": len(matches) > 1,
                            "action": "UPDATE",
                            "source_permissions": src_perms,
                            "target_permissions": tgt_perms,
                        })
                    else:
                        mapping.append({
                            "type": "flow", "id": rid, "name": name,
                            "in_source": True, "in_target": False,
                            "id_match": False, "name_match": False,
                            "matched_by": "none", "match_by": "name",
                            "action": "CREATE",
                            "source_permissions": src_perms,
                            "target_permissions": [],
                        })
                    continue

                # ── id-matched types (agent/connector/kb/space) ──
                # Primary match is by ID (what migrate can honor). We ALSO report
                # whether a same-name target resource exists (name_match) so the
                # FE can flag "a differently-identified same-name resource is in
                # target". action stays driven by id_match, since migrate reuses
                # the source id.
                obj, id_exists, err = describe_target_resource(
                    target_qs, target_account_id, rtype_name, rid
                )
                if err is not None:
                    mapping.append({
                        "type": rtype_name, "id": rid, "name": name,
                        "in_source": True, "in_target": None,
                        "id_match": None, "name_match": None,
                        "matched_by": "unknown",
                        "action": "UNKNOWN", "error": err,
                        "source_permissions": src_perms,
                    })
                    continue

                # Name match: does any target resource of this type share the
                # name? Uses the per-type cached name-set (listed once).
                names = _target_name_set(rtype_name)
                if names is None:
                    name_match = None  # could not determine
                else:
                    name_match = (name or "").strip().lower() in names

                matched_by = (
                    "id+name" if (id_exists and name_match)
                    else "id" if id_exists
                    else "name" if name_match
                    else "none"
                )

                if id_exists:
                    tgt_perms = []
                    if include_permissions:
                        tgt_perms, tperr = describe_resource_permissions(
                            target_qs, target_account_id, rtype_name, rid
                        )
                        obj["permissions"] = tgt_perms
                        if tperr:
                            obj["permissions_error"] = tperr
                    target_inv[coll_key].append(obj)
                    mapping.append({
                        "type": rtype_name, "id": rid, "name": name,
                        "in_source": True, "in_target": True,
                        "id_match": True, "name_match": name_match,
                        "matched_by": matched_by,
                        "action": "UPDATE",
                        "source_permissions": src_perms,
                        "target_permissions": tgt_perms,
                    })
                else:
                    # No id match → migrate will CREATE (reusing the source id),
                    # even if a same-name resource exists (that would duplicate
                    # by name; the FE can warn via name_match).
                    mapping.append({
                        "type": rtype_name, "id": rid, "name": name,
                        "in_source": True, "in_target": False,
                        "id_match": False, "name_match": name_match,
                        "matched_by": matched_by,
                        "action": "CREATE",
                        "source_permissions": src_perms,
                        "target_permissions": [],
                    })
        result["target_account_id"] = target_account_id
        result["target"] = target_inv
        result["mapping"] = mapping

    result["status"] = "OK" if not inventory["errors"] else "COMPLETED_WITH_ERRORS"
    logger.info(
        f"[TOOL] preview_migration done: {len(inventory['agents'])} agents, "
        f"{len(inventory['connectors'])} connectors, "
        f"{len(inventory['knowledge_bases'])} KBs, "
        f"{len(inventory['spaces'])} spaces, {len(inventory['flows'])} flows, "
        f"{len(inventory['errors'])} errors"
        + (f", mapping={len(result.get('mapping', []))}" if target_qs else "")
    )
    return json.dumps(result, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# BACKUP CATALOG + RESTORE (target account)
# ═══════════════════════════════════════════════════════════════════


@mcp.tool()
def list_backups(query: str = "", region: str = "us-east-1") -> str:
    """
    Search the backup catalog and list versions per asset.

    Backups are pre-update snapshots of TARGET resources written to the backup
    bucket (see BACKUP_BUCKET) as "<Asset Name> (asset_id)/json_v<N>.json".

    Args:
        query: optional case-insensitive substring to filter by asset name OR
               asset id. Empty → list all backed-up assets.
        region: AWS region (for the S3 client).

    Returns:
        JSON: { bucket, count, assets: [ { name, asset_id, folder,
                latest_version, versions: [ {version, key, last_modified, size} ] } ] }
    """
    logger.info(f"[TOOL] list_backups: query={query!r}")
    if not BACKUP_BUCKET:
        return json.dumps(
            {"status": "FAILED", "user_message": "No backup bucket is configured."},
            indent=2,
        )
    s3 = boto3.client("s3", region_name=region)
    assets, err = list_backup_catalog(s3, BACKUP_BUCKET, query)
    if err:
        return json.dumps({"status": "FAILED", "user_message": err}, indent=2)
    return json.dumps(
        {"bucket": BACKUP_BUCKET, "count": len(assets), "assets": assets},
        indent=2,
        default=str,
    )


@mcp.tool()
def get_backup(asset_id: str, version: int = 0, region: str = "us-east-1") -> str:
    """
    Return the full stored backup envelope for an asset version.

    Args:
        asset_id: the resource id whose backup to fetch.
        version: the version number (json_v<N>). 0 (default) → the latest.
        region: AWS region.

    Returns:
        JSON: the backup envelope { schema_version, backed_up_at, resource_type,
        resource_id, name, resource: <describe>, dependencies: {...} }, plus the
        s3 key it came from.
    """
    logger.info(f"[TOOL] get_backup: asset_id={asset_id!r} version={version}")
    if not BACKUP_BUCKET:
        return json.dumps(
            {"status": "FAILED", "user_message": "No backup bucket is configured."},
            indent=2,
        )
    s3 = boto3.client("s3", region_name=region)
    assets, err = list_backup_catalog(s3, BACKUP_BUCKET, asset_id)
    if err:
        return json.dumps({"status": "FAILED", "user_message": err}, indent=2)
    match = next((a for a in assets if a["asset_id"] == asset_id), None)
    if not match or not match["versions"]:
        return json.dumps(
            {"status": "FAILED", "user_message": f"No backups found for asset {asset_id!r}."},
            indent=2,
        )
    want = version or match["latest_version"]
    ver = next((v for v in match["versions"] if v["version"] == want), None)
    if not ver:
        return json.dumps(
            {
                "status": "FAILED",
                "user_message": f"Version {want} not found for {asset_id!r}. "
                f"Available: {[v['version'] for v in match['versions']]}.",
            },
            indent=2,
        )
    try:
        envelope = read_backup_envelope(s3, BACKUP_BUCKET, ver["key"])
    except (ClientError, ValueError) as e:
        return json.dumps(
            {"status": "FAILED", "user_message": f"Could not read backup: {e}"}, indent=2
        )
    return json.dumps(
        {"status": "OK", "s3_key": ver["key"], "version": want, "backup": envelope},
        indent=2,
        default=str,
    )


def _restore_resource(target_qs, target_account_id, region, envelope, report):
    """Apply a backup envelope back onto the TARGET resource (update, or create
    if it no longer exists). Config-level restore; returns a status string."""
    rtype = envelope.get("resource_type")
    rid = envelope.get("resource_id")
    name = envelope.get("name") or rid
    obj = envelope.get("resource", {}) or {}

    try:
        if rtype == "connector":
            auth = sanitize_auth_config(obj.get("AuthenticationConfig", {}))
            params = {
                "AwsAccountId": target_account_id,
                "ActionConnectorId": rid,
                "Name": obj.get("Name", name),
                "AuthenticationConfig": auth,
            }
            if obj.get("Description"):
                params["Description"] = obj["Description"]
            try:
                target_qs.update_action_connector(**params)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                    params["Type"] = obj.get("Type", "GENERIC_HTTP")
                    target_qs.create_action_connector(**params)
                else:
                    raise
        elif rtype == "agent":
            # Re-apply the agent's OWN config, and RE-ATTACH its connector links
            # (remapped to target) so it shows connected again. We never update
            # the connector RESOURCES themselves — only the agent + its linkage.
            params = {
                "AwsAccountId": target_account_id,
                "AgentId": rid,
                "Name": obj.get("Name", name),
            }
            if obj.get("Description"):
                params["Description"] = obj["Description"]
            # Custom prompt / instructions.
            prompt = obj.get("CustomPromptInterface") or {}
            new_prompt = {
                k: v for k, v in {
                    "CustomInstructions": prompt.get("CustomInstructions"),
                    "Identity": prompt.get("Identity"),
                    "Tone": prompt.get("Tone"),
                    "OutputStyle": prompt.get("OutputStyle"),
                    "ResponseLength": prompt.get("ResponseLength"),
                }.items() if v
            }
            if new_prompt:
                params["CustomPromptInput"] = {"NewPrompt": new_prompt}
            if obj.get("StarterPrompts"):
                params["StarterPrompts"] = obj["StarterPrompts"]
            if obj.get("WelcomeMessage"):
                params["WelcomeMessage"] = obj["WelcomeMessage"]
            if obj.get("IconId"):
                params["IconId"] = obj["IconId"]
            # Re-attach connectors that are NOT already linked (link only —
            # the connectors' own configs are untouched).
            backed_up_conns = [
                remap_arn(a, target_account_id, region)
                for a in obj.get("ActionConnectors", []) or []
            ]
            if backed_up_conns:
                existing = set()
                try:
                    cur = target_qs.describe_agent(
                        AwsAccountId=target_account_id, AgentId=rid
                    ).get("Agent", {})
                    existing = set(cur.get("ActionConnectors", []) or [])
                except ClientError:
                    pass
                to_add = [a for a in backed_up_conns if a not in existing]
                if to_add:
                    params["ActionConnectorsToAdd"] = to_add
            target_qs.update_agent(**params)
        elif rtype == "space":
            # Re-apply the space's OWN fields and RE-LINK its resources (remapped
            # to target). The linked resources themselves are not modified.
            params = {
                "AwsAccountId": target_account_id,
                "SpaceId": rid,
                "Name": obj.get("name", name),
            }
            if obj.get("description"):
                params["Description"] = obj["description"]
            target_qs.update_space(**params)
            add_resources = []
            for r in obj.get("resources", []) or []:
                src_arn = (r.get("resourceDetails") or {}).get("resourceArn")
                if not src_arn:
                    continue
                add_resources.append({
                    "ResourceType": r.get("resourceType"),
                    "ResourceDetails": {
                        "resourceArn": remap_arn(src_arn, target_account_id, region)
                    },
                })
            if add_resources:
                try:
                    target_qs.update_space_resources(
                        AwsAccountId=target_account_id,
                        SpaceId=rid,
                        AddResources=add_resources,
                    )
                except ClientError as e:
                    report["errors"].append(
                        format_error_for_report(f"restore_space_resources({rid})", e)
                    )
        elif rtype == "knowledge_base":
            target_qs.update_knowledge_base(
                AwsAccountId=target_account_id,
                KnowledgeBaseId=rid,
                Name=obj.get("Name", name),
                KnowledgeBaseConfiguration=obj.get("KnowledgeBaseConfiguration"),
            )
        elif rtype == "flow":
            definition = obj.get("FlowDefinition")
            if not definition:
                return "FAILED: backup has no FlowDefinition to restore"
            target_qs.update_flow(
                AwsAccountId=target_account_id,
                FlowId=rid,
                Name=obj.get("Name", name),
                FlowDefinition=definition,
            )
        else:
            return f"FAILED: unsupported resource_type {rtype!r}"
        return "RESTORED"
    except ClientError as e:
        report["errors"].append(
            format_error_for_report(f"restore_{rtype}({rid})", e)
        )
        return f"FAILED: {classify_error(e)['user_message']}"


@mcp.tool()
def restore_backup(
    asset_id: str, version: int = 0, region: str = "us-east-1"
) -> str:
    """
    Revert a TARGET resource to a previously backed-up version.

    Reverting OVERWRITES the current target resource, so this first takes a
    FRESH pre-restore backup (a new json_v<N> for the asset) — you can always
    undo the undo. Then it re-applies the chosen backup's stored configuration
    to the target (update in place, or create if the resource no longer exists).

    This is a config-level restore of the resource itself. Where the backup
    envelope captured dependencies (agent→connectors, space→links, KB→data
    source), they are reported for visibility, but dependent RESOURCES are
    restored individually (call restore_backup for each) — this call restores
    the single named asset.

    All writes target the TARGET account (the account the resource lives in).

    Args:
        asset_id: the resource id to restore.
        version: the backup version to restore (json_v<N>). 0 → latest.
        region: AWS region.

    Returns:
        JSON report: { status, asset_id, restored_from_version, pre_restore_backup,
        result, dependencies, errors }.
    """
    logger.info(f"[TOOL] restore_backup: asset_id={asset_id!r} version={version}")
    report = {"asset_id": asset_id, "errors": []}
    if not BACKUP_BUCKET:
        return json.dumps(
            {"status": "FAILED", "user_message": "No backup bucket is configured."},
            indent=2,
        )
    s3 = boto3.client("s3", region_name=region)

    # 1. Locate the requested backup version.
    assets, err = list_backup_catalog(s3, BACKUP_BUCKET, asset_id)
    if err:
        return json.dumps({"status": "FAILED", "user_message": err}, indent=2)
    match = next((a for a in assets if a["asset_id"] == asset_id), None)
    if not match or not match["versions"]:
        return json.dumps(
            {"status": "FAILED", "user_message": f"No backups for {asset_id!r}."}, indent=2
        )
    want = version or match["latest_version"]
    ver = next((v for v in match["versions"] if v["version"] == want), None)
    if not ver:
        return json.dumps(
            {
                "status": "FAILED",
                "user_message": f"Version {want} not found for {asset_id!r}. "
                f"Available: {[v['version'] for v in match['versions']]}.",
            },
            indent=2,
        )
    try:
        envelope = read_backup_envelope(s3, BACKUP_BUCKET, ver["key"])
    except (ClientError, ValueError) as e:
        return json.dumps(
            {"status": "FAILED", "user_message": f"Could not read backup: {e}"}, indent=2
        )

    rtype = envelope.get("resource_type")
    name = envelope.get("name") or asset_id
    target_account_id = envelope.get("account_id")

    # 2. Assume the target role (restore always writes to the target account).
    try:
        target_qs = assume_role_client("quicksight", TARGET_ROLE_ARN, region)
    except RuntimeError as e:
        return json.dumps(
            {"status": "FAILED", "user_message": f"Restore failed: {e}"}, indent=2
        )

    # 3. Take a FRESH pre-restore backup so the revert is itself reversible.
    if not maybe_backup_before_update(
        target_qs, s3, target_account_id, rtype, asset_id, name, report
    ):
        return json.dumps(
            {
                "status": "FAILED",
                "asset_id": asset_id,
                "user_message": "Could not take a pre-restore backup; the resource "
                "was NOT modified.",
                "errors": report["errors"],
            },
            indent=2,
        )
    pre_restore = report.get("backups", [{}])[-1].get("s3_key")

    # 4. Re-apply the chosen backup.
    result_status = _restore_resource(target_qs, target_account_id, region, envelope, report)

    status = "OK" if result_status == "RESTORED" else "FAILED"
    return json.dumps(
        {
            "status": status,
            "asset_id": asset_id,
            "resource_type": rtype,
            "restored_from_version": want,
            "restored_from_key": ver["key"],
            "pre_restore_backup": pre_restore,
            "result": result_status,
            "dependencies": envelope.get("dependencies", {}),
            "errors": report["errors"],
        },
        indent=2,
        default=str,
    )


# ═══════════════════════════════════════════════════════════════════
# CLI MODE (for local testing)
# ═══════════════════════════════════════════════════════════════════


def cli_mode():
    print("=" * 60)
    print("  Quick Resource Migrator — Local CLI Mode")
    print("=" * 60)
    print(f"\n  SOURCE_ROLE_ARN: {SOURCE_ROLE_ARN or '(using local creds)'}")
    print(f"  TARGET_ROLE_ARN: {TARGET_ROLE_ARN or '(using local creds)'}\n")

    action = input("Action [migrate / preview]: ").strip().lower()
    source_account = input("Source account ID: ").strip()
    region = input("Region [us-east-1]: ").strip() or "us-east-1"

    if action == "preview":
        resource_type = (
            input("Resource type [agent / connector / knowledge_base / all]: ").strip()
            or "all"
        )
        search_by = input("Search by [id / name / all]: ").strip() or "all"
        value = (
            input("Value (id or name; blank for all): ").strip()
            if search_by in ("id", "name")
            else ""
        )
        print("\n⏳ Reading source...\n")
        print(
            preview_migration(source_account, resource_type, search_by, value, region)
        )
    else:
        target_account = input("Target account ID: ").strip()
        resource_type = input(
            "Resource type [agent / connector / knowledge_base]: "
        ).strip()
        search_by = input("Search by [id / name / all]: ").strip() or "all"
        value = (
            input("Value (id or name; blank for all): ").strip()
            if search_by in ("id", "name")
            else ""
        )
        source_env = input("Source env [dev]: ").strip() or "dev"
        target_env = input("Target env [prod]: ").strip() or "prod"
        qs_role = (
            input(f"QuickSight service role [{DEFAULT_QS_SERVICE_ROLE}]: ").strip()
            or DEFAULT_QS_SERVICE_ROLE
        )
        print("\n⏳ Migrating...\n")
        print(
            migrate_resources(
                source_account,
                target_account,
                resource_type,
                search_by,
                value,
                region,
                source_env,
                target_env,
                qs_role,
            )
        )
    print("\n✅ Done.")
    sys.exit(0)


# ═══════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--cli" in sys.argv:
        cli_mode()
    else:
        mcp.run(transport="streamable-http")
