"""Tests for listing enabled runtime registry rows."""

from activities.list_ingestion_tables_activity import (
    ListIngestionTablesActivity,
    ListIngestionTablesRequest,
)
from common.daos.table_metadata import Destination, TableMetadata


class FakeTableMetadataDao:
    def __init__(self, rows: list[TableMetadata]) -> None:
        self._rows = rows

    def list_enabled(self) -> list[TableMetadata]:
        return sorted((row for row in self._rows if row.enabled), key=lambda row: row.table_name)


def _table(name: str, enabled: bool = True) -> TableMetadata:
    return TableMetadata.model_validate(
        {"TableName": name, "Destination": Destination.STRUCTURED, "Enabled": enabled}
    )


def test_list_tables_returns_enabled_rows_sorted() -> None:
    dao = FakeTableMetadataDao(
        [_table("incident"), _table("change_request"), _table("problem", enabled=False)]
    )
    activity = ListIngestionTablesActivity(dao)  # type: ignore[arg-type]

    response = activity.list_tables(ListIngestionTablesRequest())

    assert [row.table_name for row in response.tables] == ["change_request", "incident"]
