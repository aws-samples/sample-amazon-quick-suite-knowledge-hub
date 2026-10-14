---
category: Use Case
description: "Sync ServiceNow tables into Amazon S3 Tables on a schedule so Amazon Quick can query them with Direct Query. You choose the tables in an AWS CDK configuration file"
---

# Sync ServiceNow Tables to Amazon Quick Sight Using OAuth

To report on ServiceNow incidents or change requests in [Amazon Quick](https://aws.amazon.com/quick/), the records have to be in tables that Amazon Quick can query. You list the ServiceNow tables you want in a configuration file and deploy the stack. On a schedule, a workflow then copies the records that changed since its previous run into Apache Iceberg tables in an [Amazon S3 table bucket](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables.html). Amazon Quick reads those tables with Direct Query, which runs each query against the table when someone opens a visual instead of importing a copy, so dashboards, and natural-language questions that you ask with Dataset Q&A, are as fresh as the last completed run.

An [AWS Step Functions](https://aws.amazon.com/step-functions/) workflow coordinates the sync. [AWS Lambda](https://aws.amazon.com/lambda/) functions read the records from ServiceNow and stage them in [Amazon Simple Storage Service (Amazon S3)](https://aws.amazon.com/s3/) as Parquet files, and an [AWS Glue](https://aws.amazon.com/glue/) job merges the staged files into the tables, applying updates and deletions. [docs/DESIGN.md](docs/DESIGN.md) explains how the sync works, and [docs/DEPLOY_RUNBOOK.md](docs/DEPLOY_RUNBOOK.md) covers the full setup, from the ServiceNow OAuth application to the Amazon Quick dataset.

## Prerequisites

The deployment spans AWS, Amazon Quick, and ServiceNow, so you need access to each system, plus a few tools on a macOS or Linux computer.

- An AWS account and an [AWS Identity and Access Management (IAM)](https://aws.amazon.com/iam/) identity that can deploy [AWS CloudFormation](https://aws.amazon.com/cloudformation/) stacks, including the IAM roles they create.
- Version 2 of the [AWS Command Line Interface (AWS CLI)](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), recent enough to include the `s3tables` commands, and configured with credentials for that identity.
- An AWS Region that [supports S3 Tables](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-tables-regions-quotas.html) and is the Region of your Amazon Quick account. The Amazon Quick **AWS Resources** page lists only the table buckets in its current Region.
- An account and Region that are [bootstrapped for the AWS Cloud Development Kit (AWS CDK)](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping-env.html). The runbook has the command.
- An Amazon Quick Enterprise subscription and a user who can open **Manage account**.
- A ServiceNow instance on the Washington DC release or later. That release added the OAuth client credentials grant that the sync uses.
- A ServiceNow user with the `admin` role who can also switch to the `security_admin` role, which ServiceNow requires to create access control rules.
- [Node.js](https://nodejs.org/) 22 and npm. The repository pins the version in `.nvmrc`, so `nvm use` selects it.
- [Python](https://www.python.org/) 3.12 and [uv](https://docs.astral.sh/uv/).
- Docker or [Finch](https://github.com/runfinch/finch) to build the Lambda container image. The functions run on ARM64, so the stack builds a `linux/arm64` image, and on an x86 machine your container tool must support emulated builds. To use Finch, set `CDK_DOCKER` as [Build and deploy container image assets in CDK apps](https://docs.aws.amazon.com/cdk/v2/guide/build-containers.html) describes.
- `make`, which runs the project's checks and deploy commands.

The AWS CDK command line is a development dependency of the `cdk` folder, so you don't install it separately.

## Deploy

Set up ServiceNow first, because the stack reads credentials that you create there.

1. Follow [Set up ServiceNow](docs/DEPLOY_RUNBOOK.md#set-up-servicenow) to turn on the client credentials grant, create an integration user with read access to your tables, and create the OAuth application.

2. Follow [Create the AWS secret](docs/DEPLOY_RUNBOOK.md#create-the-aws-secret) to store the OAuth client ID and secret in [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/) under the name `ServiceNowIngestionServiceNowCredentials`. The stack reads this secret but never creates it, so it must exist before you deploy.

3. Review the table list in [`cdk/lib/common/table-registry-config.ts`](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/examples/servicenow-table-sync/cdk/lib/common/table-registry-config.ts). The defaults work as they are.

4. Install the dependencies and deploy:

    ```bash
    nvm use
    make install
    make deploy
    ```

5. Follow [Run the first sync](docs/DEPLOY_RUNBOOK.md#run-the-first-sync) and [Set up Amazon Quick](docs/DEPLOY_RUNBOOK.md#set-up-amazon-quick) to start the first sync and connect Amazon Quick to the table bucket.

After the first run, the schedule starts each later sync, and your ongoing work is the table list and the ServiceNow credentials.

## Choose the tables to sync

The table list is a constant in the CDK project, so a table change is a code change that deploys with the stack. The defaults are the standard ServiceNow tables for IT Service Management, the Service Catalog, and the configuration management database (CMDB):

```typescript
export const TABLE_REGISTRY: readonly TableMetadataConfig[] = [
  syncTable("incident"),
  syncTable("change_request"),
  syncTable("problem"),
  syncTable("sc_request"),
  syncTable("sc_req_item"),
  syncTable("task_sla"),
  syncTable("cmdb_ci_server"),
  syncTable("cmdb_ci_appl"),
];
```

To add a table, add a line and run `make deploy` again, and to stop syncing one, remove its line. Each entry also accepts optional settings:

```typescript
syncTable("incident", {
  fields: "number,short_description,priority,state,sys_updated_on", // empty means every field
  baseQuery: "active=true", // a ServiceNow encoded query
}),
```

Give the integration user read access to every table you add, as the runbook describes. Deployment writes the list to an [Amazon DynamoDB](https://aws.amazon.com/dynamodb/) table, which the workflow reads at the start of each run.

## What you get in Amazon Quick

Each ServiceNow table becomes an Apache Iceberg table in the `servicenow` namespace of the table bucket, named after the ServiceNow table, and every table follows the same rules:

- Every column is a string. You change types such as dates and numbers when you prepare the dataset in Amazon Quick.
- Reference and choice fields hold ServiceNow display values, such as a person's name instead of a `sys_id`.
- Each row is keyed by `sys_id`, so an update replaces the existing row and a deletion removes it.

## Deployment options

Pass these with `CDK_ARGS`, for example `make deploy CDK_ARGS="--context enableSchedule=false"`.

| Context key | Values | Default | Effect |
| --- | --- | --- | --- |
| `enableSchedule` | `true`, `false` | `true` | Runs the sync on the interval that `SYNC_INTERVAL_HOURS` sets in `cdk/lib/common/config.ts`. Set `false` to run it only when you start an execution. |
| `costCenter` | any string | none | Adds a `cost-center` tag to the stack. |
| `enableTestStack` | `true`, `false` | `false` | Adds an optional test stack that syncs from a ServiceNow simulator. Deploy it with `make -C cdk deploy-test`. |

## Project layout

| Path | Contents |
| --- | --- |
| `cdk/` | The AWS CDK app in TypeScript. |
| `backend/` | The Python Lambda handlers and the AWS Glue job. |
| `simulator/` | A ServiceNow API simulator for the optional test stack. |
| `docs/DEPLOY_RUNBOOK.md` | ServiceNow, AWS, and Amazon Quick setup, troubleshooting, and cleanup. |
| `docs/DESIGN.md` | How the sync works and why. |

## Security

The stack encrypts data at rest with service-managed keys and creates no [AWS Key Management Service (AWS KMS)](https://aws.amazon.com/kms/) keys. [Storage and encryption](docs/DESIGN.md#storage-and-encryption) lists the encryption each service uses.

The OAuth credentials live in Secrets Manager, and the stack grants read access to that secret only. You are responsible for who can use those credentials in ServiceNow and for who can open the datasets in Amazon Quick. ServiceNow access rules aren't carried into Amazon Quick, so share a dataset only with people who are allowed to read that data in ServiceNow.

## Cost

You pay for the AWS services in the stack and for Amazon Quick. The sync cost depends on how many records change and how often the workflow runs, and the Glue job starts only for a table that has new records or deletions. See [Amazon Quick pricing](https://aws.amazon.com/quick/pricing/) for the Amazon Quick side.

## Clean up resources

S3 Tables doesn't delete a table bucket that still holds tables, so delete the tables first and then destroy the stack. The runbook has the commands in [Clean up resources](docs/DEPLOY_RUNBOOK.md#clean-up-resources).

## License

This library is licensed under the MIT-0 License. See the repository [LICENSE](https://github.com/aws-samples/sample-amazon-quick-suite-knowledge-hub/blob/main/LICENSE).
