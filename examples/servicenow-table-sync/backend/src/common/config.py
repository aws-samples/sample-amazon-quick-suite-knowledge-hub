"""Service configuration, validated at import.

Every runtime value is a typed field parsed from the environment at import, so a missing or
malformed value raises `pydantic.ValidationError` at cold start rather than mid-run. Import
`config` from here; raw `os.environ` access is forbidden elsewhere. The CDK injects these
variables into the Lambda environment.

Values only some handlers need are optional on the shared Config so every handler still
constructs at import; the Glue entry point calls `require_s3_tables_config()` for its
optional values.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.errors import ConfigurationError


class LogLevel(StrEnum):
    """Severity levels accepted by the Powertools logger."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class S3TablesTarget(BaseModel):
    """The S3 Tables (Iceberg) merge target: the table bucket ARN and its namespace."""

    model_config = ConfigDict(frozen=True)

    bucket_arn: str = Field(min_length=1)
    namespace: str = Field(min_length=1)


class Config(BaseSettings):
    """Typed, fail-fast configuration sourced from unprefixed environment variables."""

    model_config = SettingsConfigDict(frozen=True)

    # State tables (owned by the CDK state-tables construct group).
    ingestion_checkpoint_table_name: str = Field(min_length=1)
    table_metadata_table_name: str = Field(min_length=1)

    # Raw data lake bucket containing staged Parquet.
    raw_data_lake_bucket_name: str = Field(min_length=1)

    # ServiceNow OAuth client-credentials secret: instance URL, client ID, client secret,
    # and grant type.
    service_now_secret_arn: str = Field(min_length=1)

    # S3 Tables (Iceberg) merge target: the bucket ARN and the namespace the per-table Iceberg
    # tables live under. Optional on the shared Config so Lambda handlers that do not
    # use S3 Tables can still initialize; the Glue entry point requires both values.
    s3_tables_bucket_arn: str | None = None
    s3_tables_namespace: str | None = None

    aws_region: str = Field(min_length=1)
    log_level: LogLevel = LogLevel.INFO

    def require_s3_tables_config(self) -> S3TablesTarget:
        """Return the S3 Tables merge target, raising if unset (normalizer only)."""
        if not self.s3_tables_bucket_arn or not self.s3_tables_namespace:
            raise ConfigurationError(
                "The normalizer requires S3_TABLES_BUCKET_ARN and S3_TABLES_NAMESPACE."
            )
        return S3TablesTarget(
            bucket_arn=self.s3_tables_bucket_arn, namespace=self.s3_tables_namespace
        )


config = Config()  # type: ignore[call-arg]  # values are supplied by the environment
