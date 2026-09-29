#!/usr/bin/env python3
"""
Quick Resource Migrator — Bedrock AgentCore MCP Server
════════════════════════════════════════════════════════

Migrates Quick Agents, Action Connectors, Knowledge Bases, Spaces, and Flows
between AWS accounts. Resource-driven: you select a resource type
(agent | connector | knowledge_base | space | flow) and choose resources by id,
by name, or all.

This module is the MCP entrypoint. The heavy lifting lives in flat sibling
modules that the AgentCore bundle flattens alongside this file:

  common.py                 — shared foundation: role assumption, error
                              classification, ARN/auth remapping, permission
                              copy helpers, bucket helpers, and the per-type
                              resource maps + module-level config
                              (SOURCE_ROLE_ARN, TARGET_ROLE_ARN, BACKUP_BUCKET,
                              DEFAULT_QS_SERVICE_ROLE, assume_role_client).
  resources.py              — resource selection (resolve_resource_ids), value
                              coercion, and target existence/describe/permission
                              helpers used by preview + migrate.
  backups.py                — backup catalog, pre-update backups, and
                              post-migration snapshots (reads common.BACKUP_BUCKET).
  migrate_connectors.py     — _migrate_connectors
  migrate_agents.py         — _migrate_agents
  migrate_spaces.py         — _migrate_spaces
  migrate_knowledge_bases.py— _migrate_knowledge_bases (+ KB bucket helpers)
  migrate_flows.py          — _migrate_flows (+ flow describe/remap/link helpers)

Permissions are NOT hardcoded — the server DESCRIBES the permissions on each
source resource and copies the exact same actions to the target.

Env vars (configure in AgentCore):
  SOURCE_ROLE_ARN  — IAM role in source account (read-only QuickSight + S3)
  TARGET_ROLE_ARN  — IAM role in target account (read+write QuickSight + S3)
  BACKUP_BUCKET    — bucket in the runner account for backups/snapshots
"""

import json
import logging
import os
import sys
import traceback

import boto3
import common
from backups import (
    _collect_dependencies,
    _parse_backup_folder,
    list_backup_catalog,
    maybe_backup_before_update,
    read_backup_envelope,
    snapshot_after_migrate,
)
from botocore.exceptions import ClientError
from common import (
    _MIGRATABLE_TYPES,
    _RESOURCE_DESCRIBE_KEYS,
    _RESOURCE_DESCRIBERS,
    _RESOURCE_LISTERS,
    _RESOURCE_PERMISSION_DESCRIBERS,
    DEFAULT_QS_SERVICE_ROLE,
    _normalize_type,
    _paginate,
    assume_role_client,
    classify_error,
    format_error_for_report,
    remap_arn,
    sanitize_auth_config,
)
from mcp.server.fastmcp import FastMCP
from migrate_agents import _migrate_agents
from migrate_connectors import _migrate_connectors
from migrate_flows import (
    _describe_flow,
    _extract_flow_linked_resources,
    _find_target_flows_by_name,
    _migrate_flows,
    _remap_flow_definition,
)
from migrate_knowledge_bases import _derive_kb_bucket_name, _migrate_knowledge_bases
from migrate_spaces import _migrate_spaces
from resources import (
    describe_resource_permissions,
    describe_target_resource,
    resolve_resource_ids,
    target_resource_exists,
)

# These names are imported for use by the tools/dispatcher below AND re-exported
# so `server.X` keeps resolving for callers and tests that reference them
# directly (e.g. server.target_resource_exists, server._describe_flow).
__all__ = [
    "assume_role_client",
    "resolve_resource_ids",
    "target_resource_exists",
    "describe_target_resource",
    "describe_resource_permissions",
    "_migrate_connectors",
    "_migrate_agents",
    "_migrate_spaces",
    "_migrate_knowledge_bases",
    "_migrate_flows",
    "_describe_flow",
    "_extract_flow_linked_resources",
    "_find_target_flows_by_name",
    "_remap_flow_definition",
    "_derive_kb_bucket_name",
    "_RESOURCE_DESCRIBERS",
    "_RESOURCE_DESCRIBE_KEYS",
    "_RESOURCE_PERMISSION_DESCRIBERS",
    "_parse_backup_folder",
    "_collect_dependencies",
    "do_migrate_resources",
    "_restore_resource",
    "migrate_resources",
    "preview_migration",
    "list_backups",
    "get_backup",
    "restore_backup",
]

