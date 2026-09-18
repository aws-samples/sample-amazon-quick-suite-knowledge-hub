"""Tests for SearchSitesActivity."""

from unittest.mock import MagicMock

import pytest
from aws_lambda_powertools.event_handler import Response

from activities.activity_utils import ActivityErrorHandler
from activities.search_sites_activity import SearchSitesActivity
from common.exceptions import GraphApiError, UnauthorizedError
from common.models import SearchSitesRequest, SharePointSite, StatusCode
from common.services.graph_api_client import SearchSitesResponse


@pytest.fixture
def graph_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def activity(graph_client: MagicMock) -> SearchSitesActivity:
    return SearchSitesActivity(graph_client, ActivityErrorHandler())


class TestSearchSitesActivity:
    def test_search_success(
        self, activity: SearchSitesActivity, graph_client: MagicMock, sample_sites: list[SharePointSite]
    ) -> None:
        graph_client.search_sites.return_value = SearchSitesResponse(sites=sample_sites)
        request = SearchSitesRequest(query="team", access_token="token-123")

        response = activity.search(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.OK.value
        response_data = SearchSitesResponse.model_validate_json(response.body)
        assert len(response_data.sites) == 2
        assert response_data.sites[0].id == "site-1"

    def test_search_graph_api_error(self, activity: SearchSitesActivity, graph_client: MagicMock) -> None:
        graph_client.search_sites.side_effect = GraphApiError(403, "Forbidden")
        request = SearchSitesRequest(query="team", access_token="token-123")

        response = activity.search(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value

    def test_search_unexpected_error(self, activity: SearchSitesActivity, graph_client: MagicMock) -> None:
        graph_client.search_sites.side_effect = RuntimeError("boom")
        request = SearchSitesRequest(query="team", access_token="token-123")

        response = activity.search(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value

    def test_search_unauthorized(self, activity: SearchSitesActivity, graph_client: MagicMock) -> None:
        graph_client.search_sites.side_effect = UnauthorizedError()
        request = SearchSitesRequest(query="team", access_token="bad-token")

        response = activity.search(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.UNAUTHORIZED.value
