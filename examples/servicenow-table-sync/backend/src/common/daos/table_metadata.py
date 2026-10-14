"""Runtime table registry model and DynamoDB access."""

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from common.daos.dynamodb import DynamoDbDao

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


class Destination(StrEnum):
    """Supported extraction destination."""

    STRUCTURED = "STRUCTURED"


class TableMetadata(BaseModel):
    """Extraction settings for one structured ServiceNow table."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    table_name: str = Field(alias="TableName")
    destination: Destination = Field(alias="Destination")
    enabled: bool = Field(default=True, alias="Enabled")
    fields: str = Field(default="", alias="Fields")
    base_query: str = Field(default="", alias="BaseQuery")


class TableMetadataDao(DynamoDbDao[TableMetadata]):
    """Read the deployment-owned table registry at runtime."""

    def __init__(self, table: "Table") -> None:
        super().__init__(table, TableMetadata)

    def list_enabled(self) -> list[TableMetadata]:
        """Return enabled tables in deterministic order."""
        rows = [row for row in self.scan_all() if row.enabled]
        return sorted(rows, key=lambda row: row.table_name)