# Configure logging for CloudWatch (AgentCore captures stdout/stderr).
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

# Re-export config as module globals so `server.X` remains patchable in tests
# and readable by the functions defined in THIS module. Functions that must see
# test-time reassignment of the backup bucket (the migrate/backup paths) read
# common.BACKUP_BUCKET directly; the read-only backup tools below use the
# module global, which mirrors it at import time.
SOURCE_ROLE_ARN = common.SOURCE_ROLE_ARN
TARGET_ROLE_ARN = common.TARGET_ROLE_ARN
BACKUP_BUCKET = common.BACKUP_BUCKET


# ═══════════════════════════════════════════════════════════════════
# MIGRATION DISPATCHER
# ═══════════════════════════════════════════════════════════════════


def do_migrate_resources(
    source_account_id,
    target_account_id,
    resource_type,
    ids,
    region,
    source_env,
    target_env,
    qs_service_role,
    target_kb_bucket="",
    target_data_source_arn="",
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
            target_kb_bucket,
            target_data_source_arn,
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
            target_qs,
            backup_s3,
            target_account_id,
            rtype,
            snap_id,
            m.get("name"),
            action,
            report,
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
    target_kb_bucket: str = "",
    target_data_source_arn: str = "",
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
        target_kb_bucket: (S3 knowledge bases only) optional target S3 bucket to
                     use for the KB's data source. If omitted, a bucket named
                     "<source-bucket>-<target_account_id>" is created.
        target_data_source_arn: (credentialed KB types — SharePoint, Confluence,
                     Google Drive, QBusiness) optional ARN of a data source you
                     already created in the target account (its connection/auth
                     cannot be copied across accounts). If omitted, such KBs are
                     skipped with guidance to create the connection first.

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
            target_kb_bucket,
            target_data_source_arn,
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
                    # The agent references connectors by ARN only (no names). The
                    # FE needs the id AND name to render each linked connector and
                    # to submit the correct connector id when migrating it — so
                    # resolve each connector's name here (best-effort describe).
                    connector_arns = agent.get("ActionConnectors", []) or []
                    connector_ids = [a.split("/")[-1] for a in connector_arns]
                    connectors_detailed = []
                    for arn in connector_arns:
                        cid = arn.split("/")[-1]
                        cname = None
                        try:
                            cinfo = source_qs.describe_action_connector(
                                AwsAccountId=source_account_id, ActionConnectorId=cid
                            )
                            cname = (cinfo.get("ActionConnector", cinfo) or {}).get(
                                "Name"
                            )
                        except ClientError:
                            cname = None
                        connectors_detailed.append(
                            {
                                "connector_id": cid,
                                "name": cname or cid,
                                "arn": arn,
                            }
                        )
                    inventory["agents"].append(
                        {
                            "agent_id": agent.get("AgentId", aid),
                            "arn": agent.get("Arn"),
                            "name": agent.get("Name"),
                            "description": agent.get("Description"),
                            # Bare id list kept for backward compatibility.
                            "connectors": connector_ids,
                            # id + name objects so the FE never shows "Unnamed"
                            # and can submit the real connector id.
                            "connectors_detailed": connectors_detailed,
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
                    linked = _extract_flow_linked_resources(flow.get("FlowDefinition"))
                    inventory["flows"].append(
                        {
                            "flow_id": flow.get("FlowId", fid),
                            "arn": flow.get("Arn"),
                            "name": flow.get("Name", fid),
                            "description": flow.get("Description"),
                            "publish_state": flow.get("PublishState"),
                            "created_time": flow.get("CreatedTime"),
                            "last_updated_time": flow.get("LastUpdatedTime"),
                            # Resources referenced inside the flow definition, so
                            # the FE can show them and offer to migrate them too.
                            "linked_resources": linked,
                            "resources_count": len(linked),
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
                    (s.get(name_field, "") or "").strip().lower() for s in summaries
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
                        mapping.append(
                            {
                                "type": "flow",
                                "id": rid,
                                "name": name,
                                "in_source": True,
                                "in_target": None,
                                "id_match": False,
                                "name_match": None,
                                "matched_by": "none",
                                "match_by": "name",
                                "action": "UNKNOWN",
                                "error": classify_error(e)["user_message"],
                                "source_permissions": src_perms,
                            }
                        )
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
                        mapping.append(
                            {
                                "type": "flow",
                                "id": rid,
                                "name": name,
                                "in_source": True,
                                "in_target": True,
                                "id_match": False,
                                "name_match": True,
                                "matched_by": "name",
                                "match_by": "name",
                                "target_id": matches[0],
                                "ambiguous": len(matches) > 1,
                                "action": "UPDATE",
                                "source_permissions": src_perms,
                                "target_permissions": tgt_perms,
                            }
                        )
                    else:
                        mapping.append(
                            {
                                "type": "flow",
                                "id": rid,
                                "name": name,
                                "in_source": True,
                                "in_target": False,
                                "id_match": False,
                                "name_match": False,
                                "matched_by": "none",
                                "match_by": "name",
                                "action": "CREATE",
                                "source_permissions": src_perms,
                                "target_permissions": [],
                            }
                        )
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
                    mapping.append(
                        {
                            "type": rtype_name,
                            "id": rid,
                            "name": name,
                            "in_source": True,
                            "in_target": None,
                            "id_match": None,
                            "name_match": None,
                            "matched_by": "unknown",
                            "action": "UNKNOWN",
                            "error": err,
                            "source_permissions": src_perms,
                        }
                    )
                    continue

                # Name match: does any target resource of this type share the
                # name? Uses the per-type cached name-set (listed once).
                names = _target_name_set(rtype_name)
                if names is None:
                    name_match = None  # could not determine
                else:
                    name_match = (name or "").strip().lower() in names

                matched_by = (
                    "id+name"
                    if (id_exists and name_match)
                    else "id"
                    if id_exists
                    else "name"
                    if name_match
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
                    mapping.append(
                        {
                            "type": rtype_name,
                            "id": rid,
                            "name": name,
                            "in_source": True,
                            "in_target": True,
                            "id_match": True,
                            "name_match": name_match,
                            "matched_by": matched_by,
                            "action": "UPDATE",
                            "source_permissions": src_perms,
                            "target_permissions": tgt_perms,
                        }
                    )
                else:
                    # No id match → migrate will CREATE (reusing the source id),
                    # even if a same-name resource exists (that would duplicate
                    # by name; the FE can warn via name_match).
                    mapping.append(
                        {
                            "type": rtype_name,
                            "id": rid,
                            "name": name,
                            "in_source": True,
                            "in_target": False,
                            "id_match": False,
                            "name_match": name_match,
                            "matched_by": matched_by,
                            "action": "CREATE",
                            "source_permissions": src_perms,
                            "target_permissions": [],
                        }
                    )
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
            {
                "status": "FAILED",
                "user_message": f"No backups found for asset {asset_id!r}.",
            },
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
            {"status": "FAILED", "user_message": f"Could not read backup: {e}"},
            indent=2,
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
                if (
                    e.response.get("Error", {}).get("Code")
                    == "ResourceNotFoundException"
                ):
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
                add_resources.append(
                    {
                        "ResourceType": r.get("resourceType"),
                        "ResourceDetails": {
                            "resourceArn": remap_arn(src_arn, target_account_id, region)
                        },
                    }
                )
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
        report["errors"].append(format_error_for_report(f"restore_{rtype}({rid})", e))
        return f"FAILED: {classify_error(e)['user_message']}"


@mcp.tool()
def restore_backup(asset_id: str, version: int = 0, region: str = "us-east-1") -> str:
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
            {"status": "FAILED", "user_message": f"No backups for {asset_id!r}."},
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
            {"status": "FAILED", "user_message": f"Could not read backup: {e}"},
            indent=2,
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
    result_status = _restore_resource(
        target_qs, target_account_id, region, envelope, report
    )

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
