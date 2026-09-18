"""S3 data access for storing exported files."""

import boto3

from common.models import UploadContentRequest
from common.observability import logger, tracer


class S3Dao:
    """Stores exported SharePoint list files in S3."""

    def __init__(self, bucket_name: str) -> None:
        self._client = boto3.client("s3")
        self._bucket_name = bucket_name

    @property
    def bucket_name(self) -> str:
        return self._bucket_name

    @tracer.capture_method
    def upload(self, request: UploadContentRequest) -> None:
        """Upload content to S3."""
        logger.info("Uploading to S3", extra={"bucket": self._bucket_name, "key": request.key})
        self._client.put_object(
            Bucket=self._bucket_name,
            Key=request.key,
            Body=request.content.encode("utf-8"),
            ContentType=request.content_type,
        )
