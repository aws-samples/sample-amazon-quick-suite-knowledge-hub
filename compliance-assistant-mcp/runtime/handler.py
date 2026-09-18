"""
Lambda function backing the AgentCore Gateway MCP target.

3 MCP tools exposed via AgentCore Gateway:
  - start_compliance_analysis: kicks off async CrewAI pipeline
  - get_analysis_status: polls job progress
  - get_analysis_report: retrieves completed report from S3

Flow:
  1. Quick → Gateway → this Lambda (sync, <60s)
  2. start_compliance_analysis → writes DynamoDB → invokes self async → returns job_id
  3. Async invocation → HTTP POST to AgentCore Runtime → Runtime runs CrewAI
  4. Runtime agent updates DynamoDB + S3 as it progresses
  5. get_analysis_status / get_analysis_report → reads DynamoDB/S3

Environment variables:
  - JOBS_TABLE: DynamoDB table name
  - REPORTS_BUCKET: S3 bucket for reports
  - DEPLOY_REGION: AWS region (default: us-east-1)
  - RUNTIME_ARN: AgentCore Runtime ARN
  - FUNCTION_NAME: this Lambda's own function name (for self-invocation)
"""

import json
import logging
import os
import time
import uuid

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("DEPLOY_REGION", "us-east-1")
dynamodb = boto3.resource("dynamodb", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)

TABLE_NAME = os.environ.get("JOBS_TABLE", "compliance-assistant-v2-jobs")
BUCKET_NAME = os.environ.get("REPORTS_BUCKET", "compliance-assistant-v2-reports")
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
FUNCTION_NAME = os.environ.get("FUNCTION_NAME", "")

table = dynamodb.Table(TABLE_NAME)


def _invoke_runtime_sync(payload: dict) -> dict:
    """Call AgentCore Runtime via boto3. Retries on cold-start timeout."""
    if not RUNTIME_ARN:
        raise RuntimeError("RUNTIME_ARN not configured")

    body = json.dumps(payload).encode("utf-8")
    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            logger.info("Runtime invocation attempt %d/%d", attempt + 1, max_retries)
            response = agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=RUNTIME_ARN,
                payload=body,
                contentType="application/json",
                accept="application/json",
            )
            result_bytes = response["response"].read()
            return json.loads(result_bytes.decode("utf-8"))
        except Exception as e:
            last_error = e
            error_str = str(e)
            logger.warning("Attempt %d failed: %s", attempt + 1, error_str)
            if (
                "initialization time exceeded" in error_str.lower()
                or "RuntimeClientError" in error_str
            ):
                if attempt < max_retries - 1:
                    wait_time = 15 * (attempt + 1)
                    logger.info("Cold start detected, waiting %ds...", wait_time)
                    time.sleep(wait_time)
                    continue
            raise
    raise last_error


def _fire_and_forget_runtime(job_id: str, topic: str):
    """Invoke this Lambda asynchronously to call Runtime without blocking."""
    payload = {
        "_async_runtime_call": True,
        "job_id": job_id,
        "topic": topic,
        "jobs_table": TABLE_NAME,
        "reports_bucket": BUCKET_NAME,
    }
    function_name = FUNCTION_NAME or os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )


def start_compliance_analysis(params: dict) -> dict:
    topic = params.get("topic")
    if not topic:
        return {"error": "Missing required parameter: topic"}

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    created_at = int(time.time())

    table.put_item(
        Item={
            "job_id": job_id,
            "status": "PENDING",
            "topic": topic,
            "progress": "Job created. Launching analysis pipeline...",
            "created_at": created_at,
            "ttl": created_at + 86400,
        }
    )

    try:
        _fire_and_forget_runtime(job_id, topic)
    except Exception as e:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, progress = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "FAILED",
                ":p": f"Failed to launch: {str(e)}",
            },
        )
        return {
            "job_id": job_id,
            "status": "FAILED",
            "error": f"Failed to launch: {str(e)}",
        }

    return {
        "job_id": job_id,
        "status": "PENDING",
        "message": "Compliance analysis started. Use get_analysis_status to check progress.",
    }


