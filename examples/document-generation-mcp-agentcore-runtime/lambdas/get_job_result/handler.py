"""
get_job_result Lambda — AgentCore Gateway target.

Checks job status in DynamoDB. If complete, generates a presigned S3 URL
for the document download. Returns status to the Quick agent.
"""

import json
import logging
import os

from dynamo_helpers import get_job
from s3_helpers import generate_presigned_url

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def lambda_handler(event, context):
    """
    Expected input:
    {
        "job_id": "uuid-string"
    }
    """
    logger.info(f"Received event: {json.dumps(event)}")

    body = (
        event
        if isinstance(event, dict) and "job_id" in event
        else json.loads(event.get("body", "{}"))
    )
    job_id = body.get("job_id", "").strip()

    if not job_id:
        return _response(400, {"error": "job_id is required"})

    job = get_job(job_id)
    if not job:
        return _response(404, {"error": f"Job {job_id} not found"})

    status = job["status"]

    if status == "COMPLETED":
        s3_key = job.get("s3_key", "")
        filename = job.get("filename", "document")

        # Use unsigned CloudFront URL — clean, short, not blocked by corporate proxies.
        # Security: paths contain UUIDs (unguessable), files auto-expire from S3 after 7 days.
        # Note: CloudFront signed URLs don't work with Quick because the chat
        # renderer strips ~ characters from signatures, corrupting them.
        cf_domain = os.environ.get("CLOUDFRONT_DOMAIN", "")
        if cf_domain and s3_key:
            download_url = f"https://{cf_domain}/{s3_key}"
        else:
            # Fallback to S3 presigned URL if CloudFront not configured
            expiry = int(os.environ.get("PRESIGNED_URL_EXPIRY", "3600"))
            download_url = generate_presigned_url(s3_key, expiry=expiry)

        return _response(
            200,
            {
                "job_id": job_id,
                "status": "COMPLETED",
                "filename": filename,
                "file_type": job.get("file_type", "unknown"),
                "download_url": download_url,
                "message": (
                    f'Document "{filename}" is ready.\n\n'
                    f"Download: {download_url}\n\n"
                    f"The link is valid for 7 days."
                ),
            },
        )

    elif status == "FAILED":
        return _response(
            200,
            {
                "job_id": job_id,
                "status": "FAILED",
                "error": job.get("error", "Unknown error during document generation"),
                "message": f"Document generation for job {job_id} failed. Please try again or adjust your request.",
            },
        )

    else:
        # SUBMITTED or PROCESSING — long-poll: wait up to 50s checking every 10s
        # before returning. This reduces the number of MCP calls the assistant
        # needs to make (Quick assistants have limited tool call budgets).
        import time as _time

        poll_end = _time.time() + 50
        while _time.time() < poll_end:
            _time.sleep(10)
            job = get_job(job_id)
            if not job:
                break
            status = job["status"]
            if status in ("COMPLETED", "FAILED"):
                # Re-enter the handler logic for terminal states
                return lambda_handler(event, context)

        elapsed_msg = ""
        started_at = job.get("started_at") if job else None
        created_at = job.get("created_at") if job else None
        if started_at:
            elapsed = int(_time.time()) - int(started_at)
            mins, secs = divmod(elapsed, 60)
            elapsed_msg = f" The agent has been working for {mins}m {secs}s."
        elif created_at:
            elapsed = int(_time.time()) - int(created_at)
            mins, secs = divmod(elapsed, 60)
            elapsed_msg = f" Job was submitted {mins}m {secs}s ago."

        return _response(
            200,
            {
                "job_id": job_id,
                "status": status,
                "message": (
                    f"Job {job_id} is still being generated (status: {status}).{elapsed_msg} "
                    f"IMPORTANT: You MUST call get_document_job_result again immediately "
                    f'with job_id "{job_id}". Do NOT stop polling until status is COMPLETED or FAILED. '
                    f"The document typically takes 3-5 minutes to generate."
                ),
            },
        )


def _response(code: int, body: dict) -> dict:
    return {
        "statusCode": code,
        "body": json.dumps(body),
    }
