"""
Quick Observability Lambda Handler for AgentCore Gateway.

Exposes two MCP tools:

* `quick_observability` — runs a read-only Athena (Presto/Trino) SQL query
  against the Quick observability data lake. The Quick agent translates the
  user's natural-language question into SQL (the tool description carries the
  full table/column schema) and this handler executes it.

* `export_user_data` — admin-run DSAR / data-portability export: gathers one
  user's rows across the pipeline tables (chat, feedback, agent hours, index
  usage, KB sync, DLP, audit) into a single portable JSON bundle written to
  the data lake's `dsar-exports/` prefix, and returns a short-lived pre-signed
  download URL. Scope note: this covers ONLY data captured by this pipeline
  (the CHAT_LOGS vended stream + metrics/audit) — NOT memories, knowledge
  graph, spaces, agents, or uploaded-file content, which are not captured here.

Tables (Glue database `quick_observability`):
  Vended CloudWatch logs: chat_events, feedback_events, agent_hours,
    agent_metadata, index_usage, kb_file_sync, dlp_events
  Poller output: cloudtrail_events, metric_snapshots

All timestamps are epoch seconds (bigint); convert with
from_unixtime(col).
"""

import datetime
import json
import logging
import os

import athena_client
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_s3 = boto3.client("s3")
DATA_BUCKET = os.environ.get("DATA_BUCKET", "")

# Statements the single read-only tool is allowed to run.
_ALLOWED_PREFIXES = ("select", "with", "show", "describe", "explain")

# Tables exported for a DSAR, with the user-identifier column to filter on.
# cloudtrail_events uses event_time; it has no user_arn, so match the audit
# identity columns instead.
_DSAR_TABLES = {
    "chat_events": "user_arn",
    "feedback_events": "user_arn",
    "agent_hours": "user_arn",
    "agent_metadata": "user_arn",
    "index_usage": "user_arn",
    "dlp_events": "user_arn",
}


def _is_read_only(sql: str) -> bool:
    """Allow a single read-only statement only."""
    stripped = sql.strip().rstrip(";").lstrip()
    if not stripped:
        return False
    # Reject multiple statements.
    if ";" in stripped:
        return False
    lowered = stripped.lower()
    return lowered.startswith(_ALLOWED_PREFIXES)


def _sql_escape(value: str) -> str:
    return str(value).replace("'", "''")


def handler(event, context):
    """AgentCore Gateway Lambda handler."""
    logger.info("=" * 80)
    logger.info("INCOMING REQUEST")
    logger.info(f"Full Event: {json.dumps(event, default=str, indent=2)}")
    logger.info("=" * 80)

    try:
        result = _handle_request(event, context)
        body_str = result.get("body", "")
        logger.info(f"Status: {result.get('statusCode')}")
        logger.info(
            f"Body: {body_str[:1000] + '...' if len(body_str) > 1000 else body_str}"
        )
        return result
    except Exception as e:
        logger.error(f"FATAL ERROR in handler: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def _resolve_tool_name(event, context):
    """Resolve the invoked tool name from AgentCore context or the event."""
    tool_name = None
    client_context = getattr(context, "client_context", None)
    if client_context and getattr(client_context, "custom", None):
        tool_name = client_context.custom.get("bedrockAgentCoreToolName")

    if not tool_name and isinstance(event, dict):
        tool_name = (
            event.get("toolName")
            or event.get("tool_name")
            or event.get("bedrockAgentCoreToolName")
        )
        headers = event.get("headers", {}) or {}
        tool_name = tool_name or headers.get("bedrockAgentCoreToolName")

    # Handle prefixed format: target-name___tool_name
    if tool_name and "___" in tool_name:
        tool_name = tool_name.split("___")[-1]
    return tool_name


def _handle_request(event, context):
    """Route the request to the Athena query tool or the DSAR export tool."""
    tool_name = _resolve_tool_name(event, context)
    logger.info(f"Tool name: {tool_name}")

    parameters = event if isinstance(event, dict) else {}

    if tool_name == "quick_observability":
        return _run_query_tool(parameters)
    if tool_name == "export_user_data":
        return _export_user_data_tool(parameters)
    return {
        "statusCode": 400,
        "body": json.dumps({"error": f"Unknown tool: {tool_name}"}),
    }


def _run_query_tool(parameters):
    sql = (parameters.get("sql") or "").strip()
    max_rows = int(parameters.get("max_rows", 1000))

    if not sql:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "sql is required"}),
        }

    if not _is_read_only(sql):
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "error": (
                        "Only a single read-only statement is allowed "
                        "(SELECT, WITH, SHOW, DESCRIBE, EXPLAIN)."
                    )
                }
            ),
        }

    result = athena_client.run_query(sql, max_rows=max_rows)
    status = 200 if result.get("status") == "success" else 500
    return {"statusCode": status, "body": json.dumps(result, default=str)}


