#!/usr/bin/env python3
"""
Quick Resource Migrator — Bedrock AgentCore MCP Server
════════════════════════════════════════════════════════

Migrates Quick Agents, Action Connectors, and S3 Knowledge Bases between AWS
accounts. Resource-driven: you select a resource type (agent | connector |
knowledge_base) and choose resources by id, by name, or all. Spaces are NOT
created or linked.

Agents are recreated with their Action Connectors attached (remapped to the
target account) but with no Spaces attachment. Connectors carry sanitized
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
import sys
import time
import traceback
import uuid

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

# ═══════════════════════════════════════════════════════════════════
# ENV CONFIG (set these in AgentCore runtime config)
# ═══════════════════════════════════════════════════════════════════

SOURCE_ROLE_ARN = os.environ.get("SOURCE_ROLE_ARN", "")
TARGET_ROLE_ARN = os.environ.get("TARGET_ROLE_ARN", "")

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
        time.sleep(5)
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
        time.sleep(5)
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


def _list_target_users(target_qs, target_account, namespace, _cache={}):  # noqa: B006 — module-level memoization cache is intentional (persists across calls)
    """List and cache all registered QuickSight users in the target namespace."""
    key = (target_account, namespace)
    if key in _cache:
        return _cache[key]
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
    _cache[key] = users
    return users


def _resolve_target_principal(
    target_qs,
    target_account,
    region,
    principal_arn,
    _cache={},  # noqa: B006 — intentional persistent memoization cache
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
    if principal_arn in _cache:
        return _cache[principal_arn]

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

    _cache[principal_arn] = resolved
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

    if rtype == "connector":
        _migrate_connectors(
            source_qs,
            target_qs,
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
            source_account_id,
            target_account_id,
            region,
            ids,
            report,
        )

    created_key = {
        "connector": "connectors",
        "knowledge_base": "knowledge_bases",
        "agent": "agents",
    }[rtype]
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
    source_account_id,
    target_account_id,
    region,
    agent_ids,
    report,
):
    """Recreate the given agents in the target (standalone — no Spaces) + copy permissions.

    Action Connectors referenced by the agent are remapped to the target account
    and attached, exactly as in the source. Spaces are intentionally NOT set.
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


# ═══════════════════════════════════════════════════════════════════
# RESOURCE RESOLUTION (resource_type + id | name | all)
# ═══════════════════════════════════════════════════════════════════

# The three resource types this server can migrate. "space" is intentionally
# NOT here — migration is resource-driven and does not create or link spaces.
_MIGRATABLE_TYPES = {"agent", "connector", "knowledge_base"}

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
}


def _summary_id(summary, id_field):
    """Return a resource id from a list_* summary, falling back to the ARN tail."""
    return summary.get(id_field) or (summary.get("Arn", "").split("/")[-1] or None)


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

    # search_by=id does not need a full listing — return the id directly and
    # let the downstream describe/create surface a not-found error if it is bogus.
    if mode == "id":
        return [value.strip()], None

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
# outside its loopback interface, so the server must listen on 0.0.0.0 to
# receive them — binding to 127.0.0.1 would make the runtime unreachable.
# This is not a public exposure: the container runs behind AgentCore's managed
# ingress, inbound is gated by the Cognito JWT authorizer, and the runtime
# operates in VPC network mode (private subnets). Override with BIND_HOST if
# you run the server in a different context.
#
# nosec B104 — bind-all is required for the AgentCore container runtime (see above).
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")  # nosec B104  # noqa: S104

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
    Migrate Quick resources (Agents, Action Connectors, or S3 Knowledge Bases)
    from a source account to a target account. Resource-driven — Spaces are NOT
    created or linked.

    Selection model:
      resource_type: which kind of resource to migrate — one of
                     agent | connector | knowledge_base.
      search_by:     how to select within that type — one of:
                       "all"  → every resource of this type in the account
                       "id"   → the single resource whose id == value
                       "name" → all resources whose name matches value (case-insensitive)
      value:         the id or name when search_by is id/name; ignored for "all".

    Behavior:
      - Agents are recreated with their Action Connectors attached (remapped to
        the target account), but with NO Spaces attachment.
      - Connectors are recreated with sanitized (placeholder-secret) auth config
        and must be re-authenticated in the target UI.
      - Knowledge bases provision the target bucket + data source + KB
        (documents are NOT copied). Requires a valid QuickSight service role.
      - Permissions are copied by describing the source and replaying the exact
        grants, with principals resolved to registered target users.

    Args:
        source_account_id: 12-digit source AWS account ID
        target_account_id: 12-digit target AWS account ID
        resource_type: agent | connector | knowledge_base
        search_by: id | name | all (default: all)
        value: id or name to match (required when search_by is id or name)
        region: AWS region (default: us-east-1)
        source_env: env name used in the source KB bucket (knowledge-base-<env>-<account>)
        target_env: env name used in the target KB bucket (knowledge-base-<env>-<account>)
        qs_service_role: QuickSight service role name for the S3 bucket policy

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
) -> str:
    """
    Discovery / dry run. Read-only inventory of the resources that would be
    migrated. Uses the same selection model as migrate_resources. Spaces are NOT
    part of the selection or the output.

    Selection model:
      resource_type: agent | connector | knowledge_base | all
                     ("all" inventories every type in the account).
      search_by:     id | name | all
                       "all"  → every resource of the given type(s)
                       "id"   → the single resource whose id == value
                       "name" → resources whose name matches value (case-insensitive)
      value:         id or name when search_by is id/name; ignored for "all".

    Args:
        source_account_id: Source AWS account ID
        resource_type: agent | connector | knowledge_base | all (default: all)
        search_by: id | name | all (default: all)
        value: id or name to match (required when search_by is id or name)
        region: AWS region

    Returns:
        JSON inventory of the selected agents, connectors, and knowledge bases.
    """
    logger.info(
        f"[TOOL] preview_migration: source={source_account_id} type={resource_type} "
        f"search_by={search_by} value={value!r}"
    )
    try:
        source_qs = assume_role_client("quicksight", SOURCE_ROLE_ARN, region)
    except RuntimeError as e:
        return json.dumps(
            {"status": "FAILED", "user_message": f"Preview failed: {str(e)}"}, indent=2
        )

    rtype = _normalize_type(resource_type)
    types = ["agent", "connector", "knowledge_base"] if rtype == "all" else [rtype]
    if rtype != "all" and rtype not in _MIGRATABLE_TYPES:
        return json.dumps(
            {
                "status": "FAILED",
                "user_message": f"Invalid resource_type {resource_type!r}. "
                f"Valid: agent, connector, knowledge_base, all.",
            },
            indent=2,
        )

    inventory = {"agents": [], "connectors": [], "knowledge_bases": [], "errors": []}

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

    inventory["status"] = "OK" if not inventory["errors"] else "COMPLETED_WITH_ERRORS"
    logger.info(
        f"[TOOL] preview_migration done: {len(inventory['agents'])} agents, "
        f"{len(inventory['connectors'])} connectors, "
        f"{len(inventory['knowledge_bases'])} KBs, {len(inventory['errors'])} errors"
    )
    return json.dumps(inventory, indent=2, default=str)


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
