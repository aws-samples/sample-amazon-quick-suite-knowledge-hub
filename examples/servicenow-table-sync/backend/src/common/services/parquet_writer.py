"""Writes a table's records to the raw data lake as partitioned Parquet.

Thin wrapper over the AWS SDK for pandas (`awswrangler`): it builds a string-typed DataFrame
from the extracted records, stamps the ingestion-date partition column, and appends a
partitioned Parquet dataset under the staging prefix (design §4.2:
`{table}/ingested_utc_date=YYYY-MM-DD/`). awswrangler owns the S3 write, partitioning, and
Snappy compression; this class owns the path convention and records-to-frame conversion (pandas
lives here, not in the activity).
"""

import awswrangler as wr
import pandas as pd
from pydantic import BaseModel

from common.services.service_now_record import ServiceNowRecord

_STAGING_PREFIX = "staging"
_PARTITION_COLUMN = "ingested_utc_date"


class WriteParquetRequest(BaseModel):
    """A table's extracted records to write for one ingestion date."""

    table_name: str
    ingested_utc_date: str
    records: list[ServiceNowRecord]


class WriteParquetResponse(BaseModel):
    """The S3 paths written for the batch."""

    paths: list[str]


class ParquetWriter:
    """Appends records to `s3://<bucket>/staging/{table}/ingested_utc_date=.../` as Parquet."""

    def __init__(self, bucket_name: str) -> None:
        self._bucket_name = bucket_name

    def write(self, request: WriteParquetRequest) -> WriteParquetResponse:
        """Write the records as date-partitioned Parquet; no-op on an empty batch."""
        if not request.records:
            return WriteParquetResponse(paths=[])
        frame = pd.DataFrame([record.fields for record in request.records], dtype="string")
        frame[_PARTITION_COLUMN] = request.ingested_utc_date
        result = wr.s3.to_parquet(
            df=frame,
            path=f"s3://{self._bucket_name}/{_STAGING_PREFIX}/{request.table_name}/",
            dataset=True,
            mode="append",
            partition_cols=[_PARTITION_COLUMN],
            compression="snappy",
        )
        return WriteParquetResponse(paths=list(result.get("paths", [])))