def _export_user_data_tool(parameters):
    """Assemble a portable per-user export bundle and return a pre-signed URL."""
    user_arn = (parameters.get("user_arn") or parameters.get("user") or "").strip()
    if not user_arn:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "user_arn (or user) is required"}),
        }
    if not DATA_BUCKET:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "DATA_BUCKET is not configured"}),
        }

    try:
        max_rows = int(parameters.get("max_rows_per_table", 10000))
    except (TypeError, ValueError):
        max_rows = 10000

    esc = _sql_escape(user_arn)
    # Match on the full ARN or its trailing user token (handles bare names).
    token = user_arn.rstrip("/").split("/")[-1]
    esc_token = _sql_escape(token)

    bundle = {
        "user_arn": user_arn,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "scope_note": (
            "Covers data captured by the Quick observability pipeline only "
            "(CHAT_LOGS vended stream + metrics/audit). Does NOT include "
            "memories, knowledge graph, spaces, agents, or uploaded-file "
            "content, which are not captured by this pipeline."
        ),
        "tables": {},
        "errors": {},
    }

    for table, col in _DSAR_TABLES.items():
        sql = (
            f"SELECT * FROM {table} WHERE {col} = '{esc}' OR {col} LIKE '%{esc_token}'"
        )
        res = athena_client.run_query(sql, max_rows=max_rows)
        if res.get("status") == "success":
            bundle["tables"][table] = res.get("rows", [])
        else:
            bundle["errors"][table] = res.get("error")

    # Audit trail (cloudtrail_events) — match the user's audit identity columns.
    audit_sql = (
        "SELECT * FROM cloudtrail_events "
        f"WHERE user_name LIKE '%{esc_token}%' "
        f"OR user_identity_arn LIKE '%{esc_token}%' "
        f"OR on_behalf_of_user_id LIKE '%{esc_token}%'"
    )
    audit = athena_client.run_query(audit_sql, max_rows=max_rows)
    if audit.get("status") == "success":
        bundle["tables"]["cloudtrail_events"] = audit.get("rows", [])
    else:
        bundle["errors"]["cloudtrail_events"] = audit.get("error")

    row_total = sum(len(v) for v in bundle["tables"].values())
    key = (
        "dsar-exports/"
        f"{_s3_safe(token)}/"
        f"{datetime.datetime.now(datetime.UTC):%Y%m%dT%H%M%SZ}-export.json"
    )
    body = json.dumps(bundle, default=str, indent=2).encode("utf-8")
    _s3.put_object(
        Bucket=DATA_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    try:
        url = _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": DATA_BUCKET, "Key": key},
            ExpiresIn=3600,
        )
    except Exception:  # noqa: BLE001
        url = None

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "user_arn": user_arn,
                "total_rows": row_total,
                "tables_included": sorted(bundle["tables"].keys()),
                "s3_key": key,
                "download_url": url,
                "scope_note": bundle["scope_note"],
                "errors": bundle["errors"],
            },
            default=str,
        ),
    }


def _s3_safe(token: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", (token or "").strip()).strip("-")
    return safe or "user"
