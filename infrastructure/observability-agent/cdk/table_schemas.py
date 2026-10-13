"""
Glue Data Catalog table schemas for Amazon Quick observability data.

Each entry defines one Athena/Glue table over the observability S3 data lake:
its logical name, the Quick data source it stores, and its ordered column
list. These same tables back the QuickSight datasets.

Columns use Hive/Athena primitive type names (string, bigint, double,
boolean) so the dicts can be used directly for `aws_glue.CfnTable` column
definitions and to render Athena DDL.

Two groups of tables:

* Vended-log tables (`VENDED_LOG_TABLES`) are populated by CloudWatch Logs
  vended-log delivery straight to S3 (JSON records, one object per batch).
  The table name maps to the S3 prefix under the data-lake bucket.
* Derived tables (`DERIVED_TABLES`) are populated by the CloudTrail poller
  and the CloudWatch-metrics poller Lambdas, which write newline-delimited
  JSON to their own prefixes.

`event_timestamp` / `event_time` / `snapshot_timestamp` are epoch
`event_timestamp` / `event_time` / `snapshot_timestamp` are epoch
seconds stored as bigint; the MCP and QuickSight derive readable
timestamps with Athena `from_unixtime(col)`.
"""

from __future__ import annotations

# Column list for one table: (name, hive_type) in storage order.
Column = tuple[str, str]


# --------------------------------------------------------------------------
# Vended CloudWatch log tables (delivered to S3 by CloudWatch Logs delivery).
#
# The dict key is BOTH the Glue/Athena table name and the S3 prefix. The
# `log_type` in LOG_TYPE_BY_TABLE is the CloudWatch vended `logType` passed to
# PutDeliverySource.
# --------------------------------------------------------------------------
VENDED_LOG_TABLES: dict[str, list[Column]] = {
    # CHAT_LOGS
    "chat_events": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("user_type", "string"),
        ("status_code", "string"),
        ("conversation_id", "string"),
        ("system_message_id", "string"),
        ("message_scope", "string"),
        ("user_message_id", "string"),
        ("user_message", "string"),
        ("agent_id", "string"),
        ("flow_id", "string"),
        ("system_text_message", "string"),
        ("resource_arn", "string"),
        ("selected_resource_id", "string"),
        ("selected_resource_type", "string"),
        ("action_connector_id", "string"),
        ("cited_resource_type", "string"),
        ("cited_resource_id", "string"),
        ("cited_resource_name", "string"),
        ("file_attachment_type", "string"),
        ("file_attachment_name", "string"),
    ],
    # FEEDBACK_LOGS
    "feedback_events": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("user_type", "string"),
        ("status_code", "string"),
        ("conversation_id", "string"),
        ("system_message_id", "string"),
        ("user_message_id", "string"),
        ("feedback_type", "string"),
        ("feedback_reason", "string"),
        ("feedback_details", "string"),
        ("resource_arn", "string"),
    ],
    # AGENT_HOURS_LOGS
    "agent_hours": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("subscription_type", "string"),
        ("reporting_service", "string"),
        ("usage_group", "string"),
        ("usage_hours", "double"),
        ("service_resource_arn", "string"),
        ("resource_arn", "string"),
    ],
    # AGENT_METADATA_LOGS
    "agent_metadata": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("event_name", "string"),
        ("event_version", "string"),
        ("agent_id", "string"),
        ("agent_arn", "string"),
        ("agent_name", "string"),
        ("agent_status", "string"),
        ("request_id", "string"),
        ("description", "string"),
        ("update_action", "string"),
        ("version", "string"),
        ("icon_id", "string"),
        ("space_arn", "string"),
        ("resource_arn", "string"),
    ],
    # INDEX_USAGE_LOGS
    "index_usage": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("source_type", "string"),
        ("source_name", "string"),
        ("source_arn", "string"),
        ("consumed_index_size", "bigint"),
        ("consumed_source_size", "bigint"),
        ("consumed_source_doc_count", "bigint"),
        ("resource_arn", "string"),
    ],
    # KB_FILE_SYNC_LOGS
    "kb_file_sync": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("document_id", "string"),
        ("document_title", "string"),
        ("document_status", "string"),
        ("sync_result", "string"),
        ("sync_id", "string"),
        ("data_source_id", "string"),
        ("source_uri", "string"),
        ("error_message", "string"),
        ("error_mitigation", "string"),
        ("error_type", "string"),
        ("knowledge_base_id", "string"),
        ("resource_arn", "string"),
    ],
    # DLP_LOGS
    "dlp_events": [
        ("event_timestamp", "bigint"),
        ("account_id", "string"),
        ("user_arn", "string"),
        ("event_type", "string"),
        ("request_id", "string"),
        ("dlp_job_id", "string"),
        ("dlp_setting_id", "string"),
        ("policy_action", "string"),
        ("file_name", "string"),
        ("file_size", "bigint"),
        ("failure_type", "string"),
        ("policy_message", "string"),
        ("dlp_setting_name", "string"),
        ("status", "string"),
        ("provider_type", "string"),
        ("auth_type", "string"),
        ("provider_outage_mode", "string"),
        ("unmapped_action", "string"),
        ("last_updated_by", "string"),
        ("resource_arn", "string"),
    ],
}

