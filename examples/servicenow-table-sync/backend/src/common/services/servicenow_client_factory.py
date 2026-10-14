"""Build an OAuth-authenticated ServiceNow client from Secrets Manager."""

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field
from pysnc import ServiceNowClient
from pysnc.auth import ServiceNowClientCredentialsFlow

from common.errors import ServiceNowAuthError

if TYPE_CHECKING:
    from mypy_boto3_secretsmanager import SecretsManagerClient


class ServiceNowCredentials(BaseModel):
    """Structured extractor credentials stored in Secrets Manager."""

    model_config = ConfigDict(extra="forbid")

    instance_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    grant_type: Literal["client_credentials"] = "client_credentials"


class ServiceNowClientFactory:
    """Read the credentials secret and create a PySNC client."""

    def __init__(self, secrets_client: "SecretsManagerClient", secret_arn: str) -> None:
        self._secrets_client = secrets_client
        self._secret_arn = secret_arn

    def create(self) -> ServiceNowClient:
        """Create a client that obtains and refreshes OAuth access tokens."""
        credentials = self._fetch_credentials()
        auth = ServiceNowClientCredentialsFlow(
            credentials.client_id,
            credentials.client_secret,
        )
        return ServiceNowClient(credentials.instance_url, auth)

    def _fetch_credentials(self) -> ServiceNowCredentials:
        raw = self._secrets_client.get_secret_value(SecretId=self._secret_arn).get("SecretString")
        if not raw:
            raise ServiceNowAuthError("ServiceNow secret has no SecretString value.")
        try:
            return ServiceNowCredentials.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as error:
            raise ServiceNowAuthError("ServiceNow secret is malformed.") from error
