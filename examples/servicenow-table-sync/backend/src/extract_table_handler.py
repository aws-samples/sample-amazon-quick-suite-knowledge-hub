"""Lambda entry point for one structured ServiceNow table extraction."""

from typing import TYPE_CHECKING, Any

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from activities.extract_service_now_table_activity import (
    ExtractServiceNowTableActivity,
    ExtractTableRequest,
)
from common.clock import Clock
from common.config import config
from common.daos.ingestion_checkpoint import IngestionCheckpointDao
from common.daos.table_metadata import TableMetadata
from common.observability import logger, metrics, tracer
from common.services.parquet_writer import ParquetWriter
from common.services.servicenow_client_factory import ServiceNowClientFactory

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBServiceResource
    from mypy_boto3_secretsmanager import SecretsManagerClient

_dynamodb: "DynamoDBServiceResource" = boto3.resource("dynamodb", region_name=config.aws_region)
_secrets: "SecretsManagerClient" = boto3.client("secretsmanager", region_name=config.aws_region)

_activity = ExtractServiceNowTableActivity(
    client_factory=ServiceNowClientFactory(_secrets, config.service_now_secret_arn),
    checkpoint_dao=IngestionCheckpointDao(_dynamodb.Table(config.ingestion_checkpoint_table_name)),
    parquet_writer=ParquetWriter(config.raw_data_lake_bucket_name),
    clock=Clock(),
)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Extract one table and return its Step Functions result."""
    request = ExtractTableRequest(table=TableMetadata.model_validate(event))
    return _activity.extract(request).model_dump(by_alias=True)
