# CDK

This folder holds the AWS Cloud Development Kit (AWS CDK) app that deploys the ServiceNow table sync. By default the app creates `ServiceNowIngestionStack`. The optional `ServiceNowIngestionTestStack` runs the same pipeline against a ServiceNow simulator with synthetic data, and the app includes it only when you pass `--context enableTestStack=true`.

To change which tables sync, edit the `TABLE_REGISTRY` list in `lib/common/table-registry-config.ts` and deploy again. Deployment writes the list to Amazon DynamoDB, where the workflow reads it at the start of each run.

## Commands

Run these from this folder, or from the repository root with `make`.

```bash
make install       # install dependencies
make check         # type-check, lint, and run the tests
make deploy        # deploy ServiceNowIngestionStack
make deploy-test   # deploy the optional test stack
```

The stack imports an AWS Secrets Manager secret named `ServiceNowIngestionServiceNowCredentials`, so create the secret before you run `make deploy`. The [runbook](../docs/DEPLOY_RUNBOOK.md) covers the secret, the first deploy, and cleanup, and the [README](../README.md#deployment-options) lists the context options.

## Layout

| Path | Contents |
| --- | --- |
| `lib/app.ts` | Reads the context options and creates the stacks. |
| `lib/stacks/` | The sync stack and the optional test stack. |
| `lib/construct-groups/` | A construct group for each part of the pipeline. |
| `lib/common/` | Names, context parsing, the table list, and cdk-nag suppressions. |
| `test/` | Template assertions and cdk-nag checks. |
