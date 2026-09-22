"""Tests for S3Dao."""

import boto3
from moto import mock_aws

from common.dao.s3_dao import S3Dao
from common.models import UploadContentRequest

BUCKET_NAME = "test-export-bucket"


class TestS3Dao:
    @mock_aws
    def test_upload_csv(self) -> None:
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET_NAME)
        dao = S3Dao(bucket_name=BUCKET_NAME)

        request = UploadContentRequest(
            key="exports/site-1/list-1/20260407T120000Z.csv",
            content="Title,Status\nTask A,Active\n",
            content_type="text/csv",
        )
        dao.upload(request)

        response = s3.get_object(Bucket=BUCKET_NAME, Key=request.key)
        body = response["Body"].read().decode("utf-8")
        assert body == request.content
        assert response["ContentType"] == "text/csv"

    @mock_aws
    def test_upload_manifest(self) -> None:
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET_NAME)
        dao = S3Dao(bucket_name=BUCKET_NAME)

        request = UploadContentRequest(
            key="exports/site-1/list-1/20260407T120000Z.manifest.json",
            content='{"fileLocations": []}',
            content_type="application/json",
        )
        dao.upload(request)

        response = s3.get_object(Bucket=BUCKET_NAME, Key=request.key)
        assert response["ContentType"] == "application/json"

    @mock_aws
    def test_bucket_name_property(self) -> None:
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET_NAME)
        dao = S3Dao(bucket_name=BUCKET_NAME)

        assert dao.bucket_name == BUCKET_NAME
