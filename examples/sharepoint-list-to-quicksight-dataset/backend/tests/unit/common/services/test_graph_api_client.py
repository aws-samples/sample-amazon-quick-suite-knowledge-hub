"""Tests for GraphApiClient."""

from unittest.mock import MagicMock, patch

import pytest

from common.exceptions import GraphApiError, NotFoundError
from common.models import (
    GetColumnsRequest,
    GetListItemsRequest,
    ListListsRequest,
    SearchSitesRequest,
)
from common.services.graph_api_client import GraphApiClient


@pytest.fixture
def graph_client() -> GraphApiClient:
    return GraphApiClient(base_url="https://graph.microsoft.com/v1.0", timeout_seconds=10, max_items_per_page=100)


class TestSearchSites:
    @patch("common.services.graph_api_client.httpx.get")
    def test_returns_sites(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [{"id": "site-1", "name": "Team", "displayName": "Team Site", "webUrl": "https://example.com"}]
        }
        mock_get.return_value = mock_response

        request = SearchSitesRequest(query="team", access_token="token-123")
        response = graph_client.search_sites(request)

        assert len(response.sites) == 1
        assert response.sites[0].id == "site-1"
        assert response.sites[0].display_name == "Team Site"

    @patch("common.services.graph_api_client.httpx.get")
    def test_returns_empty_on_no_results(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": []}
        mock_get.return_value = mock_response

        request = SearchSitesRequest(query="nonexistent", access_token="token-123")
        response = graph_client.search_sites(request)

        assert len(response.sites) == 0

    @patch("common.services.graph_api_client.httpx.get")
    def test_follows_pagination(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "value": [{"id": "site-1", "name": "A", "displayName": "A"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites?search=x&$skiptoken=abc",
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {"value": [{"id": "site-2", "name": "B", "displayName": "B"}]}
        mock_get.side_effect = [page1, page2]

        response = graph_client.search_sites(SearchSitesRequest(query="x", access_token="token-123"))

        assert len(response.sites) == 2
        assert mock_get.call_count == 2


class TestGetLists:
    @patch("common.services.graph_api_client.httpx.get")
    def test_returns_lists(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [{"id": "list-1", "name": "Tasks", "displayName": "Tasks", "list": {"template": "genericList"}}]
        }
        mock_get.return_value = mock_response

        request = ListListsRequest(site_id="site-1", access_token="token-123")
        response = graph_client.get_lists(request)

        assert len(response.lists) == 1
        assert response.lists[0].name == "Tasks"

    @patch("common.services.graph_api_client.httpx.get")
    def test_raises_not_found(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        request = ListListsRequest(site_id="bad-site", access_token="token-123")

        with pytest.raises(NotFoundError):
            graph_client.get_lists(request)

    @patch("common.services.graph_api_client.httpx.get")
    def test_follows_pagination(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "value": [{"id": "list-1", "name": "Tasks", "displayName": "Tasks", "list": {}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites/s/lists?$skiptoken=abc",
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {"value": [{"id": "list-2", "name": "Docs", "displayName": "Docs", "list": {}}]}
        mock_get.side_effect = [page1, page2]

        response = graph_client.get_lists(ListListsRequest(site_id="s", access_token="token-123"))

        assert len(response.lists) == 2
        assert mock_get.call_count == 2


class TestGetColumns:
    @patch("common.services.graph_api_client.httpx.get")
    def test_filters_readonly_columns(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"name": "Title", "displayName": "Title", "readOnly": False},
                {"name": "Created", "displayName": "Created", "readOnly": True},
                {"name": "Status", "displayName": "Status", "readOnly": False},
            ]
        }
        mock_get.return_value = mock_response

        request = GetColumnsRequest(site_id="site-1", list_id="list-1", access_token="token-123")
        response = graph_client.get_columns(request)

        assert len(response.columns) == 2
        assert response.columns[0].name == "Title"
        assert response.columns[1].name == "Status"


class TestGetListItems:
    @patch("common.services.graph_api_client.httpx.get")
    def test_single_page(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {"id": "1", "fields": {"Title": "Item A"}},
                {"id": "2", "fields": {"Title": "Item B"}},
            ]
        }
        mock_get.return_value = mock_response

        request = GetListItemsRequest(site_id="site-1", list_id="list-1", access_token="token-123")
        response = graph_client.get_list_items(request)

        assert len(response.items) == 2
        assert response.items[0].fields["Title"] == "Item A"

    @patch("common.services.graph_api_client.httpx.get")
    def test_follows_pagination(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "value": [{"id": "1", "fields": {"Title": "A"}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites/s/lists/l/items?$skiptoken=abc",
        }

        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {"value": [{"id": "2", "fields": {"Title": "B"}}]}

        mock_get.side_effect = [page1, page2]

        request = GetListItemsRequest(site_id="s", list_id="l", access_token="token-123")
        response = graph_client.get_list_items(request)

        assert len(response.items) == 2
        assert mock_get.call_count == 2
        second_call_url = mock_get.call_args_list[1][0][0]
        assert "skiptoken" in second_call_url

    @patch("common.services.graph_api_client.httpx.get")
    def test_raises_graph_api_error(self, mock_get: MagicMock, graph_client: GraphApiClient) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": {"message": "Access denied"}}
        mock_response.text = "Access denied"
        mock_get.return_value = mock_response

        request = GetListItemsRequest(site_id="site-1", list_id="list-1", access_token="token-123")

        with pytest.raises(GraphApiError) as exc_info:
            graph_client.get_list_items(request)

        assert exc_info.value.status_code == 403