def get_analysis_status(params: dict) -> dict:
    job_id = params.get("job_id")
    if not job_id:
        return {"error": "Missing required parameter: job_id"}

    response = table.get_item(Key={"job_id": job_id})
    item = response.get("Item")
    if not item:
        return {"error": f"Job not found: {job_id}"}

    result = {
        "job_id": job_id,
        "status": item.get("status"),
        "progress": item.get("progress", ""),
        "topic": item.get("topic", ""),
    }
    if item.get("status") == "COMPLETED":
        result["message"] = (
            "Analysis complete. Use get_analysis_report to retrieve the full report."
        )
        if item.get("completed_at"):
            result["duration_seconds"] = item["completed_at"] - item["created_at"]
    if item.get("status") == "FAILED":
        result["error"] = item.get("error_message", "Unknown error")
    return result


def get_analysis_report(params: dict) -> dict:
    job_id = params.get("job_id")
    if not job_id:
        return {"error": "Missing required parameter: job_id"}

    response = table.get_item(Key={"job_id": job_id})
    item = response.get("Item")
    if not item:
        return {"error": f"Job not found: {job_id}"}

    status = item.get("status")
    if status in ("RUNNING", "PENDING"):
        return {
            "job_id": job_id,
            "status": status,
            "progress": item.get("progress", ""),
            "message": "Analysis still in progress. Please check back shortly.",
        }
    if status == "FAILED":
        return {
            "job_id": job_id,
            "status": "FAILED",
            "error": item.get("error_message", "Unknown error"),
        }

    s3_key = item.get("report_s3_key")
    if not s3_key:
        return {
            "job_id": job_id,
            "status": "COMPLETED",
            "error": "Report key missing from job record.",
        }

    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
        report_content = obj["Body"].read().decode("utf-8")
    except Exception as e:
        return {
            "job_id": job_id,
            "status": "COMPLETED",
            "error": f"Failed to retrieve report: {str(e)}",
        }

    return {
        "job_id": job_id,
        "status": "COMPLETED",
        "topic": item.get("topic", ""),
        "report": report_content,
    }


TOOL_DISPATCH = {
    "start_compliance_analysis": start_compliance_analysis,
    "get_analysis_status": get_analysis_status,
    "get_analysis_report": get_analysis_report,
}


def handler(event, context):
    logger.info("RAW EVENT: %s", json.dumps(event, default=str))

    # Async self-invocation path: call Runtime synchronously
    if event.get("_async_runtime_call"):
        job_id = event.get("job_id")
        logger.info("Async path: invoking AgentCore Runtime for job %s", job_id)
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, progress = :p",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "RUNNING",
                ":p": "Invoking AgentCore Runtime...",
            },
        )
        try:
            result = _invoke_runtime_sync(
                {
                    "job_id": job_id,
                    "topic": event["topic"],
                    "jobs_table": event.get("jobs_table", TABLE_NAME),
                    "reports_bucket": event.get("reports_bucket", BUCKET_NAME),
                }
            )
            logger.info("Runtime returned: %s", json.dumps(result, default=str))
            return result
        except Exception as e:
            logger.error("Runtime call failed: %s", str(e))
            table.update_item(
                Key={"job_id": event["job_id"]},
                UpdateExpression="SET #s = :s, progress = :p, error_message = :e",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "FAILED",
                    ":p": "Runtime invocation failed.",
                    ":e": str(e),
                },
            )
            return {"error": str(e)}

    # Normal MCP tool invocation path
    tool_name = None
    try:
        if context and hasattr(context, "client_context") and context.client_context:
            custom = getattr(context.client_context, "custom", None)
            if custom:
                tool_name = custom.get("bedrockAgentCoreToolName")
    except Exception:
        pass

    if not tool_name:
        tool_name = event.get("toolName") or event.get("tool_name")
    if not tool_name:
        if "topic" in event and "job_id" not in event:
            tool_name = "start_compliance_analysis"
        elif "job_id" in event:
            tool_name = "get_analysis_status"

    if not tool_name:
        return {
            "error": "Could not determine tool",
            "available_tools": list(TOOL_DISPATCH.keys()),
        }

    if "___" in tool_name:
        tool_name = tool_name.split("___")[-1]

    if tool_name not in TOOL_DISPATCH:
        return {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(TOOL_DISPATCH.keys()),
        }

    try:
        return TOOL_DISPATCH[tool_name](event)
    except Exception as e:
        return {"error": f"Tool execution failed: {str(e)}", "tool": tool_name}
