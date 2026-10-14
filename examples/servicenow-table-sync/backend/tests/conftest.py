"""Shared test configuration.

Sets the environment variables the fail-fast Config requires at import. Values are synthetic.
"""

import os

os.environ.setdefault("INGESTION_CHECKPOINT_TABLE_NAME", "TestIngestionCheckpoint")
os.environ.setdefault("TABLE_METADATA_TABLE_NAME", "TestTableMetadata")
os.environ.setdefault("RAW_DATA_LAKE_BUCKET_NAME", "test-raw-data-lake")
os.environ.setdefault(
    "SERVICE_NOW_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111122223333:secret:test"
)
os.environ.setdefault("AWS_REGION", "us-east-1")
