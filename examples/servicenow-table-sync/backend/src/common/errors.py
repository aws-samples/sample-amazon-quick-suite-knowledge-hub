"""Domain exceptions for the ingestion pipeline.

The data-access and service layers raise these typed errors rather than leaking raw
`botocore`/`urllib` exceptions to callers. A processing error a Step Functions Task should
surface (so the Catch routes it to the failure state) is an `ExtractionError`; a genuinely
absent item is a `NotFoundError`. No message here embeds internal details that would reach an
external consumer.
"""


class IngestionError(Exception):
    """Base class for every error the ingestion pipeline raises."""


class NotFoundError(IngestionError):
    """An item was requested by key but does not exist."""

    def __init__(self, entity: str, key: str) -> None:
        super().__init__(f"{entity} not found: {key}")
        self.entity = entity
        self.key = key


class ConfigurationError(IngestionError):
    """A handler-specific configuration value required at its composition root is absent."""


class ServiceNowAuthError(IngestionError):
    """The ServiceNow OAuth token grant failed or returned no access token."""


class ServiceNowRequestError(IngestionError):
    """A ServiceNow REST call failed after the retry budget was exhausted."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ExtractionError(IngestionError):
    """A structured table extraction could not complete.

    Raising this error leaves the checkpoint at the last successful value and routes the
    workflow to its failure state.
    """

    def __init__(self, table_name: str, reason: str) -> None:
        super().__init__(f"extraction failed for {table_name}: {reason}")
        self.table_name = table_name
        self.reason = reason
