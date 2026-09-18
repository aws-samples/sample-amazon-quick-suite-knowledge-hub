"""Activity for searching SharePoint sites."""

from aws_lambda_powertools.event_handler import Response

from activities.activity_utils import ActivityErrorHandler
from common.models import ContentType, SearchSitesRequest, StatusCode
from common.observability import logger, tracer
from common.services.graph_api_client import GraphApiClient


class SearchSitesActivity:
    """Searches SharePoint sites via Microsoft Graph on behalf of the user."""

    def __init__(self, graph_client: GraphApiClient, error_handler: ActivityErrorHandler) -> None:
        self._graph_client = graph_client
        self._error_handler = error_handler

    @tracer.capture_method
    def search(self, request: SearchSitesRequest) -> Response:
        """Search SharePoint sites the user has access to."""
        try:
            logger.info("Searching sites", extra={"query": request.query})
            response_data = self._graph_client.search_sites(request)
            return Response(
                status_code=StatusCode.OK.value,
                content_type=ContentType.APPLICATION_JSON.value,
                body=response_data.model_dump_json(),
            )
        except Exception as e:
            return self._error_handler.handle(e)
