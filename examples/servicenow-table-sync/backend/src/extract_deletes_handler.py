"""Lambda entry point for the `ExtractServiceNowDeletes` Step Functions task."""

from typing import TYPE_CHECKING, Any

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from activities.extract_service_now_deletes_activity import (
    ExtractDeletesRequest,
    ExtractServiceNowDeletesActivity,
)
from common.clock import Clock
from common.config import config
from common.daos.ingestion_checkpoint import IngestionCheckpointDao
from common.daos.s3_dao import S3Dao
from common.daos.table_metadata import TableMetadata
from common.observability import logger, metrics, tracer
from common.services.servicenow_client_factory import ServiceNowClientFactory

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBServiceResource
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_secretsmanager import SecretsManagerClient

_dynamodb: "DynamoDBServiceResource" = boto3.resource("dynamodb", region_name=config.aws_region)
_s3: "S3Client" = boto3.client("s3", region_name=config.aws_region)
_secrets: "SecretsManagerClient" = boto3.client("secretsmanager", region_name=config.aws_region)

_activity = ExtractServiceNowDeletesActivity(
    client_factory=ServiceNowClientFactory(_secrets, config.service_now_secret_arn),
    checkpoint_dao=IngestionCheckpointDao(_dynamodb.Table(config.ingestion_checkpoint_table_name)),
    raw_lake_dao=S3Dao(_s3, config.raw_data_lake_bucket_name),
    clock=Clock(),
)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Propagate one ServiceNow table's audited deletions and return the outcome."""
    request = ExtractDeletesRequest(table=TableMetadata.model_validate(event))
    return _activity.extract(request).model_dump(by_alias=True)
