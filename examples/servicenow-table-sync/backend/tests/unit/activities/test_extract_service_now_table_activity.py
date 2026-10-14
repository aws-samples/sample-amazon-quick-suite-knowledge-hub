"""Tests for incremental structured ServiceNow extraction."""

from datetime import UTC, datetime

import pytest

from activities.extract_service_now_table_activity import (
    ExtractServiceNowTableActivity,
    ExtractTableRequest,
)
from common.daos.ingestion_checkpoint import IngestionCheckpoint, RunStatus
from common.daos.table_metadata import Destination, TableMetadata
from common.errors import ExtractionError


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC)

    def today_partition(self) -> str:
        return "2026-09-01"


class FakeGlideRecord:
    def __init__(self, rows: list[dict[str, object]], fail: bool = False) -> None:
        self._rows = rows
        self._fail = fail
        self.fields: list[str] = []
        self.encoded_query: str | None = None
        self.order_field: str | None = None

    def add_encoded_query(self, query: str) -> None:
        self.encoded_query = query

    def order_by(self, field: str) -> None:
        self.order_field = field

    def query(self) -> None:
        if self._fail:
            raise RuntimeError("ServiceNow unreachable")

    def serialize_all(self, display_value: bool = False) -> list[dict[str, object]]:
        return self._rows


class FakeClient:
    def __init__(self, record: FakeGlideRecord) -> None:
        self._record = record

    def GlideRecord(self, table_name: str) -> FakeGlideRecord:  # noqa: N802
        return self._record


class FakeClientFactory:
    def __init__(self, record: FakeGlideRecord) -> None:
        self._record = record

    def create(self) -> FakeClient:
        return FakeClient(self._record)


class FakeCheckpointDao:
    def __init__(self, existing: IngestionCheckpoint | None = None) -> None:
        self._existing = existing
        self.saved: IngestionCheckpoint | None = None

    def get_checkpoint(self, table_name: str) -> IngestionCheckpoint | None:
        return self._existing

    def save_checkpoint(self, checkpoint: IngestionCheckpoint) -> None:
        self.saved = checkpoint


class FakeParquetWriter:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def write(self, request: object) -> None:
        self.calls.append(request)


def _table(base_query: str = "") -> TableMetadata:
    return TableMetadata.model_validate(
        {
            "TableName": "incident",
            "Destination": Destination.STRUCTURED,
            "BaseQuery": base_query,
        }
    )


def _activity(
    record: FakeGlideRecord,
    checkpoint: FakeCheckpointDao,
    writer: FakeParquetWriter,
) -> ExtractServiceNowTableActivity:
    return ExtractServiceNowTableActivity(
        client_factory=FakeClientFactory(record),  # type: ignore[arg-type]
        checkpoint_dao=checkpoint,  # type: ignore[arg-type]
        parquet_writer=writer,  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
    )


def test_extract_writes_parquet_and_advances_checkpoint() -> None:
    rows = [{"sys_id": "i1", "sys_updated_on": "2026-09-01 09:00:00", "state": "New"}]
    checkpoint = FakeCheckpointDao()
    writer = FakeParquetWriter()

    response = _activity(FakeGlideRecord(rows), checkpoint, writer).extract(
        ExtractTableRequest(table=_table())
    )

    assert response.records_extracted == 1
    assert response.high_water_mark == "2026-09-01 09:00:00"
    assert len(writer.calls) == 1
    assert checkpoint.saved is not None
    assert checkpoint.saved.last_status is RunStatus.SUCCEEDED


def test_incremental_query_combines_base_query_and_checkpoint() -> None:
    prior = IngestionCheckpoint.model_validate(
        {
            "TableName": "incident",
            "HighWaterMark": "2026-08-31 00:00:00",
            "LastRunAt": "2026-08-31T00:00:00+00:00",
            "LastStatus": RunStatus.SUCCEEDED,
        }
    )
    record = FakeGlideRecord([])

    _activity(record, FakeCheckpointDao(prior), FakeParquetWriter()).extract(
        ExtractTableRequest(table=_table("active=true"))
    )

    assert record.encoded_query == "active=true^sys_updated_on>=2026-08-31 00:00:00"
    assert record.order_field == "sys_updated_on"


def test_empty_result_keeps_checkpoint() -> None:
    checkpoint = FakeCheckpointDao()

    response = _activity(FakeGlideRecord([]), checkpoint, FakeParquetWriter()).extract(
        ExtractTableRequest(table=_table())
    )

    assert response.records_extracted == 0
    assert checkpoint.saved is not None
    assert checkpoint.saved.last_status is RunStatus.SUCCEEDED


def test_failure_records_failed_checkpoint() -> None:
    checkpoint = FakeCheckpointDao()

    with pytest.raises(ExtractionError):
        _activity(FakeGlideRecord([], fail=True), checkpoint, FakeParquetWriter()).extract(
            ExtractTableRequest(table=_table())
        )

    assert checkpoint.saved is not None
    assert checkpoint.saved.last_status is RunStatus.FAILED
