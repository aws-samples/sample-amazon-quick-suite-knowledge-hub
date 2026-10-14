"""The per-table high-water-mark checkpoint record and its DynamoDB DAO.

Partition key `TableName`, one row per extracted ServiceNow table. Holds the `sys_updated_on`
of the last successfully-extracted record (the incremental anchor, design §"Incremental
changes"), the last run timestamp, and the last run status. The extractor writes the advanced
mark only after a successful pass, so a failed pass re-extracts from the same point.
"""

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from common.daos.dynamodb import DynamoDbDao, DynamoDbKey

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table

_PARTITION_KEY = "TableName"


class RunStatus(StrEnum):
    """Terminal status of the most recent extraction pass for a table."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class IngestionCheckpoint(BaseModel):
    """A table's incremental high-water mark and last-run outcome."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    table_name: str = Field(alias="TableName")
    high_water_mark: str = Field(alias="HighWaterMark")
    last_run_at: str = Field(alias="LastRunAt")
    last_status: RunStatus = Field(alias="LastStatus")
    records_extracted: int = Field(default=0, alias="RecordsExtracted")


class IngestionCheckpointDao(DynamoDbDao[IngestionCheckpoint]):
    """High-water-mark checkpoint per ServiceNow table, keyed on `TableName`."""

    def __init__(self, table: "Table") -> None:
        super().__init__(table, IngestionCheckpoint)

    def get_checkpoint(self, table_name: str) -> IngestionCheckpoint | None:
        """Return a table's checkpoint, or None on the first-ever run for it."""
        return self.get_or_none(DynamoDbKey(key={_PARTITION_KEY: table_name}))

    def save_checkpoint(self, checkpoint: IngestionCheckpoint) -> None:
        """Persist the advanced high-water mark after a successful extraction pass."""
        self.put(checkpoint)
