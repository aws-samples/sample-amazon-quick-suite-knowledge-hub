"""Propagate audited ServiceNow deletions through a staging manifest.

The activity keeps a separate delete checkpoint for each table, queries
`sys_audit_delete`, and writes deleted `sys_id` values for the Glue merge job.
"""

from pydantic import BaseModel, ConfigDict, Field
from pysnc import GlideRecord, ServiceNowClient

from common.clock import Clock
from common.daos.ingestion_checkpoint import (
    IngestionCheckpoint,
    IngestionCheckpointDao,
    RunStatus,
)
from common.daos.s3_dao import PutObjectRequest, S3Dao
from common.daos.table_metadata import Destination, TableMetadata
from common.errors import ExtractionError
from common.observability import logger, tracer
from common.services.servicenow_client_factory import ServiceNowClientFactory

_AUDIT_DELETE_TABLE = "sys_audit_delete"
_STAGING_PREFIX = "staging"
_DELETES_PREFIX = "_deletes"
_DOCUMENT_KEY_FIELD = "documentkey"
_TABLE_NAME_FIELD = "tablename"
_CREATED_ON_FIELD = "sys_created_on"
_DELETES_CHECKPOINT_SUFFIX = "#deletes"


class ExtractDeletesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table: TableMetadata


class ExtractDeletesResponse(BaseModel):
    """Outcome of a deletes pass, aliased for the Step Functions Map/Choice."""

    model_config = ConfigDict(populate_by_name=True)

    table_name: str = Field(alias="TableName")
    deletes_processed: int = Field(alias="DeletesProcessed")
    high_water_mark: str = Field(alias="HighWaterMark")
    destination: Destination = Field(alias="Destination")


class DeletedRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    sys_id: str
    created_on: str


class ProcessResult(BaseModel):
    deletes_processed: int
    high_water_mark: str


class ExtractServiceNowDeletesActivity:
    """Reads `sys_audit_delete` incrementally and propagates each deletion to the raw lake."""

    def __init__(
        self,
        client_factory: ServiceNowClientFactory,
        checkpoint_dao: IngestionCheckpointDao,
        raw_lake_dao: S3Dao,
        clock: Clock,
    ) -> None:
        self._client_factory = client_factory
        self._checkpoint_dao = checkpoint_dao
        self._raw_lake_dao = raw_lake_dao
        self._clock = clock

    @tracer.capture_method
    def extract(self, request: ExtractDeletesRequest) -> ExtractDeletesResponse:
        """Query audited deletions from the deletes mark, propagate them, advance on success."""
        table = request.table
        mark = self._current_deletes_mark(table.table_name)
        try:
            record = self._query(self._client_factory.create(), table.table_name, mark)
            deletions = self._collect(record)
            result = self._propagate(table, deletions, mark)
        except Exception as error:
            self._save_checkpoint(table.table_name, mark, RunStatus.FAILED, 0)
            raise ExtractionError(table.table_name, str(error)) from error
        self._save_checkpoint(
            table.table_name,
            result.high_water_mark,
            RunStatus.SUCCEEDED,
            result.deletes_processed,
        )
        logger.info(
            "Propagated deletions",
            extra={
                "table": table.table_name,
                "deletes": result.deletes_processed,
                "high_water_mark": result.high_water_mark,
            },
        )
        return ExtractDeletesResponse.model_validate(
            {
                "TableName": table.table_name,
                "DeletesProcessed": result.deletes_processed,
                "HighWaterMark": result.high_water_mark,
                "Destination": table.destination,
            }
        )

    def _query(self, client: ServiceNowClient, table_name: str, mark: str) -> GlideRecord:
        """Build and run the incremental `sys_audit_delete` query. PySNC handles pagination."""
        record = client.GlideRecord(_AUDIT_DELETE_TABLE)
        record.add_encoded_query(self._encoded_query(table_name, mark))
        record.order_by(_CREATED_ON_FIELD)
        record.query()
        return record

    @staticmethod
    def _encoded_query(table_name: str, mark: str) -> str:
        """Filter to the source table and, after the first run, to deletions since the mark."""
        incremental = f"{_CREATED_ON_FIELD}>={mark}" if mark else ""
        clauses = (f"{_TABLE_NAME_FIELD}={table_name}", incremental)
        return "^".join(clause for clause in clauses if clause)

    @staticmethod
    def _collect(record: GlideRecord) -> list[DeletedRecord]:
        """Read each audit row's `documentkey` as a deleted `sys_id`, skipping empty keys."""
        deletions: list[DeletedRecord] = []
        for row in record.serialize_all():
            document_key = row.get(_DOCUMENT_KEY_FIELD)
            if not document_key:
                continue
            created_on = row.get(_CREATED_ON_FIELD)
            deletions.append(
                DeletedRecord(sys_id=str(document_key), created_on=str(created_on or ""))
            )
        return deletions

    def _propagate(
        self, table: TableMetadata, deletions: list[DeletedRecord], mark: str
    ) -> ProcessResult:
        """Route deletions by destination; an empty set is a no-op that keeps the mark."""
        if not deletions:
            return ProcessResult(deletes_processed=0, high_water_mark=mark)
        self._write_manifest(table, deletions)
        return ProcessResult(
            deletes_processed=len(deletions),
            high_water_mark=self._max_created_on(deletions, mark),
        )

    def _write_manifest(self, table: TableMetadata, deletions: list[DeletedRecord]) -> None:
        """Write the sorted, newline-delimited deleted `sys_id`s for the normalizer to apply."""
        sys_ids = sorted({deletion.sys_id for deletion in deletions})
        content = ("\n".join(sys_ids) + "\n").encode("utf-8")
        key = self._manifest_key(table.table_name, self._max_created_on(deletions, ""))
        self._raw_lake_dao.put(
            PutObjectRequest(
                key=key,
                content=content,
                metadata={"servicenow_table": table.table_name},
            )
        )

    @staticmethod
    def _manifest_key(table_name: str, window: str) -> str:
        """A deterministic manifest key scoped to the window's greatest `sys_created_on`."""
        return f"{_STAGING_PREFIX}/{table_name}/{_DELETES_PREFIX}/{_slugify(window)}.txt"

    @staticmethod
    def _max_created_on(deletions: list[DeletedRecord], mark: str) -> str:
        """The greatest `sys_created_on` across the batch (audit rows are ordered ascending)."""
        present = [deletion.created_on for deletion in deletions if deletion.created_on]
        return max([mark, *present]) if present else mark

    def _current_deletes_mark(self, table_name: str) -> str:
        checkpoint = self._checkpoint_dao.get_checkpoint(self._deletes_key(table_name))
        return checkpoint.high_water_mark if checkpoint is not None else ""

    def _save_checkpoint(self, table_name: str, mark: str, status: RunStatus, count: int) -> None:
        self._checkpoint_dao.save_checkpoint(
            IngestionCheckpoint.model_validate(
                {
                    "TableName": self._deletes_key(table_name),
                    "HighWaterMark": mark,
                    "LastRunAt": self._clock.now().isoformat(),
                    "LastStatus": status,
                    "RecordsExtracted": count,
                }
            )
        )

    @staticmethod
    def _deletes_key(table_name: str) -> str:
        """The distinct checkpoint row key for a table's deletes mark (never a real table name)."""
        return f"{table_name}{_DELETES_CHECKPOINT_SUFFIX}"


def _slugify(value: str) -> str:
    """Render a ServiceNow timestamp as a filesystem-safe, deterministic key segment."""
    slug = "".join(char if char.isalnum() else "-" for char in value)
    return slug or "deletes"
