"""Activity for listing SharePoint lists in a site."""

from aws_lambda_powertools.event_handler import Response

from activities.activity_utils import ActivityErrorHandler
from common.models import ContentType, ListListsRequest, StatusCode
from common.observability import logger, tracer
from common.services.graph_api_client import GraphApiClient


class ListListsActivity:
    """Lists all SharePoint lists in a site via Microsoft Graph."""

    def __init__(self, graph_client: GraphApiClient, error_handler: ActivityErrorHandler) -> None:
        self._graph_client = graph_client
        self._error_handler = error_handler

    @tracer.capture_method
    def list_lists(self, request: ListListsRequest) -> Response:
        """Get all lists in a SharePoint site the user can access."""
        try:
            logger.info("Listing lists", extra={"site_id": request.site_id})
            response_data = self._graph_client.get_lists(request)
            return Response(
                status_code=StatusCode.OK.value,
                content_type=ContentType.APPLICATION_JSON.value,
                body=response_data.model_dump_json(),
            )
        except Exception as e:
            return self._error_handler.handle(e)
