"""Exception types for SharePoint list export operations."""

from common.models import ResourceType


class SharePointExportError(Exception):
    pass


class NotFoundError(SharePointExportError):
    def __init__(self, resource_type: ResourceType, identifier: str) -> None:
        self.resource_type = resource_type
        self.identifier = identifier
        super().__init__(f"{resource_type.value} '{identifier}' not found")


class GraphApiError(SharePointExportError):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Graph API error ({status_code}): {message}")


class UnauthorizedError(SharePointExportError):
    def __init__(self, message: str = "Missing or invalid access token") -> None:
        super().__init__(message)
