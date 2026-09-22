"""Shared test fixtures."""

import pytest

from common.models import (
    SharePointColumn,
    SharePointList,
    SharePointListItem,
    SharePointSite,
)


@pytest.fixture
def access_token() -> str:
    return "test-access-token-abc123"


@pytest.fixture
def site_id() -> str:
    return "contoso.sharepoint.com,abc-123,def-456"


@pytest.fixture
def list_id() -> str:
    return "list-789"


@pytest.fixture
def sample_sites() -> list[SharePointSite]:
    return [
        SharePointSite(
            id="site-1", name="TeamSite", display_name="Team Site", web_url="https://contoso.sharepoint.com/sites/team"
        ),
        SharePointSite(
            id="site-2",
            name="ProjectSite",
            display_name="Project Site",
            web_url="https://contoso.sharepoint.com/sites/project",
        ),
    ]


@pytest.fixture
def sample_lists() -> list[SharePointList]:
    return [
        SharePointList(id="list-1", name="Tasks", display_name="Tasks", item_count=10, template="genericList"),
        SharePointList(
            id="list-2", name="Documents", display_name="Documents", item_count=5, template="documentLibrary"
        ),
    ]


@pytest.fixture
def sample_columns() -> list[SharePointColumn]:
    return [
        SharePointColumn(name="Title", display_name="Title"),
        SharePointColumn(name="Status", display_name="Status"),
        SharePointColumn(name="AssignedTo", display_name="Assigned To"),
    ]


@pytest.fixture
def sample_items() -> list[SharePointListItem]:
    return [
        SharePointListItem(id="1", fields={"Title": "Task A", "Status": "Active", "AssignedTo": "Alice"}),
        SharePointListItem(id="2", fields={"Title": "Task B", "Status": "Done", "AssignedTo": "Bob"}),
        SharePointListItem(id="3", fields={"Title": "Task C", "Status": "Active", "AssignedTo": "Charlie"}),
    ]
