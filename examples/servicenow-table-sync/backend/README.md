# Backend

This folder holds the code that runs during a sync: AWS Lambda handlers that read from ServiceNow, and an AWS Glue job that writes Apache Iceberg tables to Amazon S3 Tables. AWS Step Functions calls them in order. The handlers in `src/` share a container image and differ only in their command.

- `list_tables_handler.py` reads the table list from Amazon DynamoDB. The CDK app writes that list from [`table-registry-config.ts`](../cdk/lib/common/table-registry-config.ts).
- `extract_table_handler.py` reads new and changed records and writes them to Amazon S3 as Parquet.
- `extract_deletes_handler.py` reads the ServiceNow delete audit records.
- `glue/normalize_content_job.py` merges the staged records into the tables and applies deletions.

ServiceNow authentication uses the OAuth client credentials flow in [PySNC](https://servicenow.github.io/PySNC/). The credentials come from the AWS Secrets Manager secret that the [runbook](../docs/DEPLOY_RUNBOOK.md#create-the-aws-secret) describes.

## Development

```bash
make install   # install dependencies with uv
make check     # lint, format check, type-check, and unit tests
make test      # tests with coverage
make build     # build the Lambda container image with Finch
```

Set `CONTAINER_CLI=docker` to build with Docker instead.
