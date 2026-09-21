"""
update_job Lambda — Called by Step Function to update job status in DynamoDB.
Handles both COMPLETED and FAILED transitions.
"""

import json
import logging
import os

from dynamo_helpers import update_job

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    """
    Expected input from Step Function:
    For COMPLETED: { "job_id": "...", "status": "COMPLETED", "s3_key": "...", "filename": "...", "file_type": "docx" }
    For FAILED:    { "job_id": "...", "status": "FAILED", "error": "..." }
    """
    logger.info(f"Received event: {json.dumps(event)}")

    job_id = event["job_id"]
    status = event["status"]

    kwargs = {}
    if status == "COMPLETED":
        kwargs["s3_key"] = event.get("s3_key")
        kwargs["filename"] = event.get("filename")
        kwargs["file_type"] = event.get("file_type")
    elif status == "FAILED":
        # Before marking FAILED, check if the agent already direct-persisted
        # the result. The agent runs asynchronously and may have completed
        # between the check_agent_status poll and this FAILED transition.
        from dynamo_helpers import get_job

        existing = get_job(job_id)
        if existing and existing.get("status") == "COMPLETED":
            logger.info(
                f"Job {job_id}: agent direct-persisted COMPLETED — skipping FAILED override"
            )
            return {"job_id": job_id, "status": "COMPLETED"}

        error = event.get("error", "")
        # Handle Step Function catch error format
        if isinstance(error, dict):
            error = error.get("Cause", str(error))
        kwargs["error"] = str(error)[:1000]  # truncate long errors

    update_job(job_id, status, **kwargs)
    logger.info(f"Job {job_id} updated to {status}")

    return {"job_id": job_id, "status": status}
