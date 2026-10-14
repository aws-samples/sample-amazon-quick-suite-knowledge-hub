"""Lists the enabled ServiceNow tables the extraction Map state fans out over."""

from pydantic import BaseModel, ConfigDict, Field

from common.daos.table_metadata import TableMetadata, TableMetadataDao
from common.observability import logger, tracer


class ListIngestionTablesRequest(BaseModel):
    """The list request. No parameters: the registry is the single source of truth."""


class ListIngestionTablesResponse(BaseModel):
    """The enabled table rows the Map state iterates over."""

    model_config = ConfigDict(populate_by_name=True)

    tables: list[TableMetadata] = Field(alias="Tables")


class ListIngestionTablesActivity:
    """Returns the enabled extraction-registry rows for the Step Functions Map state."""

    def __init__(self, table_metadata_dao: TableMetadataDao) -> None:
        self._table_metadata_dao = table_metadata_dao

    @tracer.capture_method
    def list_tables(self, request: ListIngestionTablesRequest) -> ListIngestionTablesResponse:
        """Return every enabled table row, in a deterministic order."""
        tables = self._table_metadata_dao.list_enabled()
        logger.info("Listed ingestion tables", extra={"count": len(tables)})
        return ListIngestionTablesResponse.model_validate({"Tables": tables})
