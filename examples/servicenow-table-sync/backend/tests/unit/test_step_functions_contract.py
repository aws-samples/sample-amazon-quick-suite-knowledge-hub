"""Step Functions I/O contract tests.

The workflow reads specific JsonPath fields from each task's output (`$.Tables`,
`$.TableName`, `$.RecordsExtracted`, `$.Destination`, `$.ExtractResults`). A task response
whose aliased wire shape does not carry those exact PascalCase keys silently breaks the state
machine (a Map finds no items, a Choice never matches). These tests pin the wire contract so a
field rename or a dropped alias fails here instead of at deploy.
"""

from activities.extract_service_now_deletes_activity import ExtractDeletesResponse
from activities.extract_service_now_table_activity import ExtractTableResponse
from activities.list_ingestion_tables_activity import ListIngestionTablesResponse
from common.daos.table_metadata import Destination


def test_list_tables_response_exposes_tables_key_for_the_map() -> None:
    response = ListIngestionTablesResponse.model_validate({"Tables": []})
    dumped = response.model_dump(by_alias=True)
    # The ExtractTables Map reads itemsPath "$.Tables".
    assert "Tables" in dumped
    assert "tables" not in dumped


def test_extract_table_response_exposes_choice_and_map_fields() -> None:
    response = ExtractTableResponse.model_validate(
        {
            "TableName": "incident",
            "RecordsExtracted": 3,
            "HighWaterMark": "2026-09-01 10:00:00",
            "Destination": Destination.STRUCTURED,
        }
    )
    dumped = response.model_dump(by_alias=True)
    # The NormalizeTables Choice reads "$.Destination" and "$.RecordsExtracted";
    # the NormalizeTable Glue task reads "$.TableName".
    assert set(dumped) == {"TableName", "RecordsExtracted", "HighWaterMark", "Destination"}
    assert dumped["Destination"] == "STRUCTURED"
    assert dumped["RecordsExtracted"] == 3


def test_extract_deletes_response_exposes_choice_and_map_fields() -> None:
    response = ExtractDeletesResponse.model_validate(
        {
            "TableName": "incident",
            "DeletesProcessed": 2,
            "HighWaterMark": "2026-09-01 10:00:00",
            "Destination": Destination.STRUCTURED,
        }
    )
    dumped = response.model_dump(by_alias=True)
    assert set(dumped) == {"TableName", "DeletesProcessed", "HighWaterMark", "Destination"}
    assert dumped["Destination"] == "STRUCTURED"
    assert dumped["DeletesProcessed"] == 2
