"""Extracts the inbound token and exchanges it for a Graph API token."""

from pydantic import BaseModel

from common.exceptions import UnauthorizedError
from common.services.obo_token_exchanger import OboTokenExchanger, OboTokenRequest


class TokenExtractorRequest(BaseModel):
    """Request containing the Authorization header from the API Gateway event."""

    request_headers: dict[str, str]


class TokenExtractorResponse(BaseModel):
    """Response containing the Graph API access token."""

    access_token: str


class TokenExtractor:
    """Extracts the inbound bearer token and exchanges it for a Graph token via OBO."""

    def __init__(self, obo_exchanger: OboTokenExchanger) -> None:
        self._obo_exchanger = obo_exchanger

    def extract(self, request: TokenExtractorRequest) -> TokenExtractorResponse:
        """Extract the bearer token from headers and exchange it via OBO."""
        auth_header = request.request_headers.get("authorization", request.request_headers.get("Authorization", ""))
        if not auth_header:
            raise UnauthorizedError()
        inbound_token = auth_header[7:] if auth_header.startswith("Bearer ") else auth_header
        obo_response = self._obo_exchanger.exchange(OboTokenRequest(assertion=inbound_token))
        return TokenExtractorResponse(access_token=obo_response.access_token)
