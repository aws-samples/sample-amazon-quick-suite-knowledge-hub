"""S3 helpers for document storage and presigned URL generation."""

import os

import boto3

_s3_client = None

CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html",
}


def _get_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def upload_file(file_bytes: bytes, s3_key: str, filename: str, file_type: str) -> str:
    """Upload file bytes to S3 with proper content type and disposition."""
    bucket = os.environ["DOCS_BUCKET"]
    content_type = CONTENT_TYPES.get(file_type, "application/octet-stream")

    _get_client().put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=file_bytes,
        ContentType=content_type,
        ContentDisposition=f'attachment; filename="{filename}"',
        ServerSideEncryption="AES256",
    )
    return s3_key


def generate_presigned_url(s3_key: str, expiry: int = 3600) -> str:
    """Generate a presigned download URL."""
    bucket = os.environ["DOCS_BUCKET"]
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=expiry,
    )
