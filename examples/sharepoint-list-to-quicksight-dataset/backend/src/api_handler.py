"""API Gateway Lambda handler for SharePoint list export.

Uses Powertools API Gateway REST resolver for routing. The Entra access
token from the Authorization header is exchanged for a Graph API token
via the On-Behalf-Of flow before calling activities.
"""

import boto3
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver
from aws_lambda_powertools.utilities.typing import LambdaContext

from activities.activity_utils import ActivityErrorHandler
from activities.export_list_activity import ExportListActivity
from activities.list_lists_activity import ListListsActivity
from activities.search_sites_activity import SearchSitesActivity
from common.dao.s3_dao import S3Dao
from common.env import (
    ENTRA_CLIENT_ID,
    ENTRA_CLIENT_SECRET_ARN,
    ENTRA_TOKEN_URL,
    EXPORT_BUCKET,
    GRAPH_API_BASE_URL,
    GRAPH_API_TIMEOUT_SECONDS,
    MAX_LIST_ITEMS_PER_PAGE,
)
from common.models import ExportListRequest, ListListsRequest, SearchSitesRequest
from common.observability import logger, metrics, tracer
from common.services.graph_api_client import GraphApiClient
from common.services.obo_token_exchanger import OboTokenExchanger
from common.services.token_extractor import TokenExtractor, TokenExtractorRequest

secrets_client = boto3.client("secretsmanager")
entra_client_secret = secrets_client.get_secret_value(SecretId=ENTRA_CLIENT_SECRET_ARN)["SecretString"]

obo_exchanger = OboTokenExchanger(
    token_url=ENTRA_TOKEN_URL,
    client_id=ENTRA_CLIENT_ID,
    client_secret=entra_client_secret,
)
token_extractor = TokenExtractor(obo_exchanger)
graph_client = GraphApiClient(
    base_url=GRAPH_API_BASE_URL,
    timeout_seconds=GRAPH_API_TIMEOUT_SECONDS,
    max_items_per_page=MAX_LIST_ITEMS_PER_PAGE,
)
s3_dao = S3Dao(bucket_name=EXPORT_BUCKET)

error_handler = ActivityErrorHandler()
search_sites_activity = SearchSitesActivity(graph_client, error_handler)
list_lists_activity = ListListsActivity(graph_client, error_handler)
export_list_activity = ExportListActivity(graph_client, s3_dao, error_handler)

app = APIGatewayHttpResolver()


def _get_graph_token() -> str:
    """Extract and exchange the bearer token from the current request."""
    auth_header = app.current_event.headers.get("Authorization", "")
    return token_extractor.extract(TokenExtractorRequest(request_headers={"authorization": auth_header})).access_token


@app.get("/sites")
@tracer.capture_method
def search_sites() -> dict:
    """Search SharePoint sites."""
    query = app.current_event.get_query_string_value("query", default_value="")
    token = _get_graph_token()
    return search_sites_activity.search(SearchSitesRequest(query=query, access_token=token))


@app.get("/sites/<site_id>/lists")
@tracer.capture_method
def list_lists(site_id: str) -> dict:
    """List all lists in a SharePoint site."""
    token = _get_graph_token()
    return list_lists_activity.list_lists(ListListsRequest(site_id=site_id, access_token=token))


@app.post("/sites/<site_id>/lists/<list_id>/export")
@tracer.capture_method
def export_list(site_id: str, list_id: str) -> dict:
    """Export a SharePoint list to CSV in S3."""
    token = _get_graph_token()
    return export_list_activity.export(ExportListRequest(site_id=site_id, list_id=list_id, access_token=token))


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, context: LambdaContext) -> dict:
    """Lambda entry point."""
    return app.resolve(event, context)
