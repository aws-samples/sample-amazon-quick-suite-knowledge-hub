"""
Quick Observability Lambda Handler for AgentCore Gateway

Provides 15 custom tools for Quick monitoring:

CloudWatch Logs Tools (7):
- get_chat_conversations
- get_chat_errors
- get_chat_performance
- get_user_feedback
- get_feedback_summary
- get_agent_hours_usage
- search_chat_by_query

CloudWatch Metrics Tools (6):
- get_dashboard_metrics (enhanced)
- get_ingestion_metrics
- get_visual_metrics
- get_knowledge_base_metrics
- get_action_connector_metrics
- get_spice_capacity

CloudTrail Tools (1):
- get_quicksight_api_calls
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logs_client = boto3.client("logs")
cloudwatch_client = boto3.client("cloudwatch")
cloudtrail_client = boto3.client("cloudtrail")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logs_client = boto3.client("logs")
cloudwatch_client = boto3.client("cloudwatch")

# Log groups from environment variables
CHAT_LOG_GROUP = os.environ.get("CHAT_LOG_GROUP", "/aws/quicksuite/chat")
FEEDBACK_LOG_GROUP = os.environ.get("FEEDBACK_LOG_GROUP", "/aws/quicksuite/feedback")
AGENT_HOURS_LOG_GROUP = os.environ.get(
    "AGENT_HOURS_LOG_GROUP", "/aws/quicksuite/agent-hours"
)


def get_metric_data(metric_name, dimensions, hours, statistic):
    """Helper to get CloudWatch metric data"""
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    # Use appropriate period based on time range
    # CloudWatch requires: period * datapoints <= 1440
    if hours <= 24:
        period = 3600  # 1 hour
    elif hours <= 168:  # 7 days
        period = 3600 * 6  # 6 hours
    else:
        period = 86400  # 1 day

    try:
        response = cloudwatch_client.get_metric_statistics(
            Namespace="AWS/QuickSight",
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=[statistic],
        )
        total = sum(dp[statistic] for dp in response.get("Datapoints", []))
        logger.info(
            f"Metric {metric_name} with dims {dimensions}: {total} ({len(response.get('Datapoints', []))} datapoints)"
        )
        return total
    except Exception as e:
        logger.error(f"Error getting metric {metric_name}: {str(e)}")
        return 0


def list_dimension_values(metric_name, dimension_name):
    """List all unique values for a dimension with pagination"""
    values = set()
    next_token = None

    try:
        while True:
            params = {"Namespace": "AWS/QuickSight", "MetricName": metric_name}
            if next_token:
                params["NextToken"] = next_token

            response = cloudwatch_client.list_metrics(**params)

            for metric in response.get("Metrics", []):
                for dim in metric.get("Dimensions", []):
                    if dim["Name"] == dimension_name:
                        values.add(dim["Value"])

            next_token = response.get("NextToken")
            if not next_token:
                break

        logger.info(
            f"Found {len(values)} unique values for {dimension_name} in {metric_name}"
        )
        return list(values)
    except Exception as e:
        logger.error(f"Error listing dimension values: {str(e)}")
        return []


def execute_logs_query(log_group, query, hours=24):
    """Execute CloudWatch Logs Insights query"""
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=hours)

    try:
        response = logs_client.start_query(
            logGroupName=log_group,
            startTime=int(start_time.timestamp()),
            endTime=int(end_time.timestamp()),
            queryString=query,
        )

        query_id = response["queryId"]

        # Wait for query
        import time

        while True:
            result = logs_client.get_query_results(queryId=query_id)
            if result["status"] == "Complete":
                return {"status": "success", "results": result["results"]}
            elif result["status"] == "Failed":
                return {"status": "failed", "error": "Query failed"}
            time.sleep(0.5)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def handler(event, context):
    """AgentCore Gateway Lambda handler with comprehensive logging"""

    # Log the entire incoming request
    logger.info("=" * 80)
    logger.info("INCOMING REQUEST")
    logger.info(f"Full Event: {json.dumps(event, default=str, indent=2)}")
    logger.info("=" * 80)

    try:
        result = _handle_request(event, context)

        # Log the response
        logger.info("=" * 80)
        logger.info("OUTGOING RESPONSE")
        logger.info(f"Status: {result.get('statusCode')}")
        body_str = result.get("body", "")
        if len(body_str) > 1000:
            logger.info(f"Body (truncated): {body_str[:1000]}...")
        else:
            logger.info(f"Body: {body_str}")
        logger.info("=" * 80)

        return result

    except Exception as e:
        logger.error(f"FATAL ERROR in handler: {str(e)}", exc_info=True)
        error_response = {"statusCode": 500, "body": json.dumps({"error": str(e)})}
        logger.info(f"ERROR RESPONSE: {error_response}")
        return error_response


def _handle_request(event, context):
    """Internal request handler"""
    try:
        # Log context details
        logger.info(
            f"Context client_context: {getattr(context, 'client_context', None)}"
        )
        if hasattr(context, "client_context") and context.client_context:
            logger.info(
                f"Context custom: {getattr(context.client_context, 'custom', None)}"
            )

        # Extract tool name from event if not in context
        if not (
            context.client_context
            and hasattr(context.client_context, "custom")
            and context.client_context.custom.get("bedrockAgentCoreToolName")
        ):
            tool_name = None
            if isinstance(event, dict):
                tool_name = (
                    event.get("toolName")
                    or event.get("tool_name")
                    or event.get("bedrockAgentCoreToolName")
                )
                headers = event.get("headers", {})
                if headers:
                    tool_name = tool_name or headers.get("bedrockAgentCoreToolName")

                logger.info(f"Extracted tool_name from event: {tool_name}")

            if tool_name:
                if not hasattr(context, "client_context") or not context.client_context:
                    context.client_context = type("ClientContext", (), {})()
                if not hasattr(context.client_context, "custom"):
                    context.client_context.custom = {}
                context.client_context.custom["bedrockAgentCoreToolName"] = tool_name

        # Get tool name from context
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName")

        # Handle prefixed format: target-name___tool_name
        if tool_name and "___" in tool_name:
            tool_name = tool_name.split("___")[-1]

        logger.info(f"Tool name: {tool_name}")
        logger.info(f"Parameters received: {json.dumps(event, default=str)}")

        # Parameters are in the event root
        parameters = event

        # Route to appropriate tool
        if tool_name == "get_chat_conversations":
            hours = parameters.get("hours", 24)

            query = """
            fields @timestamp, conversation_id, user_message, system_text_message, status_code, user_type, agent_id, flow_id
            | sort @timestamp desc
            """

            result = execute_logs_query(CHAT_LOG_GROUP, query, hours)
            if result["status"] == "success":
                conversations = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "total_conversations": len(conversations),
                            "time_range_hours": hours,
                            "conversations": conversations,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "get_chat_errors":
            hours = parameters.get("hours", 24)

            query = """
            fields @timestamp, conversation_id, user_message, status_code, user_type, agent_id, flow_id
            | filter status_code != "success"
            | sort @timestamp desc
            | limit 100
            """

            result = execute_logs_query(CHAT_LOG_GROUP, query, hours)
            if result["status"] == "success":
                errors = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                error_counts = {}
                for error in errors:
                    status = error.get("status_code", "unknown")
                    error_counts[status] = error_counts.get(status, 0) + 1

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "total_errors": len(errors),
                            "time_range_hours": hours,
                            "error_breakdown": error_counts,
                            "errors": errors,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "get_chat_performance":
            hours = parameters.get("hours", 24)

            # Get total conversations (unique conversation_ids)
            query_conversations = """
            fields conversation_id
            | stats count_distinct(conversation_id) as total_conversations
            """

            # Get total queries (all messages)
            query_queries = """
            fields @timestamp
            | stats count(*) as total_queries
            """

            # Get successful requests
            query_success = """
            fields @timestamp
            | filter status_code = "success"
            | stats count(*) as successful_requests
            """

            # Get unique users
            query_users = """
            fields user_arn
            | stats count_distinct(user_arn) as unique_users
            """

            result_conv = execute_logs_query(CHAT_LOG_GROUP, query_conversations, hours)
            result_queries = execute_logs_query(CHAT_LOG_GROUP, query_queries, hours)
            result_success = execute_logs_query(CHAT_LOG_GROUP, query_success, hours)
            result_users = execute_logs_query(CHAT_LOG_GROUP, query_users, hours)

            total_conversations = (
                int(result_conv["results"][0][0]["value"])
                if result_conv["status"] == "success" and result_conv["results"]
                else 0
            )
            total_queries = (
                int(result_queries["results"][0][0]["value"])
                if result_queries["status"] == "success" and result_queries["results"]
                else 0
            )
            successful_requests = (
                int(result_success["results"][0][0]["value"])
                if result_success["status"] == "success" and result_success["results"]
                else 0
            )
            unique_users = (
                int(result_users["results"][0][0]["value"])
                if result_users["status"] == "success" and result_users["results"]
                else 0
            )

            avg_queries_per_user = (
                round(total_queries / unique_users, 2) if unique_users > 0 else 0
            )
            avg_queries_per_conversation = (
                round(total_queries / total_conversations, 2)
                if total_conversations > 0
                else 0
            )
            success_rate = (
                round((successful_requests / total_queries * 100), 2)
                if total_queries > 0
                else 0
            )

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_conversations": total_conversations,
                        "total_queries": total_queries,
                        "unique_users": unique_users,
                        "successful_requests": successful_requests,
                        "success_rate_percent": success_rate,
                        "avg_queries_per_user": avg_queries_per_user,
                        "avg_queries_per_conversation": avg_queries_per_conversation,
                    }
                ),
            }

        elif tool_name == "get_user_feedback":
            hours = parameters.get("hours", 24)
            feedback_type = parameters.get("feedback_type", "all")

            filter_clause = (
                f'filter feedback_type = "{feedback_type}"'
                if feedback_type != "all"
                else ""
            )

            query = f"""
            fields @timestamp, conversation_id, feedback_type, feedback_reason, feedback_details, user_type
            {filter_clause}
            | sort @timestamp desc
            | limit 100
            """

            result = execute_logs_query(FEEDBACK_LOG_GROUP, query, hours)
            if result["status"] == "success":
                feedback_items = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "total_feedback": len(feedback_items),
                            "time_range_hours": hours,
                            "filter": feedback_type,
                            "feedback": feedback_items,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "get_feedback_summary":
            hours = parameters.get("hours", 24)

            query = """
            fields feedback_type, feedback_reason
            | stats count(*) as count by feedback_type, feedback_reason
            | sort count desc
            """

            result = execute_logs_query(FEEDBACK_LOG_GROUP, query, hours)
            if result["status"] == "success":
                summary = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                total_useful = sum(
                    int(s.get("count", 0))
                    for s in summary
                    if s.get("feedback_type") == "Useful"
                )
                total_not_useful = sum(
                    int(s.get("count", 0))
                    for s in summary
                    if s.get("feedback_type") == "Not Useful"
                )
                total = total_useful + total_not_useful

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "time_range_hours": hours,
                            "total_feedback": total,
                            "useful_count": total_useful,
                            "not_useful_count": total_not_useful,
                            "useful_percentage": round(
                                (total_useful / total * 100) if total > 0 else 0, 2
                            ),
                            "breakdown": summary,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "get_agent_hours_usage":
            hours = parameters.get("hours", 720)

            query = """
            fields reporting_service, usage_hours, usage_group
            | stats sum(usage_hours) as total_hours by reporting_service, usage_group
            | sort total_hours desc
            """

            result = execute_logs_query(AGENT_HOURS_LOG_GROUP, query, hours)
            if result["status"] == "success":
                usage = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                total_hours = sum(float(u.get("total_hours", 0)) for u in usage)

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "time_range_hours": hours,
                            "total_agent_hours": round(total_hours, 2),
                            "usage_by_service": usage,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "search_chat_by_query":
            search_term = parameters.get("search_term", "")
            hours = parameters.get("hours", 24)

            if not search_term:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "search_term is required"}),
                }

            # Escape special regex characters
            import re

            escaped_term = re.escape(search_term)

            query = f"""
            fields @timestamp, conversation_id, user_message, system_text_message, status_code, agent_id, flow_id
            | filter user_message like /(?i){escaped_term}/ or system_text_message like /(?i){escaped_term}/
            | sort @timestamp desc
            | limit 50
            """

            result = execute_logs_query(CHAT_LOG_GROUP, query, hours)
            if result["status"] == "success":
                matches = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]
                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "search_term": search_term,
                            "total_matches": len(matches),
                            "time_range_hours": hours,
                            "matches": matches,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        elif tool_name == "get_dashboard_metrics":
            hours = parameters.get("hours", 24)

            # Get all dashboard IDs
            dashboard_ids = list_dimension_values("DashboardViewCount", "DashboardId")

            dashboards = []
            total_views = 0
            total_load_time = 0
            count = 0

            for dashboard_id in dashboard_ids:
                dimensions = [{"Name": "DashboardId", "Value": dashboard_id}]

                views = get_metric_data("DashboardViewCount", dimensions, hours, "Sum")
                load_time = get_metric_data(
                    "DashboardViewLoadTime", dimensions, hours, "Average"
                )

                if views > 0:
                    dashboards.append(
                        {
                            "dashboard_id": dashboard_id,
                            "view_count": int(views),
                            "avg_load_time_ms": round(load_time, 2),
                        }
                    )
                    total_views += views
                    if load_time > 0:
                        total_load_time += load_time
                        count += 1

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_dashboards": len(dashboards),
                        "total_views": int(total_views),
                        "avg_load_time_ms": round(total_load_time / count, 2)
                        if count > 0
                        else 0,
                        "dashboards": sorted(
                            dashboards, key=lambda x: x["view_count"], reverse=True
                        ),
                    }
                ),
            }

        elif tool_name == "get_ingestion_metrics":
            hours = parameters.get("hours", 24)

            # Get all dataset IDs
            dataset_ids = list_dimension_values("IngestionInvocationCount", "DatasetId")

            datasets = []
            total_invocations = 0
            total_errors = 0
            total_rows = 0

            for dataset_id in dataset_ids:
                dimensions = [{"Name": "DatasetId", "Value": dataset_id}]

                invocations = get_metric_data(
                    "IngestionInvocationCount", dimensions, hours, "Sum"
                )
                errors = get_metric_data(
                    "IngestionErrorCount", dimensions, hours, "Sum"
                )
                latency = get_metric_data(
                    "IngestionLatency", dimensions, hours, "Average"
                )
                rows = get_metric_data("IngestionRowCount", dimensions, hours, "Sum")

                if invocations > 0:
                    datasets.append(
                        {
                            "dataset_id": dataset_id,
                            "invocations": int(invocations),
                            "errors": int(errors),
                            "success_rate": round(
                                (invocations - errors) / invocations * 100, 2
                            )
                            if invocations > 0
                            else 0,
                            "avg_latency_seconds": round(latency, 2),
                            "rows_ingested": int(rows),
                        }
                    )
                    total_invocations += invocations
                    total_errors += errors
                    total_rows += rows

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_datasets": len(datasets),
                        "total_invocations": int(total_invocations),
                        "total_errors": int(total_errors),
                        "total_rows_ingested": int(total_rows),
                        "overall_success_rate": round(
                            (total_invocations - total_errors)
                            / total_invocations
                            * 100,
                            2,
                        )
                        if total_invocations > 0
                        else 0,
                        "datasets": sorted(
                            datasets, key=lambda x: x["invocations"], reverse=True
                        ),
                    }
                ),
            }

        elif tool_name == "get_visual_metrics":
            hours = parameters.get("hours", 24)

            # Get all visual IDs
            visual_ids = list_dimension_values("VisualLoadTime", "VisualId")

            visuals = []
            total_load_time = 0
            total_errors = 0
            count = 0

            for visual_id in visual_ids:
                # Visual metrics have multiple dimensions
                response = cloudwatch_client.list_metrics(
                    Namespace="AWS/QuickSight", MetricName="VisualLoadTime"
                )

                for metric in response.get("Metrics", []):
                    dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
                    if dims.get("VisualId") == visual_id:
                        dimensions = [
                            {
                                "Name": "DashboardId",
                                "Value": dims.get("DashboardId", ""),
                            },
                            {"Name": "SheetId", "Value": dims.get("SheetId", "")},
                            {"Name": "VisualId", "Value": visual_id},
                        ]

                        load_time = get_metric_data(
                            "VisualLoadTime", dimensions, hours, "Average"
                        )
                        errors = get_metric_data(
                            "VisualLoadErrorCount", dimensions, hours, "Sum"
                        )

                        if load_time > 0 or errors > 0:
                            visuals.append(
                                {
                                    "dashboard_id": dims.get("DashboardId", ""),
                                    "sheet_id": dims.get("SheetId", ""),
                                    "visual_id": visual_id,
                                    "avg_load_time_ms": round(load_time, 2),
                                    "error_count": int(errors),
                                }
                            )
                            if load_time > 0:
                                total_load_time += load_time
                                count += 1
                            total_errors += errors
                        break

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_visuals": len(visuals),
                        "avg_load_time_ms": round(total_load_time / count, 2)
                        if count > 0
                        else 0,
                        "total_errors": int(total_errors),
                        "visuals": sorted(
                            visuals, key=lambda x: x["avg_load_time_ms"], reverse=True
                        )[:50],
                    }
                ),
            }

        elif tool_name == "get_knowledge_base_metrics":
            hours = parameters.get("hours", 24)

            logger.info("Getting knowledge base metrics...")

            # Get QuickInstanceId (not KnowledgeBaseId!)
            instance_ids = list_dimension_values(
                "QuickIndexDocumentCount", "QuickInstanceId"
            )
            logger.info(f"Found {len(instance_ids)} QuickInstanceIds: {instance_ids}")

            knowledge_bases = []
            total_docs = 0

            for instance_id in instance_ids:
                dimensions = [{"Name": "QuickInstanceId", "Value": instance_id}]

                doc_count = get_metric_data(
                    "QuickIndexDocumentCount", dimensions, hours, "Average"
                )

                knowledge_bases.append(
                    {
                        "quick_instance_id": instance_id,
                        "total_documents": int(doc_count),
                    }
                )
                total_docs += doc_count

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_instances": len(knowledge_bases),
                        "total_documents": int(total_docs),
                        "instances": knowledge_bases,
                    }
                ),
            }

        elif tool_name == "get_action_connector_metrics":
            hours = parameters.get("hours", 24)

            # Get all action connector IDs
            connector_ids = list_dimension_values(
                "ActionInvocationCount", "ActionConnectorId"
            )

            connectors = []
            total_invocations = 0
            total_errors = 0

            for connector_id in connector_ids:
                dimensions = [{"Name": "ActionConnectorId", "Value": connector_id}]

                invocations = get_metric_data(
                    "ActionInvocationCount", dimensions, hours, "Sum"
                )
                errors = get_metric_data(
                    "ActionInvocationError", dimensions, hours, "Sum"
                )

                if invocations > 0:
                    connectors.append(
                        {
                            "action_connector_id": connector_id,
                            "total_invocations": int(invocations),
                            "total_errors": int(errors),
                            "success_rate": round(
                                (invocations - errors) / invocations * 100, 2
                            )
                            if invocations > 0
                            else 0,
                        }
                    )
                    total_invocations += invocations
                    total_errors += errors

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "total_connectors": len(connectors),
                        "total_invocations": int(total_invocations),
                        "total_errors": int(total_errors),
                        "overall_success_rate": round(
                            (total_invocations - total_errors)
                            / total_invocations
                            * 100,
                            2,
                        )
                        if total_invocations > 0
                        else 0,
                        "connectors": sorted(
                            connectors,
                            key=lambda x: x["total_invocations"],
                            reverse=True,
                        ),
                    }
                ),
            }

        elif tool_name == "get_aggregate_metrics":
            hours = parameters.get("hours", 24)

            logger.info("Getting aggregate account-wide metrics...")

            # Dashboard metrics (no dimensions for aggregate)
            dashboard_views = get_metric_data("DashboardViewCount", [], hours, "Sum")
            dashboard_load_time = get_metric_data(
                "DashboardViewLoadTime", [], hours, "Average"
            )

            # Ingestion metrics
            ingestion_invocations = get_metric_data(
                "IngestionInvocationCount", [], hours, "Sum"
            )
            ingestion_errors = get_metric_data("IngestionErrorCount", [], hours, "Sum")
            ingestion_latency = get_metric_data(
                "IngestionLatency", [], hours, "Average"
            )
            ingestion_rows = get_metric_data("IngestionRowCount", [], hours, "Sum")

            # Visual metrics
            visual_load_time = get_metric_data("VisualLoadTime", [], hours, "Average")
            visual_errors = get_metric_data("VisualLoadErrorCount", [], hours, "Sum")

            # Knowledge base metrics
            kb_doc_count = get_metric_data("QuickIndexDocumentCount", [], hours, "Sum")
            kb_text_size = get_metric_data(
                "QuickIndexExtractedTextSize", [], hours, "Sum"
            )
            kb_purchased = get_metric_data("QuickIndexPurchasedInMB", [], hours, "Sum")
            docs_crawled = get_metric_data("DocumentsCrawled", [], hours, "Sum")
            docs_indexed = get_metric_data("DocumentsIndexed", [], hours, "Sum")
            docs_failed = get_metric_data("DocumentsFailedToIndex", [], hours, "Sum")

            # Action connector metrics
            action_invocations = get_metric_data(
                "ActionInvocationCount", [], hours, "Sum"
            )
            action_errors = get_metric_data("ActionInvocationError", [], hours, "Sum")

            # SPICE capacity
            spice_limit = get_metric_data(
                "SPICECapacityLimitInMB", [], hours, "Average"
            )
            spice_consumed = get_metric_data(
                "SPICECapacityConsumedInMB", [], hours, "Average"
            )

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "dashboards": {
                            "total_views": int(dashboard_views),
                            "avg_load_time_ms": round(dashboard_load_time, 2),
                        },
                        "ingestions": {
                            "total_invocations": int(ingestion_invocations),
                            "total_errors": int(ingestion_errors),
                            "avg_latency_seconds": round(ingestion_latency, 2),
                            "total_rows": int(ingestion_rows),
                        },
                        "visuals": {
                            "avg_load_time_ms": round(visual_load_time, 2),
                            "total_errors": int(visual_errors),
                        },
                        "knowledge_bases": {
                            "total_documents": int(kb_doc_count),
                            "text_size_bytes": int(kb_text_size),
                            "purchased_mb": int(kb_purchased),
                            "documents_crawled": int(docs_crawled),
                            "documents_indexed": int(docs_indexed),
                            "documents_failed": int(docs_failed),
                        },
                        "action_connectors": {
                            "total_invocations": int(action_invocations),
                            "total_errors": int(action_errors),
                        },
                        "spice": {
                            "capacity_limit_mb": round(spice_limit, 2),
                            "capacity_consumed_mb": round(spice_consumed, 2),
                            "capacity_available_mb": round(
                                spice_limit - spice_consumed, 2
                            ),
                            "usage_percent": round(
                                (spice_consumed / spice_limit * 100)
                                if spice_limit > 0
                                else 0,
                                2,
                            ),
                        },
                    }
                ),
            }

        elif tool_name == "get_active_users":
            days = parameters.get("days", 30)

            logger.info(f"Getting active users for {days} days...")

            # Query chat logs for unique users (using user_arn field)
            end_time = int(datetime.now().timestamp())
            start_time_1d = int((datetime.now() - timedelta(days=1)).timestamp())
            start_time_7d = int((datetime.now() - timedelta(days=7)).timestamp())
            start_time_30d = int((datetime.now() - timedelta(days=days)).timestamp())

            # DAU - last 24 hours
            query_id_dau = logs_client.start_query(
                logGroupName="/aws/quicksuite/chat",
                startTime=start_time_1d,
                endTime=end_time,
                queryString="fields user_arn | stats count_distinct(user_arn) as dau",
            )["queryId"]

            # WAU - last 7 days
            query_id_wau = logs_client.start_query(
                logGroupName="/aws/quicksuite/chat",
                startTime=start_time_7d,
                endTime=end_time,
                queryString="fields user_arn | stats count_distinct(user_arn) as wau",
            )["queryId"]

            # MAU - last 30 days
            query_id_mau = logs_client.start_query(
                logGroupName="/aws/quicksuite/chat",
                startTime=start_time_30d,
                endTime=end_time,
                queryString="fields user_arn | stats count_distinct(user_arn) as mau",
            )["queryId"]

            # Wait for queries
            time.sleep(3)

            dau_result = logs_client.get_query_results(queryId=query_id_dau)
            wau_result = logs_client.get_query_results(queryId=query_id_wau)
            mau_result = logs_client.get_query_results(queryId=query_id_mau)

            dau = (
                int(dau_result["results"][0][0]["value"])
                if dau_result.get("results") and len(dau_result["results"]) > 0
                else 0
            )
            wau = (
                int(wau_result["results"][0][0]["value"])
                if wau_result.get("results") and len(wau_result["results"]) > 0
                else 0
            )
            mau = (
                int(mau_result["results"][0][0]["value"])
                if mau_result.get("results") and len(mau_result["results"]) > 0
                else 0
            )

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "daily_active_users": dau,
                        "weekly_active_users": wau,
                        "monthly_active_users": mau,
                        "analysis_period_days": days,
                    }
                ),
            }

        elif tool_name == "get_asset_usage":
            hours = parameters.get("hours", 24)
            asset_type = parameters.get(
                "asset_type", "all"
            )  # all, agent, flow, action, space

            logger.info(f"Getting asset usage for {hours} hours, type: {asset_type}")

            # Query to get full message (action_connectors is a complex field)
            query = """
            fields @message
            | sort @timestamp desc
            """

            result = execute_logs_query(CHAT_LOG_GROUP, query, hours)

            if result["status"] != "success":
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": "Failed to query asset usage"}),
                }

            # Process results
            agents = {}
            flows = {}
            actions = {}
            spaces = {}

            for item in result["results"]:
                message_field = next(
                    (f for f in item if f["field"] == "@message"), None
                )
                if not message_field:
                    continue

                try:
                    log_data = json.loads(message_field["value"])
                except Exception:
                    continue

                user_arn = log_data.get("user_arn", "")

                # Process agents
                agent_id = log_data.get("agent_id", "")
                if agent_id and agent_id != "-":
                    if agent_id not in agents:
                        agents[agent_id] = {"usage_count": 0, "users": set()}
                    agents[agent_id]["usage_count"] += 1
                    if user_arn:
                        agents[agent_id]["users"].add(user_arn)

                # Process flows
                flow_id = log_data.get("flow_id", "")
                if flow_id and flow_id != "-":
                    if flow_id not in flows:
                        flows[flow_id] = {"usage_count": 0, "users": set()}
                    flows[flow_id]["usage_count"] += 1
                    if user_arn:
                        flows[flow_id]["users"].add(user_arn)

                # Process actions
                action_connectors = log_data.get("action_connectors", [])
                if action_connectors:
                    for action in action_connectors:
                        action_id = action.get("actionConnectorId", "")
                        if action_id:
                            if action_id not in actions:
                                actions[action_id] = {"usage_count": 0, "users": set()}
                            actions[action_id]["usage_count"] += 1
                            if user_arn:
                                actions[action_id]["users"].add(user_arn)

                # Process spaces
                user_resources = log_data.get("user_selected_resources", [])
                if user_resources:
                    for resource in user_resources:
                        resource_id = resource.get("resourceId", "")
                        if resource_id and resource_id != "ALL":
                            if resource_id not in spaces:
                                spaces[resource_id] = {"usage_count": 0, "users": set()}
                            spaces[resource_id]["usage_count"] += 1
                            if user_arn:
                                spaces[resource_id]["users"].add(user_arn)

            # Format results based on asset_type filter
            all_assets = []

            if asset_type in ["all", "agent"]:
                for asset_id, data in agents.items():
                    all_assets.append(
                        {
                            "asset_name": f"AGENT-{asset_id}",
                            "asset_type": "AGENT",
                            "usage_count": data["usage_count"],
                            "user_count": len(data["users"]),
                        }
                    )

            if asset_type in ["all", "flow"]:
                for asset_id, data in flows.items():
                    all_assets.append(
                        {
                            "asset_name": f"FLOW-{asset_id}",
                            "asset_type": "FLOW",
                            "usage_count": data["usage_count"],
                            "user_count": len(data["users"]),
                        }
                    )

            if asset_type in ["all", "action"]:
                for asset_id, data in actions.items():
                    all_assets.append(
                        {
                            "asset_name": f"ACTION-{asset_id}",
                            "asset_type": "ACTION",
                            "usage_count": data["usage_count"],
                            "user_count": len(data["users"]),
                        }
                    )

            if asset_type in ["all", "space"]:
                for asset_id, data in spaces.items():
                    all_assets.append(
                        {
                            "asset_name": f"SPACE-{asset_id}",
                            "asset_type": "SPACE",
                            "usage_count": data["usage_count"],
                            "user_count": len(data["users"]),
                        }
                    )

            # Sort by usage count
            all_assets.sort(key=lambda x: x["usage_count"], reverse=True)

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "asset_type_filter": asset_type,
                        "total_assets": len(all_assets),
                        "total_agents": len(agents),
                        "total_flows": len(flows),
                        "total_actions": len(actions),
                        "total_spaces": len(spaces),
                        "assets": all_assets,
                    }
                ),
            }

        elif tool_name == "get_spice_capacity":
            hours = parameters.get("hours", 24)

            # SPICE metrics are aggregate only (no dimensions)
            capacity_limit = get_metric_data(
                "SPICECapacityLimitInMB", [], hours, "Average"
            )
            capacity_consumed = get_metric_data(
                "SPICECapacityConsumedInMB", [], hours, "Average"
            )

            usage_percent = (
                (capacity_consumed / capacity_limit * 100) if capacity_limit > 0 else 0
            )
            available = capacity_limit - capacity_consumed

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "time_range_hours": hours,
                        "capacity_limit_mb": round(capacity_limit, 2),
                        "capacity_consumed_mb": round(capacity_consumed, 2),
                        "capacity_available_mb": round(available, 2),
                        "usage_percent": round(usage_percent, 2),
                        "status": "warning" if usage_percent > 80 else "ok",
                    }
                ),
            }

        elif tool_name == "get_quicksight_api_calls":
            hours = parameters.get("hours", 24)
            event_name = parameters.get("event_name", None)
            user_name = parameters.get("user_name", None)
            max_results = parameters.get("max_results", 50)

            logger.info(
                f"CloudTrail query - hours: {hours}, event_name: {event_name}, user_name: {user_name}, max_results: {max_results}"
            )

            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)

            logger.info(f"Time range: {start_time} to {end_time}")

            try:
                # Query CloudTrail with Quick Sight EventSource filter
                logger.info(
                    "Calling CloudTrail LookupEvents with quicksight.amazonaws.com filter..."
                )
                response = cloudtrail_client.lookup_events(
                    LookupAttributes=[
                        {
                            "AttributeKey": "EventSource",
                            "AttributeValue": "quicksight.amazonaws.com",
                        }
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    MaxResults=50,  # CloudTrail max
                )

                all_events = response.get("Events", [])
                logger.info(f"CloudTrail returned {len(all_events)} Quick Sight events")

                # Log first 5 events to see what we're getting
                for i, evt in enumerate(all_events[:5]):
                    logger.info(
                        f"Sample event {i + 1}: EventName={evt.get('EventName')}, EventSource={evt.get('EventSource')}, Username={evt.get('Username')}"
                    )

                events = []
                # All events are already Quick Sight events due to LookupAttributes filter
                for event in all_events:
                    # Extract user info from CloudTrailEvent first
                    user_identity = None
                    source_ip = "N/A"
                    user_agent_str = "N/A"
                    error_code = None
                    error_message = None

                    if "CloudTrailEvent" in event:
                        try:
                            ct_event = json.loads(event["CloudTrailEvent"])
                            user_identity = ct_event.get("userIdentity", {})
                            source_ip = ct_event.get("sourceIPAddress", "N/A")
                            user_agent_str = ct_event.get("userAgent", "N/A")
                            error_code = ct_event.get("errorCode", None)
                            error_message = ct_event.get("errorMessage", None)
                        except Exception as e:
                            logger.error(f"Error parsing CloudTrail event: {e}")

                    # Determine user name from various sources
                    username = event.get("Username")
                    if not username and user_identity:
                        # Try Identity Center user
                        if user_identity.get("type") == "IdentityCenterUser":
                            on_behalf_of = user_identity.get("onBehalfOf", {})
                            username = on_behalf_of.get("userId", "N/A")
                        # Try IAM user
                        elif user_identity.get("type") in ["IAMUser", "AssumedRole"]:
                            username = user_identity.get(
                                "userName"
                            ) or user_identity.get("arn", "N/A")
                        else:
                            username = user_identity.get("principalId", "N/A")

                    event_data = {
                        "event_time": event["EventTime"].isoformat(),
                        "event_name": event["EventName"],
                        "user_name": username or "N/A",
                        "user_type": user_identity.get("type", "N/A")
                        if user_identity
                        else "N/A",
                        "event_source": event.get("EventSource", "N/A"),
                        "resource_type": event.get("Resources", [{}])[0].get(
                            "ResourceType", "N/A"
                        )
                        if event.get("Resources")
                        else "N/A",
                        "resource_name": event.get("Resources", [{}])[0].get(
                            "ResourceName", "N/A"
                        )
                        if event.get("Resources")
                        else "N/A",
                        "source_ip": source_ip,
                        "user_agent": user_agent_str,
                        "error_code": error_code,
                        "error_message": error_message,
                    }

                    # Apply user filters
                    if (
                        event_name
                        and event_name.lower() not in event_data["event_name"].lower()
                    ):
                        logger.info(
                            f"Filtered out event {event_data['event_name']} (doesn't match {event_name})"
                        )
                        continue
                    if (
                        user_name
                        and user_name.lower() not in event_data["user_name"].lower()
                    ):
                        logger.info(
                            f"Filtered out event by user {event_data['user_name']} (doesn't match {user_name})"
                        )
                        continue

                    events.append(event_data)

                    if len(events) >= max_results:
                        break

                logger.info(f"Total events after filtering: {len(events)}")

                # Group by event name
                event_counts = {}
                for event in events:
                    name = event["event_name"]
                    event_counts[name] = event_counts.get(name, 0) + 1

                logger.info(f"Event breakdown: {json.dumps(event_counts)}")

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "time_range_hours": hours,
                            "total_events": len(events),
                            "event_breakdown": event_counts,
                            "events": events,
                        }
                    ),
                }
            except Exception as e:
                logger.error(f"CloudTrail error: {str(e)}", exc_info=True)
                return {
                    "statusCode": 500,
                    "body": json.dumps(
                        {"error": f"Error querying CloudTrail: {str(e)}"}
                    ),
                }

        elif tool_name == "get_log_schema":
            logger.info("Getting schema for all Quick log groups")

            log_groups = {
                "chat": CHAT_LOG_GROUP,
                "feedback": FEEDBACK_LOG_GROUP,
                "agent_hours": AGENT_HOURS_LOG_GROUP,
            }

            schemas = {}
            query = "fields @message | limit 10"

            for log_type, log_group in log_groups.items():
                result = execute_logs_query(log_group, query, 24)

                if result["status"] == "success" and result["results"]:
                    all_fields = set()
                    for item in result["results"]:
                        message_field = next(
                            (f for f in item if f["field"] == "@message"), None
                        )
                        if message_field:
                            try:
                                message_data = json.loads(message_field["value"])
                                all_fields.update(message_data.keys())
                            except Exception:
                                pass

                    schemas[log_type] = {
                        "log_group": log_group,
                        "fields": sorted(all_fields),
                        "total_fields": len(all_fields),
                    }
                else:
                    schemas[log_type] = {
                        "log_group": log_group,
                        "fields": [],
                        "total_fields": 0,
                        "note": "No data available yet",
                    }

            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "schemas": schemas,
                        "note": "Use these field names in query_chat_analytics queries",
                    }
                ),
            }

        elif tool_name == "query_chat_analytics":
            log_type = parameters.get("log_type", "chat")
            query = parameters.get("query", "")
            hours = parameters.get("hours", 24)

            # Map log type to log group
            log_groups = {
                "chat": CHAT_LOG_GROUP,
                "feedback": FEEDBACK_LOG_GROUP,
                "agent_hours": AGENT_HOURS_LOG_GROUP,
            }

            log_group = log_groups.get(log_type, CHAT_LOG_GROUP)
            logger.info(f"Running custom query on {log_type} logs: {query}")

            result = execute_logs_query(log_group, query, hours)

            if result["status"] == "success":
                # Return raw results - structure depends on query
                results = [
                    {field["field"]: field["value"] for field in item}
                    for item in result["results"]
                ]

                return {
                    "statusCode": 200,
                    "body": json.dumps(
                        {
                            "log_type": log_type,
                            "time_range_hours": hours,
                            "query": query,
                            "total_results": len(results),
                            "results": results,
                        }
                    ),
                }
            else:
                return {
                    "statusCode": 500,
                    "body": json.dumps({"error": result.get("error", "Query failed")}),
                }

        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Unknown tool: {tool_name}"}),
            }

    except Exception as e:
        logger.error(f"Error in _handle_request: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
