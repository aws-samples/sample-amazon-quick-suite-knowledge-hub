"""
submit_job Lambda — AgentCore Gateway target.

Accepts a document creation request, creates a job record in DynamoDB,
invokes the AgentCore Runtime agent, and starts a Step Function polling
loop that waits for the agent to finish.

The agent uses async task management (add_async_task / complete_async_task)
so InvokeAgentRuntime returns immediately — the agent continues processing
in a background thread on AgentCore Runtime with no timeout constraint.
When it finishes, it direct-persists the result to S3 and marks the job
COMPLETED in DynamoDB. The Step Function polling loop detects this and exits.

Returns the job_id immediately (well under Quick's 60s MCP timeout).
"""

import json
import logging
import os
import uuid

import boto3
from dynamo_helpers import create_job

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

sfn_client = boto3.client("stepfunctions")
agentcore_client = boto3.client("bedrock-agentcore")

VALID_SKILL_TYPES = {"docx", "pdf", "pptx", "xlsx", "frontend-design"}


def lambda_handler(event, context):
    """
    Expected input (from Gateway MCP tool call):
    {
        "skill_type": "docx",
        "prompt": "Create a professional Q4 earnings report...",
        "filename": "Q4_Report.docx",  # optional
        "user_id": "user@example.com"   # optional
    }
    """
    logger.info(f"Received event: {json.dumps(event)}")

    body = (
        event
        if isinstance(event, dict) and "skill_type" in event
        else json.loads(event.get("body", "{}"))
    )

    skill_type = body.get("skill_type", "").lower().strip()
    prompt = body.get("prompt", "").strip()
    user_id = body.get("user_id", "anonymous")
    filename = body.get("filename", "").strip()

    if skill_type not in VALID_SKILL_TYPES:
        return _error(
            400,
            f"Invalid skill_type. Must be one of: {', '.join(sorted(VALID_SKILL_TYPES))}",
        )
    if not prompt:
        return _error(400, "prompt is required")
    if not filename:
        ext = "html" if skill_type == "frontend-design" else skill_type
        filename = f"document.{ext}"

    # 1. Create job record
    job_id = str(uuid.uuid4())
    create_job(job_id=job_id, skill_type=skill_type, prompt=prompt, user_id=user_id)

    # 2. Invoke the agent on AgentCore Runtime.
    #    The agent uses async task management — it returns immediately
    #    and continues processing in a background thread. No timeout hack needed.
    region = os.environ.get("AWS_REGION", "us-east-1")
    account_id = context.invoked_function_arn.split(":")[4]
    runtime_id = os.environ["AGENTCORE_RUNTIME_ID"]
    runtime_arn = (
        f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_id}"
    )

    payload = json.dumps(
        {
            "skill_type": skill_type,
            "prompt": prompt,
            "filename": filename,
            "job_id": job_id,
            "docs_bucket": os.environ["DOCS_BUCKET"],
            "jobs_table": os.environ["JOBS_TABLE"],
        }
    ).encode("utf-8")

    try:
        agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            payload=payload,
        )
        logger.info(f"Job {job_id}: agent invocation returned (async task started)")
    except Exception as e:
        logger.error(f"Job {job_id}: agent invocation failed: {e}", exc_info=True)
        return _error(500, f"Failed to invoke agent: {str(e)}")

    # 3. Start Step Function polling loop
    sfn_client.start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=f"job-{job_id}",
        input=json.dumps(
            {
                "job_id": job_id,
                "filename": filename,
                "file_type": "html" if skill_type == "frontend-design" else skill_type,
            }
        ),
    )

    logger.info(f"Job {job_id}: agent invoked, polling loop started")

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "job_id": job_id,
                "status": "SUBMITTED",
                "message": (
                    f"Document creation job submitted successfully. "
                    f"The job ID is {job_id}. "
                    f"IMPORTANT: Tell the user their job ID is {job_id} so they can check status later. "
                    f"Then automatically call get_document_job_result with this job_id to poll for completion."
                ),
            }
        ),
    }


def _error(code: int, message: str) -> dict:
    return {
        "statusCode": code,
        "body": json.dumps({"error": message}),
    }
