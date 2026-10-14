"""Extract one ServiceNow table incrementally and write Parquet to Amazon S3."""

from pydantic import BaseModel, ConfigDict, Field
from pysnc import GlideRecord, ServiceNowClient

from common.clock import Clock
from common.daos.ingestion_checkpoint import (
    IngestionCheckpoint,
    IngestionCheckpointDao,
    RunStatus,
)
from common.daos.table_metadata import Destination, TableMetadata
from common.errors import ExtractionError
from common.observability import logger, tracer
from common.services.parquet_writer import ParquetWriter, WriteParquetRequest
from common.services.service_now_record import ServiceNowRecord
from common.services.servicenow_client_factory import ServiceNowClientFactory

_UPDATED_ON_FIELD = "sys_updated_on"


class ExtractTableRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: TableMetadata


class ExtractTableResponse(BaseModel):
    """Outcome of one structured table extraction."""

    model_config = ConfigDict(populate_by_name=True)

    table_name: str = Field(alias="TableName")
    records_extracted: int = Field(alias="RecordsExtracted")
    high_water_mark: str = Field(alias="HighWaterMark")
    destination: Destination = Field(alias="Destination")


class LandResult(BaseModel):
    records_extracted: int
    high_water_mark: str


class ExtractServiceNowTableActivity:
    """Extract a table from its high-water mark and stage it as Parquet."""

    def __init__(
        self,
        client_factory: ServiceNowClientFactory,
        checkpoint_dao: IngestionCheckpointDao,
        parquet_writer: ParquetWriter,
        clock: Clock,
    ) -> None:
        self._client_factory = client_factory
        self._checkpoint_dao = checkpoint_dao
        self._parquet_writer = parquet_writer
        self._clock = clock

    @tracer.capture_method
    def extract(self, request: ExtractTableRequest) -> ExtractTableResponse:
        """Extract records and advance the checkpoint after a successful write."""
        table = request.table
        mark = self._current_high_water_mark(table.table_name)
        try:
            record = self._query(self._client_factory.create(), table, mark)
            result = self._land(table, record, mark)
        except Exception as error:
            self._save_checkpoint(table.table_name, mark, RunStatus.FAILED, 0)
            raise ExtractionError(table.table_name, str(error)) from error

        self._save_checkpoint(
            table.table_name,
            result.high_water_mark,
            RunStatus.SUCCEEDED,
            result.records_extracted,
        )
        logger.info(
            "Extracted table",
            extra={
                "table": table.table_name,
                "records": result.records_extracted,
                "high_water_mark": result.high_water_mark,
            },
        )
        return ExtractTableResponse.model_validate(
            {
                "TableName": table.table_name,
                "RecordsExtracted": result.records_extracted,
                "HighWaterMark": result.high_water_mark,
                "Destination": table.destination,
            }
        )

    def _query(self, client: ServiceNowClient, table: TableMetadata, mark: str) -> GlideRecord:
        """Run the incremental query. PySNC handles pagination."""
        record = client.GlideRecord(table.table_name)
        if table.fields:
            record.fields = table.fields.split(",")
        encoded = self._encoded_query(table.base_query, mark)
        if encoded:
            record.add_encoded_query(encoded)
        record.order_by(_UPDATED_ON_FIELD)
        record.query()
        return record

    @staticmethod
    def _encoded_query(base_query: str, mark: str) -> str:
        incremental = f"{_UPDATED_ON_FIELD}>={mark}" if mark else ""
        return "^".join(clause for clause in (base_query, incremental) if clause)

    def _land(self, table: TableMetadata, record: GlideRecord, mark: str) -> LandResult:
        records = [
            ServiceNowRecord.from_serialized(row)
            for row in record.serialize_all(display_value=True)
        ]
        if not records:
            return LandResult(records_extracted=0, high_water_mark=mark)

        self._parquet_writer.write(
            WriteParquetRequest(
                table_name=table.table_name,
                ingested_utc_date=self._clock.today_partition(),
                records=records,
            )
        )
        return LandResult(
            records_extracted=len(records),
            high_water_mark=self._max_updated_on(records, mark),
        )

    def _max_updated_on(self, records: list[ServiceNowRecord], mark: str) -> str:
        present = [record.updated_on for record in records if record.updated_on]
        return max([mark, *present]) if present else mark

    def _current_high_water_mark(self, table_name: str) -> str:
        checkpoint = self._checkpoint_dao.get_checkpoint(table_name)
        return checkpoint.high_water_mark if checkpoint is not None else ""

    def _save_checkpoint(self, table_name: str, mark: str, status: RunStatus, count: int) -> None:
        self._checkpoint_dao.save_checkpoint(
            IngestionCheckpoint.model_validate(
                {
                    "TableName": table_name,
                    "HighWaterMark": mark,
                    "LastRunAt": self._clock.now().isoformat(),
                    "LastStatus": status,
                    "RecordsExtracted": count,
                }
            )
        )
