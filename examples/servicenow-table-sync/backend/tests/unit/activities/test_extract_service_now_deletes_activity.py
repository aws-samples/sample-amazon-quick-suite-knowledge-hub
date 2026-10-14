"""Tests for structured ServiceNow delete propagation."""

from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from activities.extract_service_now_deletes_activity import (
    ExtractDeletesRequest,
    ExtractServiceNowDeletesActivity,
)
from common.daos.ingestion_checkpoint import IngestionCheckpoint, RunStatus
from common.daos.s3_dao import GetObjectRequest, PutObjectRequest, S3Dao
from common.daos.table_metadata import Destination, TableMetadata
from common.errors import ExtractionError

_REGION = "us-east-1"
_BUCKET = "test-raw-data-lake"


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, tzinfo=UTC)


class FakeGlideRecord:
    def __init__(self, rows: list[dict[str, object]], fail: bool = False) -> None:
        self._rows = rows
        self._fail = fail
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
        self.requested_key: str | None = None

    def get_checkpoint(self, table_name: str) -> IngestionCheckpoint | None:
        self.requested_key = table_name
        return self._existing

    def save_checkpoint(self, checkpoint: IngestionCheckpoint) -> None:
        self.saved = checkpoint


class FakeS3Dao:
    def __init__(self) -> None:
        self.puts: list[PutObjectRequest] = []

    def put(self, request: PutObjectRequest) -> None:
        self.puts.append(request)


def _activity(
    record: FakeGlideRecord,
    checkpoint: FakeCheckpointDao,
    s3: FakeS3Dao | S3Dao,
) -> ExtractServiceNowDeletesActivity:
    return ExtractServiceNowDeletesActivity(
        client_factory=FakeClientFactory(record),  # type: ignore[arg-type]
        checkpoint_dao=checkpoint,  # type: ignore[arg-type]
        raw_lake_dao=s3,  # type: ignore[arg-type]
        clock=FakeClock(),  # type: ignore[arg-type]
    )


def _table() -> TableMetadata:
    return TableMetadata.model_validate(
        {"TableName": "incident", "Destination": Destination.STRUCTURED}
    )


def _audit_row(document_key: str, created_on: str) -> dict[str, object]:
    return {"tablename": "incident", "documentkey": document_key, "sys_created_on": created_on}


def _prior_mark(mark: str) -> IngestionCheckpoint:
    return IngestionCheckpoint.model_validate(
        {
            "TableName": "incident#deletes",
            "HighWaterMark": mark,
            "LastRunAt": "2026-08-31T00:00:00+00:00",
            "LastStatus": RunStatus.SUCCEEDED,
        }
    )


def test_query_uses_separate_delete_checkpoint() -> None:
    checkpoint = FakeCheckpointDao(_prior_mark("2026-08-31 00:00:00"))
    record = FakeGlideRecord([])

    _activity(record, checkpoint, FakeS3Dao()).extract(ExtractDeletesRequest(table=_table()))

    assert checkpoint.requested_key == "incident#deletes"
    assert record.encoded_query == "tablename=incident^sys_created_on>=2026-08-31 00:00:00"
    assert record.order_field == "sys_created_on"


def test_empty_result_writes_nothing_and_keeps_mark() -> None:
    checkpoint = FakeCheckpointDao(_prior_mark("2026-08-31 00:00:00"))
    s3 = FakeS3Dao()

    response = _activity(FakeGlideRecord([]), checkpoint, s3).extract(
        ExtractDeletesRequest(table=_table())
    )

    assert response.deletes_processed == 0
    assert response.high_water_mark == "2026-08-31 00:00:00"
    assert s3.puts == []


def test_empty_document_keys_are_skipped() -> None:
    rows = [_audit_row("", "2026-09-01 09:00:00"), _audit_row("inc-1", "2026-09-01 10:00:00")]
    s3 = FakeS3Dao()

    response = _activity(FakeGlideRecord(rows), FakeCheckpointDao(), s3).extract(
        ExtractDeletesRequest(table=_table())
    )

    assert response.deletes_processed == 1
    assert s3.puts[0].content == b"inc-1\n"


def test_failure_records_failed_checkpoint() -> None:
    checkpoint = FakeCheckpointDao(_prior_mark("2026-08-31 00:00:00"))

    with pytest.raises(ExtractionError):
        _activity(FakeGlideRecord([], fail=True), checkpoint, FakeS3Dao()).extract(
            ExtractDeletesRequest(table=_table())
        )

    assert checkpoint.saved is not None
    assert checkpoint.saved.last_status is RunStatus.FAILED
    assert checkpoint.saved.high_water_mark == "2026-08-31 00:00:00"


@mock_aws
def test_deletes_write_sorted_idempotent_manifest() -> None:
    s3_client = boto3.client("s3", region_name=_REGION)
    s3_client.create_bucket(Bucket=_BUCKET)
    dao = S3Dao(s3_client, _BUCKET)
    rows = [_audit_row("inc-b", "2026-09-01 09:00:00"), _audit_row("inc-a", "2026-09-01 10:00:00")]
    activity = _activity(FakeGlideRecord(rows), FakeCheckpointDao(), dao)

    activity.extract(ExtractDeletesRequest(table=_table()))
    activity.extract(ExtractDeletesRequest(table=_table()))

    listed = s3_client.list_objects_v2(Bucket=_BUCKET, Prefix="staging/incident/_deletes/")
    keys = [obj["Key"] for obj in listed.get("Contents", [])]
    assert keys == ["staging/incident/_deletes/2026-09-01-10-00-00.txt"]
    assert dao.get(GetObjectRequest(key=keys[0])).content == b"inc-a\ninc-b\n"
