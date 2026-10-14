"""Tests for the fail-fast configuration."""

import pytest

from common.config import Config, S3TablesTarget
from common.errors import ConfigurationError

_ENV = {
    "ingestion_checkpoint_table_name": "c",
    "table_metadata_table_name": "m",
    "raw_data_lake_bucket_name": "b",
    "service_now_secret_arn": "arn:secret",
    "aws_region": "us-east-1",
}


def test_valid_config_constructs() -> None:
    config = Config.model_validate(_ENV)
    assert config.service_now_secret_arn == "arn:secret"
    assert config.log_level == "INFO"


def test_invalid_value_raises() -> None:
    # An empty required string violates the field's min_length=1 constraint.
    with pytest.raises(ValueError):
        Config.model_validate({**_ENV, "raw_data_lake_bucket_name": ""})


def test_require_s3_tables_config_raises_when_unset() -> None:
    config = Config.model_validate(_ENV)
    with pytest.raises(ConfigurationError):
        config.require_s3_tables_config()


def test_require_s3_tables_config_returns_target_when_set() -> None:
    config = Config.model_validate(
        {
            **_ENV,
            "s3_tables_bucket_arn": "arn:aws:s3tables:...:bucket/x",
            "s3_tables_namespace": "servicenow",
        }
    )
    target = config.require_s3_tables_config()
    assert isinstance(target, S3TablesTarget)
    assert target.namespace == "servicenow"
