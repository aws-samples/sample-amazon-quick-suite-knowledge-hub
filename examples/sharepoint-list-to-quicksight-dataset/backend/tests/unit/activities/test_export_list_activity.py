"""Tests for ExportListActivity."""

import json
from unittest.mock import MagicMock

import pytest
from aws_lambda_powertools.event_handler import Response

from activities.activity_utils import ActivityErrorHandler
from activities.export_list_activity import ExportListActivity, ExportListResponse
from common.exceptions import GraphApiError, NotFoundError, UnauthorizedError
from common.models import (
    ExportListRequest,
    ResourceType,
    SharePointColumn,
    SharePointListItem,
    StatusCode,
)
from common.services.graph_api_client import GetColumnsResponse, GetListItemsResponse


@pytest.fixture
def graph_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def s3_dao() -> MagicMock:
    mock = MagicMock()
    mock.bucket_name = "test-bucket"
    return mock


@pytest.fixture
def activity(graph_client: MagicMock, s3_dao: MagicMock) -> ExportListActivity:
    return ExportListActivity(graph_client, s3_dao, ActivityErrorHandler())


class TestExportListActivity:
    def test_export_uploads_csv_and_manifest(
        self,
        activity: ExportListActivity,
        graph_client: MagicMock,
        s3_dao: MagicMock,
        sample_columns: list[SharePointColumn],
        sample_items: list[SharePointListItem],
    ) -> None:
        graph_client.get_columns.return_value = GetColumnsResponse(columns=sample_columns)
        graph_client.get_list_items.return_value = GetListItemsResponse(items=sample_items)
        request = ExportListRequest(site_id="site-1", list_id="list-1", access_token="token-123")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.OK.value
        response_data = ExportListResponse.model_validate_json(response.body)
        assert response_data.s3_bucket == "test-bucket"
        assert response_data.row_count == 3
        assert response_data.column_count == 3
        assert response_data.columns == ["Title", "Status", "Assigned To"]
        assert response_data.csv_s3_key == "exports/f505dccd1d9094db/b1fd2c6eb7a5ce6b/data.csv"
        assert response_data.manifest_s3_key == "exports/f505dccd1d9094db/b1fd2c6eb7a5ce6b/manifest.json"
        assert (
            response_data.manifest_s3_uri == "s3://test-bucket/exports/f505dccd1d9094db/b1fd2c6eb7a5ce6b/manifest.json"
        )

        assert s3_dao.upload.call_count == 2

        csv_upload = s3_dao.upload.call_args_list[0][0][0]
        assert csv_upload.content_type == "text/csv"
        assert "Title,Status,Assigned To" in csv_upload.content
        assert "Task A,Active,Alice" in csv_upload.content

        manifest_upload = s3_dao.upload.call_args_list[1][0][0]
        assert manifest_upload.content_type == "application/json"
        manifest = json.loads(manifest_upload.content)
        assert (
            manifest["fileLocations"][0]["URIs"][0]
            == "s3://test-bucket/exports/f505dccd1d9094db/b1fd2c6eb7a5ce6b/data.csv"
        )
        assert manifest["globalUploadSettings"]["format"] == "CSV"
        assert manifest["globalUploadSettings"]["containsHeader"] == "true"

    def test_export_not_found(self, activity: ExportListActivity, graph_client: MagicMock) -> None:
        graph_client.get_columns.side_effect = NotFoundError(ResourceType.LIST, "bad-list")
        request = ExportListRequest(site_id="site-1", list_id="bad-list", access_token="token-123")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.NOT_FOUND.value

    def test_export_empty_list(
        self,
        activity: ExportListActivity,
        graph_client: MagicMock,
        s3_dao: MagicMock,
        sample_columns: list[SharePointColumn],
    ) -> None:
        graph_client.get_columns.return_value = GetColumnsResponse(columns=sample_columns)
        graph_client.get_list_items.return_value = GetListItemsResponse(items=[])
        request = ExportListRequest(site_id="site-1", list_id="list-1", access_token="token-123")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.OK.value
        response_data = ExportListResponse.model_validate_json(response.body)
        assert response_data.row_count == 0

        csv_upload = s3_dao.upload.call_args_list[0][0][0]
        assert "Title,Status,Assigned To" in csv_upload.content

    def test_export_unauthorized(self, activity: ExportListActivity, graph_client: MagicMock) -> None:
        graph_client.get_columns.side_effect = UnauthorizedError()
        request = ExportListRequest(site_id="site-1", list_id="list-1", access_token="bad-token")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.UNAUTHORIZED.value

    def test_export_graph_api_error(self, activity: ExportListActivity, graph_client: MagicMock) -> None:
        graph_client.get_columns.side_effect = GraphApiError(500, "Internal")
        request = ExportListRequest(site_id="site-1", list_id="list-1", access_token="token-123")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value

    def test_export_unexpected_error(self, activity: ExportListActivity, graph_client: MagicMock) -> None:
        graph_client.get_columns.side_effect = RuntimeError("boom")
        request = ExportListRequest(site_id="site-1", list_id="list-1", access_token="token-123")

        response = activity.export(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value


class TestFlattenValue:
    def test_none(self) -> None:
        assert ExportListActivity._flatten_value(None) == ""

    def test_string(self) -> None:
        assert ExportListActivity._flatten_value("hello") == "hello"

    def test_number(self) -> None:
        assert ExportListActivity._flatten_value(42) == "42"

    def test_lookup_dict(self) -> None:
        assert ExportListActivity._flatten_value({"LookupId": 5, "LookupValue": "Alice"}) == "Alice"

    def test_email_dict(self) -> None:
        assert ExportListActivity._flatten_value({"Email": "bob@example.com", "Name": "Bob"}) == "bob@example.com"

    def test_unknown_dict(self) -> None:
        result = ExportListActivity._flatten_value({"foo": "bar"})
        assert "foo" in result
        assert "bar" in result

    def test_list_of_strings(self) -> None:
        assert ExportListActivity._flatten_value(["Red", "Blue"]) == "Red; Blue"

    def test_list_of_lookups(self) -> None:
        result = ExportListActivity._flatten_value([{"LookupValue": "Alice"}, {"LookupValue": "Bob"}])
        assert result == "Alice; Bob"
