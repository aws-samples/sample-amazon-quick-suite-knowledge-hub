"""
check_agent_status Lambda — Called by Step Function polling loop.

The agent runs asynchronously on AgentCore Runtime and direct-persists
results to S3 + DynamoDB when done. This Lambda polls DynamoDB to check
if the agent has completed the job.

Returns the current job status so the Step Function Choice state can decide
whether to wait and retry, or proceed.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_table = None


def _get_table():
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(os.environ["JOBS_TABLE"])
    return _table


def lambda_handler(event, context):
    """
    Input from Step Function:
    {
        "job_id": "uuid",
        "filename": "...",
        "file_type": "docx",
        "agent_pending": true
    }

    Returns:
    - If agent finished: { "job_id": "...", "s3_key": "...", "filename": "...", "file_type": "..." }
    - If still pending:  { "job_id": "...", "agent_pending": true, "filename": "...", "file_type": "..." }
    """
    job_id = event["job_id"]
    logger.info(f"Checking agent status for job {job_id}")

    job = _get_table().get_item(Key={"job_id": job_id}).get("Item")

    if not job:
        logger.error(f"Job {job_id} not found in DynamoDB")
        raise RuntimeError(f"Job {job_id} not found")

    status = job.get("status", "")

    if status == "COMPLETED":
        logger.info(f"Job {job_id}: agent direct-persist completed")
        return {
            "job_id": job_id,
            "s3_key": job.get(
                "s3_key", f"generated/{job_id}/{event.get('filename', 'output')}"
            ),
            "filename": job.get("filename", event.get("filename", "output")),
            "file_type": job.get("file_type", event.get("file_type", "docx")),
        }
    elif status == "FAILED":
        raise RuntimeError(
            f"Agent failed for job {job_id}: {job.get('error', 'unknown')}"
        )
    else:
        # Still PROCESSING — tell Step Function to wait and retry
        logger.info(f"Job {job_id}: still {status}, agent pending")
        return {
            "job_id": job_id,
            "agent_pending": True,
            "filename": event.get("filename", ""),
            "file_type": event.get("file_type", "docx"),
        }
