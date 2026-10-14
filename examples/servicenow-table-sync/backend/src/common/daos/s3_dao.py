"""S3 object access bound to one bucket and an injected client."""

from typing import TYPE_CHECKING

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

from common.errors import NotFoundError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_NOT_FOUND_CODES = ("NoSuchKey", "NoSuchBucket", "404")


class PutObjectRequest(BaseModel):
    """Object content and metadata to write."""

    key: str
    content: bytes
    metadata: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None


class GetObjectRequest(BaseModel):
    """Object key to read."""

    key: str


class GetObjectResponse(BaseModel):
    """Object content returned by S3."""

    content: bytes


class S3Dao:
    """Read and write objects in one bucket."""

    def __init__(self, client: "S3Client", bucket_name: str) -> None:
        self.client = client
        self.bucket_name = bucket_name

    def put(self, request: PutObjectRequest) -> None:
        extra: dict[str, object] = {}
        if request.metadata:
            extra["Metadata"] = request.metadata
        if request.content_type is not None:
            extra["ContentType"] = request.content_type
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=request.key,
            Body=request.content,
            **extra,  # type: ignore[arg-type]
        )

    def get(self, request: GetObjectRequest) -> GetObjectResponse:
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=request.key)
        except ClientError as error:
            raise self._as_not_found(error) from error
        return GetObjectResponse(content=response["Body"].read())

    def _as_not_found(self, error: ClientError) -> Exception:
        code = error.response.get("Error", {}).get("Code")
        if code in _NOT_FOUND_CODES:
            return NotFoundError("S3 object", f"{self.bucket_name}/unknown")
        return error
