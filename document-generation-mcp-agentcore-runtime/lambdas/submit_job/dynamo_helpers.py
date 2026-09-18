"""DynamoDB helpers for job tracking."""

import os
import time

import boto3

_table = None


def _get_table():
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(os.environ["JOBS_TABLE"])
    return _table


def create_job(
    job_id: str, skill_type: str, prompt: str, user_id: str = "anonymous"
) -> dict:
    """Create a new job record with SUBMITTED status."""
    item = {
        "job_id": job_id,
        "status": "SUBMITTED",
        "skill_type": skill_type,
        "prompt": prompt,
        "user_id": user_id,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "ttl": int(time.time()) + 7 * 86400,  # 7 days
    }
    _get_table().put_item(Item=item)
    return item


def update_job(job_id: str, status: str, **kwargs) -> dict:
    """Update job status and optional fields (s3_key, filename, error, etc.)."""
    expr_parts = ["#s = :status", "updated_at = :now"]
    attr_names = {"#s": "status"}
    attr_values = {":status": status, ":now": int(time.time())}

    # DynamoDB reserved keywords need ExpressionAttributeNames
    reserved_words = {"error", "status", "name", "value", "type", "data", "count"}

    for key, value in kwargs.items():
        if value is not None:
            if key in reserved_words:
                attr_names[f"#{key}"] = key
                expr_parts.append(f"#{key} = :{key}")
            else:
                expr_parts.append(f"{key} = :{key}")
            attr_values[f":{key}"] = value

    _get_table().update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )
    return {"job_id": job_id, "status": status}


def get_job(job_id: str) -> dict | None:
    """Retrieve a job record."""
    resp = _get_table().get_item(Key={"job_id": job_id})
    return resp.get("Item")
