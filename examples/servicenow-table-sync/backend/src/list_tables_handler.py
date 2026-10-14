"""Lambda entry point for the `ListIngestionTables` Step Functions task."""

from typing import TYPE_CHECKING, Any

import boto3
from aws_lambda_powertools.utilities.typing import LambdaContext

from activities.list_ingestion_tables_activity import (
    ListIngestionTablesActivity,
    ListIngestionTablesRequest,
)
from common.config import config
from common.daos.table_metadata import TableMetadataDao
from common.observability import logger, metrics, tracer

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBServiceResource

_dynamodb: "DynamoDBServiceResource" = boto3.resource("dynamodb", region_name=config.aws_region)
_activity = ListIngestionTablesActivity(
    table_metadata_dao=TableMetadataDao(_dynamodb.Table(config.table_metadata_table_name))
)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Return the enabled ingestion-table rows for the Map state."""
    response = _activity.list_tables(ListIngestionTablesRequest())
    return response.model_dump(by_alias=True)
