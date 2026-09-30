"""
common — shared foundation for the Quick Resource Migrator MCP server.

Pure, dependency-free layer imported by every other module (resources, backups,
migrate_*, server). It must NOT import any of those modules (avoid cycles).

Contains: env config, logging, error classification, role assumption, ARN/
principal remapping, auth sanitization, active-state polling, permission copy
helpers, QuickSight service-role + KB-bucket helpers, resource-type maps, and
small utilities (_normalize_type, _paginate, _summary_id).
"""

import json
import logging
import os
import sys
from threading import Event

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging for CloudWatch (AgentCore captures stdout/stderr)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("quick_migrator")


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
    source_qs,
    target_qs,
    source_account,
    target_account,
    region,
    source_flow_id,
    target_flow_id,
    report,
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


# ═══════════════════════════════════════════════════════════════════
# RESOURCE-TYPE MAPS (shared across preview / resources / migrators)
# ═══════════════════════════════════════════════════════════════════

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


# ARN resource-type segment -> our resource vocabulary (for existence checks on
# a space's / agent's linked resources).
_ARN_SEGMENT_TO_RTYPE = {
    "space": "space",
    "agent": "agent",
    "action-connector": "connector",
    "knowledge-base": "knowledge_base",
    "flow": "flow",
}


def target_link_exists(target_qs, target_account_id, resource_type, arn):
    """Return True if a linked resource (identified by its target ARN) can be
    confirmed to exist in the target account. Used when a resource's current
    links cannot be read, so only successfully created resources are linked and
    existing links are never touched. Unknown types are treated as
    not-confirmed (skipped)."""
    seg = (resource_type or "").strip().lower()
    rtype = _ARN_SEGMENT_TO_RTYPE.get(seg)
    describer = _RESOURCE_DESCRIBERS.get(rtype)
    if not describer:
        return False
    op_name, id_kwarg = describer
    resource_id = (arn or "").split("/")[-1]
    if not resource_id:
        return False
    try:
        getattr(target_qs, op_name)(
            **{"AwsAccountId": target_account_id, id_kwarg: resource_id}
        )
        return True
    except ClientError:
        return False


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