# CloudWatch vended logType for each vended-log table.
LOG_TYPE_BY_TABLE: dict[str, str] = {
    "chat_events": "CHAT_LOGS",
    "feedback_events": "FEEDBACK_LOGS",
    "agent_hours": "AGENT_HOURS_LOGS",
    "agent_metadata": "AGENT_METADATA_LOGS",
    "index_usage": "INDEX_USAGE_LOGS",
    "kb_file_sync": "KB_FILE_SYNC_LOGS",
    "dlp_events": "DLP_LOGS",
}


# --------------------------------------------------------------------------
# Derived tables, written by poller Lambdas as newline-delimited JSON.
# --------------------------------------------------------------------------
DERIVED_TABLES: dict[str, list[Column]] = {
    # CloudTrail: management events + InvokeAction data events.
    "cloudtrail_events": [
        ("event_time", "bigint"),
        ("event_id", "string"),
        ("event_name", "string"),
        ("event_source", "string"),
        ("event_category", "string"),
        ("aws_region", "string"),
        ("user_name", "string"),
        ("user_identity_type", "string"),
        ("user_identity_arn", "string"),
        ("on_behalf_of_user_id", "string"),
        ("source_ip_address", "string"),
        ("user_agent", "string"),
        ("read_only", "boolean"),
        ("error_code", "string"),
        ("error_message", "string"),
        ("resource_type", "string"),
        ("resource_name", "string"),
        ("action_connector_id", "string"),
        ("action_id", "string"),
        ("end_user_arn", "string"),
        ("invoke_action_status", "string"),
        ("invoke_http_status_code", "string"),
    ],
    # AWS/QuickSight CloudWatch metrics, polled periodic snapshots.
    "metric_snapshots": [
        ("snapshot_timestamp", "bigint"),
        ("metric_group", "string"),
        ("metric_name", "string"),
        ("statistic", "string"),
        ("value", "double"),
        ("unit", "string"),
        ("dashboard_id", "string"),
        ("dataset_id", "string"),
        ("sheet_id", "string"),
        ("visual_id", "string"),
        ("knowledge_base_id", "string"),
        ("quick_instance_id", "string"),
        ("action_connector_id", "string"),
        ("action_connector_type", "string"),
        ("action_id", "string"),
        ("automation_id", "string"),
        ("invoke_error_code", "string"),
    ],
}


# All tables keyed by name, for callers that iterate over the full catalog.
ALL_TABLES: dict[str, list[Column]] = {**VENDED_LOG_TABLES, **DERIVED_TABLES}
