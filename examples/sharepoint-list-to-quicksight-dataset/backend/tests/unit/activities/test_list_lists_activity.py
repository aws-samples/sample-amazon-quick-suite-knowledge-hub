"""Tests for ListListsActivity."""

from unittest.mock import MagicMock

import pytest
from aws_lambda_powertools.event_handler import Response

from activities.activity_utils import ActivityErrorHandler
from activities.list_lists_activity import ListListsActivity
from common.exceptions import GraphApiError, NotFoundError, UnauthorizedError
from common.models import ListListsRequest, ResourceType, SharePointList, StatusCode
from common.services.graph_api_client import ListListsResponse


@pytest.fixture
def graph_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def activity(graph_client: MagicMock) -> ListListsActivity:
    return ListListsActivity(graph_client, ActivityErrorHandler())


class TestListListsActivity:
    def test_list_lists_success(
        self, activity: ListListsActivity, graph_client: MagicMock, sample_lists: list[SharePointList]
    ) -> None:
        graph_client.get_lists.return_value = ListListsResponse(lists=sample_lists)
        request = ListListsRequest(site_id="site-1", access_token="token-123")

        response = activity.list_lists(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.OK.value
        response_data = ListListsResponse.model_validate_json(response.body)
        assert len(response_data.lists) == 2

    def test_list_lists_not_found(self, activity: ListListsActivity, graph_client: MagicMock) -> None:
        graph_client.get_lists.side_effect = NotFoundError(ResourceType.SITE, "bad-site")
        request = ListListsRequest(site_id="bad-site", access_token="token-123")

        response = activity.list_lists(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.NOT_FOUND.value

    def test_list_lists_unauthorized(self, activity: ListListsActivity, graph_client: MagicMock) -> None:
        graph_client.get_lists.side_effect = UnauthorizedError()
        request = ListListsRequest(site_id="site-1", access_token="bad-token")

        response = activity.list_lists(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.UNAUTHORIZED.value

    def test_list_lists_graph_api_error(self, activity: ListListsActivity, graph_client: MagicMock) -> None:
        graph_client.get_lists.side_effect = GraphApiError(500, "Internal")
        request = ListListsRequest(site_id="site-1", access_token="token-123")

        response = activity.list_lists(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value

    def test_list_lists_unexpected_error(self, activity: ListListsActivity, graph_client: MagicMock) -> None:
        graph_client.get_lists.side_effect = RuntimeError("boom")
        request = ListListsRequest(site_id="site-1", access_token="token-123")

        response = activity.list_lists(request)

        assert isinstance(response, Response)
        assert response.status_code == StatusCode.INTERNAL_SERVER_ERROR.value
