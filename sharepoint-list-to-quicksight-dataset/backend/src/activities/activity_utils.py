"""Shared activity error handling."""

from aws_lambda_powertools.event_handler import Response
from pydantic import BaseModel

from common.exceptions import GraphApiError, NotFoundError, UnauthorizedError
from common.models import ContentType, StatusCode
from common.observability import logger


class ErrorResponse(BaseModel):
    error: str
    error_code: str


class ActivityErrorHandler:
    """Maps known exceptions to HTTP error responses."""

    def handle(self, e: Exception) -> Response:
        """Map an exception to an appropriate HTTP response."""
        match e:
            case UnauthorizedError():
                logger.warning("Unauthorized", extra={"error": str(e)})
                return self._build_response(StatusCode.UNAUTHORIZED, str(e), "UNAUTHORIZED")
            case NotFoundError():
                logger.warning("Not found", extra={"error": str(e)})
                return self._build_response(StatusCode.NOT_FOUND, str(e), "NOT_FOUND")
            case GraphApiError() as graph_error:
                logger.warning("Graph API error", extra={"error": str(e), "status_code": graph_error.status_code})
                return self._build_response(StatusCode.INTERNAL_SERVER_ERROR, str(e), "GRAPH_API_ERROR")
            case _:
                logger.exception("Unexpected error", extra={"error_type": type(e).__name__})
                return self._build_response(
                    StatusCode.INTERNAL_SERVER_ERROR, "An internal error occurred", "INTERNAL_ERROR"
                )

    @staticmethod
    def _build_response(status_code: StatusCode, error: str, error_code: str) -> Response:
        return Response(
            status_code=status_code.value,
            content_type=ContentType.APPLICATION_JSON.value,
            body=ErrorResponse(error=error, error_code=error_code).model_dump_json(),
        )
