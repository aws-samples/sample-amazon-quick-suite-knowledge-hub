"""Shared Pydantic models used across multiple modules."""

from enum import Enum

from pydantic import BaseModel, Field


class StatusCode(Enum):
    OK = 200
    UNAUTHORIZED = 401
    NOT_FOUND = 404
    INTERNAL_SERVER_ERROR = 500


class ContentType(Enum):
    APPLICATION_JSON = "application/json"


class ResourceType(Enum):
    SITE = "Site"
    LIST = "List"


# --- Domain models ---


class SharePointSite(BaseModel):
    id: str
    name: str
    display_name: str = ""
    web_url: str = ""


class SharePointList(BaseModel):
    id: str
    name: str
    display_name: str = ""
    item_count: int = 0
    template: str = ""


class SharePointColumn(BaseModel):
    name: str
    display_name: str = ""


class SharePointListItem(BaseModel):
    id: str
    fields: dict = Field(default_factory=dict)


# --- Request models (shared between handler and activity/service) ---


class SearchSitesRequest(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    access_token: str


class ListListsRequest(BaseModel):
    site_id: str = Field(min_length=1)
    access_token: str


class GetListRequest(BaseModel):
    site_id: str = Field(min_length=1)
    list_id: str = Field(min_length=1)
    access_token: str


class GetColumnsRequest(BaseModel):
    site_id: str = Field(min_length=1)
    list_id: str = Field(min_length=1)
    access_token: str


class GetListItemsRequest(BaseModel):
    site_id: str = Field(min_length=1)
    list_id: str = Field(min_length=1)
    access_token: str


class ExportListRequest(BaseModel):
    site_id: str = Field(min_length=1)
    list_id: str = Field(min_length=1)
    access_token: str


# --- S3 DAO ---


class UploadContentRequest(BaseModel):
    key: str
    content: str
    content_type: str
