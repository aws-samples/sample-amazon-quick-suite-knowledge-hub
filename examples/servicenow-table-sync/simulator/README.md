# ServiceNow simulator

The simulator answers the ServiceNow calls that the sync makes, so you can run the pipeline without a ServiceNow instance. It serves the OAuth token endpoint (`POST /oauth_token.do`), the Table API (`GET /api/now/table/{table_name}`), and a health check (`GET /health`). It runs as a FastAPI app on your computer and as an AWS Lambda function in the optional `ServiceNowIngestionTestStack`.

The seed data is deterministic and covers incidents, changes, problems, and configuration items. The test stack syncs only the `incident` table.

## Development

```bash
make install   # install dependencies with uv
make check     # lint, type-check, and tests
make run       # serve on 127.0.0.1:8080, or set PORT=<port>
```

`make run` binds to the loopback address, so only your computer can reach it.

To try the whole pipeline in AWS, deploy the test stack from the repository root. The command passes the `enableTestStack` option for you:

```bash
make -C cdk deploy-test
```

The test stack creates its own resources and keeps its schedule off, so start a sync by hand as the [runbook](../docs/DEPLOY_RUNBOOK.md#run-the-first-sync) describes.
