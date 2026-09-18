"""On-Behalf-Of token exchanger for Microsoft Entra ID.

Exchanges an inbound user token (scoped to this app) for a Microsoft Graph
token using the OAuth 2.0 On-Behalf-Of flow, preserving the user's identity
and delegated permissions.
"""

import httpx
from pydantic import BaseModel, Field

from common.exceptions import GraphApiError
from common.observability import logger, tracer


class OboTokenRequest(BaseModel):
    """Request to exchange an inbound token for a Graph API token."""

    assertion: str = Field(description="The inbound JWT from the caller")
    scope: str = Field(default="https://graph.microsoft.com/.default")


class OboTokenResponse(BaseModel):
    """Response containing the exchanged Graph API token."""

    access_token: str


class OboTokenExchanger:
    """Exchanges inbound Entra tokens for Graph tokens via the OBO flow."""

    def __init__(self, token_url: str, client_id: str, client_secret: str) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret

    @tracer.capture_method
    def exchange(self, request: OboTokenRequest) -> OboTokenResponse:
        """Exchange an inbound token for a Graph API token."""
        logger.debug("Exchanging token via OBO flow")

        response = httpx.post(
            self._token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "assertion": request.assertion,
                "scope": request.scope,
                "requested_token_use": "on_behalf_of",
            },
            timeout=10,
        )

        if response.status_code != 200:
            body = response.json() if "application/json" in response.headers.get("content-type", "") else {}
            error_desc = body.get("error_description", response.text)
            logger.error("OBO token exchange failed", extra={"status": response.status_code, "error": error_desc})
            raise GraphApiError(response.status_code, f"OBO token exchange failed: {error_desc}")

        data = response.json()
        return OboTokenResponse(access_token=data["access_token"])
