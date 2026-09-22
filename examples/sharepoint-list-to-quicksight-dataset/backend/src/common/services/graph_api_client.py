"""Microsoft Graph API client for SharePoint operations.

Uses the caller's delegated access token so results are scoped
to what the authenticated user can access.
"""

import httpx
from pydantic import BaseModel, Field

from common.exceptions import GraphApiError, NotFoundError
from common.models import (
    GetColumnsRequest,
    GetListItemsRequest,
    GetListRequest,
    ListListsRequest,
    ResourceType,
    SearchSitesRequest,
    SharePointColumn,
    SharePointList,
    SharePointListItem,
    SharePointSite,
)
from common.observability import logger, tracer


class SearchSitesResponse(BaseModel):
    sites: list[SharePointSite]


class ListListsResponse(BaseModel):
    lists: list[SharePointList]


class GetColumnsResponse(BaseModel):
    columns: list[SharePointColumn]


class GetListItemsResponse(BaseModel):
    items: list[SharePointListItem]


class GraphGetRequest(BaseModel):
    url: str
    access_token: str
    params: dict[str, str] = Field(default_factory=dict)


class GraphGetResponse(BaseModel):
    data: dict = Field(default_factory=dict)


class GraphApiClient:
    def __init__(self, base_url: str, timeout_seconds: int, max_items_per_page: int) -> None:
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._max_items_per_page = max_items_per_page

    def _get(self, request: GraphGetRequest) -> GraphGetResponse:
        is_absolute = request.url.startswith("http")
        url = request.url if is_absolute else f"{self._base_url}{request.url}"
        params = request.params if not is_absolute else {}

        logger.debug("Graph API GET", extra={"url": url})
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {request.access_token}"},
            params=params,
            timeout=self._timeout,
        )

        if response.status_code == 404:
            raise NotFoundError(ResourceType.SITE, url)
        if response.status_code >= 400:
            body = response.json() if "application/json" in response.headers.get("content-type", "") else {}
            message = body.get("error", {}).get("message", response.text)
            raise GraphApiError(response.status_code, message)

        return GraphGetResponse(data=response.json())

    def _paginate(self, first_request: GraphGetRequest) -> list[dict]:
        """Follow @odata.nextLink to collect all pages."""
        all_values: list[dict] = []
        current_request = first_request

        while True:
            result = self._get(current_request)
            all_values.extend(result.data.get("value", []))

            next_link = result.data.get("@odata.nextLink")
            if not next_link:
                break

            current_request = GraphGetRequest(url=next_link, access_token=first_request.access_token)

        return all_values

    @tracer.capture_method
    def search_sites(self, request: SearchSitesRequest) -> SearchSitesResponse:
        logger.info("Searching sites", extra={"query": request.query})
        first_request = GraphGetRequest(
            url="/sites", access_token=request.access_token, params={"search": request.query}
        )
        raw_sites = self._paginate(first_request)
        sites = [
            SharePointSite(
                id=s["id"],
                name=s.get("name", ""),
                display_name=s.get("displayName", ""),
                web_url=s.get("webUrl", ""),
            )
            for s in raw_sites
        ]
        return SearchSitesResponse(sites=sites)

    @tracer.capture_method
    def get_list(self, request: GetListRequest) -> SharePointList:
        """Fetch a single SharePoint list by ID."""
        logger.info("Getting list", extra={"site_id": request.site_id, "list_id": request.list_id})
        get_request = GraphGetRequest(
            url=f"/sites/{request.site_id}/lists/{request.list_id}", access_token=request.access_token
        )
        result = self._get(get_request)
        lst = result.data
        return SharePointList(
            id=lst["id"],
            name=lst.get("name", ""),
            display_name=lst.get("displayName", ""),
            item_count=lst.get("list", {}).get("contentTypesEnabled", 0),
            template=lst.get("list", {}).get("template", ""),
        )

    @tracer.capture_method
    def get_lists(self, request: ListListsRequest) -> ListListsResponse:
        logger.info("Listing lists", extra={"site_id": request.site_id})
        first_request = GraphGetRequest(url=f"/sites/{request.site_id}/lists", access_token=request.access_token)
        raw_lists = self._paginate(first_request)
        lists = [
            SharePointList(
                id=lst["id"],
                name=lst.get("name", ""),
                display_name=lst.get("displayName", ""),
                item_count=lst.get("list", {}).get("contentTypesEnabled", 0),
                template=lst.get("list", {}).get("template", ""),
            )
            for lst in raw_lists
        ]
        return ListListsResponse(lists=lists)

    @tracer.capture_method
    def get_columns(self, request: GetColumnsRequest) -> GetColumnsResponse:
        logger.info("Getting columns", extra={"site_id": request.site_id, "list_id": request.list_id})
        get_request = GraphGetRequest(
            url=f"/sites/{request.site_id}/lists/{request.list_id}/columns", access_token=request.access_token
        )
        result = self._get(get_request)
        columns = [
            SharePointColumn(name=col.get("name", ""), display_name=col.get("displayName", ""))
            for col in result.data.get("value", [])
            if not col.get("readOnly", False)
        ]
        return GetColumnsResponse(columns=columns)

    @tracer.capture_method
    def get_list_items(self, request: GetListItemsRequest) -> GetListItemsResponse:
        logger.info("Fetching list items", extra={"site_id": request.site_id, "list_id": request.list_id})
        first_request = GraphGetRequest(
            url=f"/sites/{request.site_id}/lists/{request.list_id}/items",
            access_token=request.access_token,
            params={"$expand": "fields", "$top": str(self._max_items_per_page)},
        )
        raw_items = self._paginate(first_request)
        items = [SharePointListItem(id=item.get("id", ""), fields=item.get("fields", {})) for item in raw_items]
        logger.info("Fetched list items", extra={"count": len(items)})
        return GetListItemsResponse(items=items)
